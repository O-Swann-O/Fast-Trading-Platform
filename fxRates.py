import logging

log = logging.getLogger(__name__)


class FxRates:

    def __init__(self):
        self._meta    = {}
        self._prices  = {}
        self._ccyPair = {}

    def registerInstrument(self, conId: int, base: str, quote: str) -> None:
        self._meta[conId] = (base, quote)
        if base != "USD" and base not in self._ccyPair:
            self._ccyPair[base] = (conId, False)
        if quote != "USD" and quote not in self._ccyPair:
            self._ccyPair[quote] = (conId, True)

    def onPrice(self, conId: int, price: float) -> None:
        self._prices[conId] = price

    def baseOf(self, conId: int) -> str:
        return self._meta[conId][0]

    def quoteOf(self, conId: int) -> str:
        return self._meta[conId][1]

    def usdRate(self, ccy: str):
        if ccy == "USD":
            return 1.0
        pair = self._ccyPair.get(ccy)
        if pair is None:
            return None
        conId, inverse = pair
        px = self._prices.get(conId)
        if not px:
            return None
        return (1.0 / px) if inverse else px

    def estNotionalUSD(self, conId: int, qty: int, estPrice: float) -> float:
        base, quote = self._meta[conId]
        if base == "USD":
            return abs(qty)
        if quote == "USD" and estPrice > 0:
            return abs(qty) * estPrice
        rate = self.usdRate(base)
        return abs(qty) * rate if rate else 0.0

    def valueUSD(self, conId: int, qty: int):
        rate = self.usdRate(self.baseOf(conId))
        return None if rate is None else qty * rate
