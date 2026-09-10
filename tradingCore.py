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

    def __init__(self, ib, clock, source, session, state, sampleInterval=None) -> None:
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
        self.recorder = None
        self._source  = source
        self._blocked = {}
        self._interval = config.sampleInterval if sampleInterval is None else sampleInterval
        self._priced  = False

        self.feeder.onTick      = self._onTick
        self.orders.onAccepted  = state.onAccepted
        self.orders.onReleased  = state.releasePending
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
            sampleInterval = self._interval,
            staleLimit     = config.staleLimit,
        )
        self.sampler.onTargetPosition = self._onTargetPosition
        log.info("Core ready: %d instruments, sampling every %.1fs, stale after %.1fs, "
                 "order cap %s, position cap %s, margin floor %s",
                 len(conIds), self._interval, config.staleLimit,
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

        if not self._priced and not self.state.pendingCurrencies():
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

        blockedUntil = self._blocked.get(conId)
        if blockedUntil is not None:
            if self.clock.timestamp() < blockedUntil:
                return
            del self._blocked[conId]

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

    def _onRejected(self, contractId, orderId, action, qty, estPrice) -> None:
        self._blocked[contractId] = self.clock.timestamp() + config.rejectCooldown
        log.error("Rejected %s order %s (%s %d) — suppressing new orders for %ds",
                  logSetup.name(contractId), orderId, action, qty, config.rejectCooldown)

    def _onFill(self, conId, action, qty, price) -> None:
        self._blocked.pop(conId, None)
        self.state.applyFill(conId, action, qty, price)
        position = self.state.inventory.get(conId, 0)
        equity   = self.state.equity()
        log.info("Fill %s: %s %d @ %.5f, position now %d, equity %s",
                 logSetup.name(conId), action, qty, price, position, f"{equity:,.0f}")
        if self.recorder:
            self.recorder.fill(self.clock.now(), conId, logSetup.name(conId),
                               action, qty, price, position, equity)

    def _onPartial(self, conId, action, filledQty, avgPrice, remainingQty) -> None:
        self.state.applyFill(conId, action, filledQty, avgPrice)
        position = self.state.inventory.get(conId, 0)
        log.info("Partial fill %s: %s %d of %d @ %.5f, position now %d",
                 logSetup.name(conId), action, filledQty, filledQty + remainingQty,
                 avgPrice, position)
        if self.recorder:
            self.recorder.fill(self.clock.now(), conId, logSetup.name(conId),
                               action, filledQty, avgPrice, position, self.state.equity())

    def summary(self) -> str:
        open_pos = {logSetup.name(c): q for c, q in self.state.inventory.items() if q}
        pos = ", ".join(f"{s}:{q:+d}" for s, q in sorted(open_pos.items())) or "flat"
        pending = self.state.pendingCurrencies()
        if pending:
            return (f"awaiting first price for {', '.join(pending)}   "
                    f"fills {self.state.fills}   {pos}")
        excluded = self.state.unconvertibleCurrencies()
        note = f" (excl {', '.join(excluded)})" if excluded else ""
        return (f"equity {self.state.equity():,.0f}{note}   "
                f"gross {self.state.grossNotionalUSD():,.0f}   "
                f"free margin {self.state.freeMarginUSD():,.0f}   "
                f"fills {self.state.fills}   {pos}")

    def marks(self) -> dict:
        return {int(c): float(p) for c, p in self.state.fx._prices.items()}

    def symbols(self) -> dict:
        return {int(c.conId): f"{c.symbol}{c.currency}" for c in self.registry.getAll()}
