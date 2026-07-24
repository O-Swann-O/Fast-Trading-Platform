import logging

log = logging.getLogger(__name__)


class StateManager:

    def __init__(self, fx, marginRate: float):
        self.fx                = fx
        self.marginRate        = marginRate
        self.cashBy            = {"USD": 0.0}
        self.inventory         = {}
        self.pending_inventory = {}
        self.reservedMargin    = 0.0
        self.fills             = 0

    def registerInstrument(self, conId: int, base: str, quote: str) -> None:
        self.fx.registerInstrument(conId, base, quote)
        self.cashBy.setdefault(quote, 0.0)

    def onPrice(self, conId: int, price: float) -> None:
        self.fx.onPrice(conId, price)

    def seed(self, currency: str, amount: float) -> None:
        self.cashBy[currency] = self.cashBy.get(currency, 0.0) + amount

    def estNotionalUSD(self, conId: int, qty: int, estPrice: float) -> float:
        return self.fx.estNotionalUSD(conId, qty, estPrice)

    def grossNotionalUSD(self) -> float:
        total = 0.0
        for conId, qty in self.inventory.items():
            if qty:
                val = self.fx.valueUSD(conId, qty)
                if val is not None:
                    total += abs(val)
        return total

    def equity(self) -> float:
        total = 0.0
        for ccy, amount in self.cashBy.items():
            if amount:
                rate = self.fx.usdRate(ccy)
                if rate is None:
                    log.debug("No USD rate for %s; excluded from equity.", ccy)
                    continue
                total += amount * rate
        for conId, qty in self.inventory.items():
            if qty:
                val = self.fx.valueUSD(conId, qty)
                if val is not None:
                    total += val
        return total

    def unpricedCurrencies(self) -> list:
        return sorted(ccy for ccy, amount in self.cashBy.items()
                      if amount and self.fx.usdRate(ccy) is None)

    def freeMarginUSD(self) -> float:
        return self.equity() - self.grossNotionalUSD() * self.marginRate - self.reservedMargin

    def onAccepted(self, conId, action, qty, estPrice) -> None:
        signed = qty if action == "BUY" else -qty
        self.pending_inventory[conId] = self.pending_inventory.get(conId, 0) + signed
        self.reservedMargin += self.fx.estNotionalUSD(conId, qty, estPrice) * self.marginRate

    def _release(self, conId, action, qty, estPrice) -> None:
        signed = qty if action == "BUY" else -qty
        self.pending_inventory[conId] = self.pending_inventory.get(conId, 0) - signed
        self.reservedMargin -= self.fx.estNotionalUSD(conId, qty, estPrice) * self.marginRate

    def onFill(self, conId, action, qty, price, estPrice) -> None:
        quote  = self.fx.quoteOf(conId)
        signed = qty if action == "BUY" else -qty
        self.inventory[conId] = self.inventory.get(conId, 0) + signed
        self.cashBy[quote]    = self.cashBy.get(quote, 0.0) - signed * price
        self._release(conId, action, qty, estPrice)
        self.fills += 1

    def onPartial(self, conId, action, filledQty, avgPrice, remainingQty, estPrice) -> None:
        self.onFill(conId, action, filledQty, avgPrice, estPrice)
        self._release(conId, action, remainingQty, estPrice)

    def onCancelled(self, conId, action, qty, estPrice) -> None:
        self._release(conId, action, qty, estPrice)

    def onRejected(self, conId, action, qty, estPrice) -> None:
        self._release(conId, action, qty, estPrice)

    def reconcilePosition(self, conId: int, qty: int) -> None:
        self.inventory[conId] = qty

    def reconcileCash(self, currency: str, amount: float) -> None:
        self.cashBy[currency] = amount
