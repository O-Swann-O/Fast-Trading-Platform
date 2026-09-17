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

    def __init__(self, ib, clock, source, session, state, sampleInterval=None,
                 staleLimit=None) -> None:
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
            minOrderQty         = config.minOrderQty,
            maxTickJump         = config.maxTickJump,
        )
        self.orders   = OrderManager(ib, self.gate)
        self.sampler  = None
        self.ticks    = {}
        self.recorder = None
        self._source  = source
        self._blocked = {}
        self._atCap   = set()
        self._interval = config.sampleInterval if sampleInterval is None else sampleInterval
        self._staleLimit = config.staleLimit if staleLimit is None else staleLimit
        self._priced  = False

        self.feeder.onTick      = self._onTick
        self.orders.onAccepted  = state.onAccepted
        self.orders.onReleased  = state.releasePending
        self.orders.onFill      = self._onFill
        self.orders.onPartial   = self._onPartial
        self.orders.onCancelled  = self._onCancelled
        self.orders.onRejected   = self._onRejected
        self.orders.onCommission = self._onCommission

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
            staleLimit     = self._staleLimit,
        )
        self.sampler.onTargetPosition = self._onTargetPosition
        log.info("Core ready: %d instruments, sampling every %.1fs, stale after %.1fs, "
                 "order cap %s, position cap %s, margin floor %s",
                 len(conIds), self._interval, self._staleLimit,
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
        sign   = 1 if delta > 0 else -1
        ticker = self.ticks.get(conId)
        estPrice = 0.0
        if ticker is not None:
            px = ticker.marketPrice()
            if not math.isnan(px):
                estPrice = px

        room   = self.gate.maxQtyToPositionCap(conId, assumed, action, estPrice)
        qty    = min(abs(delta), room)
        cap    = self.gate.maxQtyFor(conId, estPrice)
        sliced = bool(cap) and qty > cap
        if sliced:
            qty = cap

        newPos   = assumed + sign * qty
        reducing = abs(newPos) < abs(assumed) and newPos * assumed >= 0
        if qty > 0 and not reducing and not self.gate.meetsMinimum(qty):
            if newPos * assumed < 0:
                qty, sliced = abs(assumed), False
            else:
                qty = 0

        if qty <= 0:
            self._cannotAdvance(conId, targetPos, assumed, room, cap, estPrice)
            return
        self._atCap.discard(conId)

        if self.orders.submitMarket(conId, contract, action, qty, estPrice) is None:
            return                                   # gate refused; it logged the reason
        if sliced:
            log.info("Signal %s: target %d -> %s %d of %d (order cap, alpha %.2f)",
                     logSetup.name(conId), targetPos, action, qty, abs(delta), confidence)
        else:
            log.info("Signal %s: target %d -> %s %d (alpha %.2f)",
                     logSetup.name(conId), targetPos, action, qty, confidence)

    def _cannotAdvance(self, conId, targetPos, assumed, room, cap, estPrice) -> None:
        if conId in self._atCap:
            return
        self._atCap.add(conId)
        name    = logSetup.name(conId)
        minimum = self.gate.minOrderQty
        if self.state.estNotionalUSD(conId, 1, estPrice) <= 0:
            log.warning("%s cannot advance toward target %d: no USD price yet", name, targetPos)
        elif room <= 0:
            log.warning("%s cannot advance toward target %d: holding %d at the position cap",
                        name, targetPos, assumed)
        elif cap and cap < minimum:
            need = self.state.estNotionalUSD(conId, minimum, estPrice)
            log.error("%s cannot trade: order cap %s USD buys %d units, below the %d-unit "
                      "minimum. Raise maxOrderNotional to at least %s.", name,
                      f"{self.gate.maxOrderNotional:,.0f}", cap, minimum, f"{need:,.0f}")
        else:
            log.warning("%s cannot advance toward target %d: holding %d, remaining step is "
                        "below the %d-unit minimum", name, targetPos, assumed, minimum)

    def _onCancelled(self, contractId, orderId, action, qty, estPrice) -> None:
        log.warning("Cancelled %s order %s (%s %d)", logSetup.name(contractId), orderId, action, qty)

    def _onRejected(self, contractId, orderId, action, qty, estPrice, reason="") -> None:
        self._blocked[contractId] = self.clock.timestamp() + config.rejectCooldown
        log.error("Rejected %s order %s (%s %d): %s — suppressing new orders for %ds",
                  logSetup.name(contractId), orderId, action, qty, reason or "no reason given",
                  config.rejectCooldown)

    def _onCommission(self, conId, amount, currency) -> None:
        self.state.applyCommission(amount, currency)

    def _onFill(self, conId, action, qty, price) -> None:
        self._blocked.pop(conId, None)
        self.state.applyFill(conId, action, qty, price)
        position = self.state.inventory.get(conId, 0)
        equity   = self.state.equity()
        log.info("Fill %s: %s %d @ %.5f, position now %d, equity %s",
                 logSetup.name(conId), action, qty, price, position, f"{equity:,.0f}")
        if self.recorder:
            self.recorder.fill(self.clock.now(), conId, logSetup.name(conId),
                               action, qty, price, position, equity,
                               self._quoteRate(conId))

    def _onPartial(self, conId, action, filledQty, avgPrice, remainingQty) -> None:
        self.state.applyFill(conId, action, filledQty, avgPrice)
        position = self.state.inventory.get(conId, 0)
        log.info("Partial fill %s: %s %d of %d @ %.5f, position now %d",
                 logSetup.name(conId), action, filledQty, filledQty + remainingQty,
                 avgPrice, position)
        if self.recorder:
            self.recorder.fill(self.clock.now(), conId, logSetup.name(conId),
                               action, filledQty, avgPrice, position, self.state.equity(),
                               self._quoteRate(conId))

    def summary(self) -> str:
        open_pos = {logSetup.name(c): q for c, q in self.state.inventory.items() if q}
        pos = ", ".join(f"{s}:{q:+d}" for s, q in sorted(open_pos.items())) or "flat"
        pending = self.state.pendingCurrencies()
        if pending:
            return (f"awaiting first price for {', '.join(pending)}   "
                    f"fills {self.state.fills}   {pos}")
        excluded = self.state.unconvertibleCurrencies()
        note = f" (excl {', '.join(excluded)})" if excluded else ""
        comm = f"   commission {self.state.commission:,.0f}" if self.state.commission else ""
        return (f"equity {self.state.equity():,.0f}{note}   "
                f"gross {self.state.grossNotionalUSD():,.0f}   "
                f"free margin {self.state.freeMarginUSD():,.0f}   "
                f"fills {self.state.fills}{comm}   {pos}")

    def marks(self) -> dict:
        return {int(c): float(p) for c, p in self.state.fx._prices.items()}

    def symbols(self) -> dict:
        return {int(c.conId): f"{c.symbol}{c.currency}" for c in self.registry.getAll()}

    def _quoteRate(self, conId) -> float:
        rate = self.state.fx.usdRate(self.state.fx.quoteOf(conId))
        return float(rate) if rate else 0.0

    def quoteCurrencies(self) -> dict:
        return {int(c.conId): self.state.fx.quoteOf(c.conId)
                for c in self.registry.getAll()}

    def quoteRates(self) -> dict:
        out = {}
        for c in self.registry.getAll():
            ccy  = self.state.fx.quoteOf(c.conId)
            rate = self.state.fx.usdRate(ccy)
            if rate is not None:
                out[ccy] = float(rate)
        return out
