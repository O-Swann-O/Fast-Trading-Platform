import math
import logging

import config
from contractRegistry import ContractRegistry
from dataFeeder import DataFeeder
from orderManager import OrderManager
from riskGate import RiskGate
from signalSampler import SignalSampler

log = logging.getLogger(__name__)


class TradingCore:

    def __init__(self, ib, clock, source, session, state) -> None:
        self.clock    = clock
        self.state    = state
        self.session  = session
        self.registry = ContractRegistry(ib)
        self.feeder   = DataFeeder(ib)
        self.gate     = RiskGate(
            state               = state,
            session             = session,
            killSwitchFile      = config.killSwitchFile,
            maxOrderNotional    = config.maxOrderNotional,
            maxPositionNotional = config.maxPositionNotional,
            minFreeMargin       = config.minFreeMargin,
            maxTickJump         = config.maxTickJump,
        )
        self.orders   = OrderManager(ib, self.gate)
        self.sampler  = None
        self.ticks    = {}
        self._source  = source

        self.feeder.onTick      = self._onTick
        self.orders.onAccepted  = state.onAccepted
        self.orders.onFill      = state.onFill
        self.orders.onPartial   = state.onPartial
        self.orders.onCancelled = self._onCancelled
        self.orders.onRejected  = self._onRejected

    async def setup(self, universe) -> bool:
        for contract in universe:
            conId = await self.registry.register(contract)
            if conId:
                qualified = self.registry.getById(conId)
                self.state.registerInstrument(conId, qualified.symbol, qualified.currency)

        conIds = [c.conId for c in self.registry.getAll()]
        if not conIds:
            log.error("No contracts qualified. Core setup failed.")
            return False

        self.sampler = SignalSampler(
            source         = self._source,
            clock          = self.clock,
            conIds         = conIds,
            sampleInterval = config.sampleInterval,
            staleLimit     = config.staleLimit,
        )
        self.sampler.onTargetPosition = self._onTargetPosition
        return True

    def start(self) -> None:
        self.feeder.start()
        for contract in self.registry.getAll():
            self.feeder.subscribe(contract.conId, contract)
        self.orders.start()

    def stop(self) -> None:
        self.orders.stop()
        self.feeder.stop()

    async def cancelAll(self) -> None:
        await self.orders.cancelAll()

    def _onTick(self, contractId, ticker) -> None:
        price = ticker.marketPrice()
        if math.isnan(price):
            return
        if not self.gate.validateTick(contractId, price):
            return
        self.ticks[contractId] = ticker
        self.state.onPrice(contractId, price)
        if self.sampler:
            self.sampler.onTick(contractId, price)

    def _onTargetPosition(self, conId, targetPos, confidence, timestamp) -> None:
        age = self.clock.timestamp() - timestamp
        if age > config.maxSignalAge:
            log.warning("Signal rejected: Contract %s signal is %ds old (limit %ds).",
                        conId, age, config.maxSignalAge)
            return

        assumed = (self.state.inventory.get(conId, 0)
                   + self.state.pending_inventory.get(conId, 0))
        delta = targetPos - assumed
        if delta == 0:
            return

        contract = self.registry.getById(conId)
        if not contract:
            log.error("Signal rejected: Unknown contract ID %s", conId)
            return

        action = "BUY" if delta > 0 else "SELL"
        qty    = abs(delta)
        ticker = self.ticks.get(conId)
        estPrice = 0.0
        if ticker is not None:
            px = ticker.marketPrice()
            if not math.isnan(px):
                estPrice = px

        log.info("Signal: %s | Target: %d | Action: %s %d (Alpha: %.2f)",
                 conId, targetPos, action, qty, confidence)
        self.orders.submitMarket(conId, contract, action, qty, estPrice)

    def _onCancelled(self, contractId, orderId, action, qty, estPrice) -> None:
        log.warning("Order cancelled — contract %s order %s", contractId, orderId)
        self.state.onCancelled(contractId, action, qty, estPrice)

    def _onRejected(self, contractId, orderId, action, qty, estPrice) -> None:
        log.error("Order rejected — contract %s order %s", contractId, orderId)
        self.state.onRejected(contractId, action, qty, estPrice)
