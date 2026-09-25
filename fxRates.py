import logging

log = logging.getLogger(__name__)


class FxRates:

    def __init__(self):
        self._meta     = {}
        self._prices   = {}
        self._ccyPair  = {}
        self._baseRate = {}

    def onBrokerAccountValue(self, tag: str, currency: str, value: float) -> None:
        """Broker-reported rates for currencies no traded pair can reach. IB sends one
        rate per currency, quoted against the account's base currency, so USD's own entry
        is what normalises them. The tag arrives as '$LEDGER-ExchangeRate'; the prefix is
        stripped and BASE skipped exactly as Reconciler does for cash. Absent the tag this
        stays empty and usdRate falls back to traded pairs alone."""
        if tag.startswith("$LEDGER-"):
            tag = tag[len("$LEDGER-"):]
        if tag == "ExchangeRate" and currency not in ("", "BASE") and value:
            self._baseRate[currency] = float(value)

    def _brokerUsdRate(self, ccy: str):
        rate    = self._baseRate.get(ccy)
        usdRate = self._baseRate.get("USD")
        if not rate or not usdRate:
            return None
        return rate / usdRate

    def registerInstrument(self, conId: int, base: str, quote: str) -> None:
        self._meta[conId] = (base, quote)
        if quote == "USD" and base not in self._ccyPair:
            self._ccyPair[base] = (conId, False)
        if base == "USD" and quote not in self._ccyPair:
            self._ccyPair[quote] = (conId, True)

    def onPrice(self, conId: int, price: float) -> None:
        self._prices[conId] = price

    def isRegistered(self, conId: int) -> bool:
        return conId in self._meta

    def baseOf(self, conId: int) -> str:
        return self._meta[conId][0]

    def quoteOf(self, conId: int) -> str:
        return self._meta[conId][1]

    def canConvert(self, ccy: str) -> bool:
        return (ccy == "USD" or ccy in self._ccyPair
                or self._brokerUsdRate(ccy) is not None)

    def usdRate(self, ccy: str):
        if ccy == "USD":
            return 1.0
        pair = self._ccyPair.get(ccy)
        if pair is None:
            return self._brokerUsdRate(ccy)
        conId, inverse = pair
        px = self._prices.get(conId)
        if not px:
            return None                      # traded pair exists but has not printed yet
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