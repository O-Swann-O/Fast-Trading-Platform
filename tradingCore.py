import math
import logging

import config
import logSetup
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
        self._priced  = False

        self.feeder.onTick      = self._onTick
        self.orders.onAccepted  = self._onAccepted
        self.orders.onFill      = self._onFill
        self.orders.onPartial   = self._onPartial
        self.orders.onCancelled = self._onCancelled
        self.orders.onRejected  = self._onRejected

    async def setup(self, universe) -> bool:
        for contract in universe:
            conId = await self.registry.register(contract)
            if conId:
                qualified = self.registry.getById(conId)
                self.state.registerInstrument(conId, qualified.symbol, qualified.currency)
                logSetup.register(conId, f"{qualified.symbol}{qualified.currency}")

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
        log.info("Core ready: %d instruments, sampling every %.1fs, stale after %.1fs, "
                 "order cap %s, position cap %s, margin floor %s",
                 len(conIds), config.sampleInterval, config.staleLimit,
                 f"{config.maxOrderNotional:,.0f}", f"{config.maxPositionNotional:,.0f}",
                 f"{config.minFreeMargin:,.0f}")
        return True

    def start(self) -> None:
        self.feeder.start()
        for contract in self.registry.getAll():
            self.feeder.subscribe(contract.conId, contract)
        self.orders.start()
        log.info("Market data subscribed for %d instruments, order tracking active.",
                 len(self.registry.getAll()))

    def stop(self) -> None:
        self.orders.stop()
        self.feeder.stop()
        self._priced = False
        log.info("Market data unsubscribed, order tracking stopped.")

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

        if not self._priced and not self.state.unpricedCurrencies():
            self._priced = True
            log.info("Book priced: %s", self.summary())

        if self.sampler:
            self.sampler.onTick(contractId, price)

    def _onTargetPosition(self, conId, targetPos, confidence, timestamp) -> None:
        age = self.clock.timestamp() - timestamp
        if age > config.maxSignalAge:
            log.warning("Signal %s rejected: %ds old (limit %ds)",
                        logSetup.name(conId), age, config.maxSignalAge)
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

        log.info("Signal %s: target %d -> %s %d (alpha %.2f)",
                 logSetup.name(conId), targetPos, action, qty, confidence)
        self.orders.submitMarket(conId, contract, action, qty, estPrice)

    def _onCancelled(self, contractId, orderId, action, qty, estPrice) -> None:
        log.warning("Cancelled %s order %s (%s %d)", logSetup.name(contractId), orderId, action, qty)
        self.state.onCancelled(contractId, action, qty, estPrice)

    def _onRejected(self, contractId, orderId, action, qty, estPrice) -> None:
        log.error("Rejected %s order %s (%s %d)", logSetup.name(contractId), orderId, action, qty)
        self.state.onRejected(contractId, action, qty, estPrice)

    def _onAccepted(self, conId, action, qty, estPrice) -> None:
        self.state.onAccepted(conId, action, qty, estPrice)

    def _onFill(self, conId, action, qty, price, estPrice) -> None:
        self.state.onFill(conId, action, qty, price, estPrice)
        log.info("Fill %s: %s %d @ %.5f, position now %d, equity %s",
                 logSetup.name(conId), action, qty, price,
                 self.state.inventory.get(conId, 0), f"{self.state.equity():,.0f}")

    def _onPartial(self, conId, action, filledQty, avgPrice, remainingQty, estPrice) -> None:
        self.state.onPartial(conId, action, filledQty, avgPrice, remainingQty, estPrice)
        log.info("Partial fill %s: %s %d of %d @ %.5f, position now %d",
                 logSetup.name(conId), action, filledQty, filledQty + remainingQty,
                 avgPrice, self.state.inventory.get(conId, 0))

    def summary(self) -> str:
        open_pos = {logSetup.name(c): q for c, q in self.state.inventory.items() if q}
        pos = ", ".join(f"{s}:{q:+d}" for s, q in sorted(open_pos.items())) or "flat"
        unpriced = self.state.unpricedCurrencies()
        if unpriced:
            return (f"awaiting first price for {', '.join(unpriced)}   "
                    f"fills {self.state.fills}   {pos}")
        return (f"equity {self.state.equity():,.0f}   "
                f"gross {self.state.grossNotionalUSD():,.0f}   "
                f"free margin {self.state.freeMarginUSD():,.0f}   "
                f"fills {self.state.fills}   {pos}")
