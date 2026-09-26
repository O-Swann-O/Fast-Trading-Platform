import logging
from ib_async import IB, AccountValue, PortfolioItem

log = logging.getLogger(__name__)


class AccountManager:

    def __init__(self, ib: IB) -> None:
        self._ib              = ib
        self._account         = ""
        self.onAccountUpdate  = None
        self.onPositionUpdate = None
        self.onExchangeRate   = None   # (currency, base units per unit of currency)

    def start(self) -> None:
        accounts = self._ib.managedAccounts()
        if not accounts:
            log.error("No managed accounts found. Cannot start AccountManager.")
            return

        self._account = accounts[0]
        log.info("Starting AccountManager for account: %s", self._account)

        self._ib.accountValueEvent    += self._onAccountValue
        self._ib.updatePortfolioEvent += self._onPortfolio

        try:
            self._ib.client.reqAccountUpdates(True, self._account)
        except Exception as e:
            log.error("reqAccountUpdates failed: %s", e)

        values    = self._ib.accountValues()
        items     = self._ib.portfolio()
        positions = self._ib.positions()
        log.info("AccountManager subscribed: replaying %d account values, "
                 "%d portfolio items, %d positions.",
                 len(values), len(items), len(positions))

        for value in values:
            self._onAccountValue(value)
        for item in items:
            self._feedPosition(item.contract, item.position)
        for pos in positions:
            self._feedPosition(pos.contract, pos.position)

    def stop(self) -> None:
        if self._account and self._ib.isConnected():
            try:
                self._ib.client.reqAccountUpdates(False, self._account)
            except Exception as e:
                log.debug("Error stopping account updates: %s", e)

        try:
            self._ib.accountValueEvent    -= self._onAccountValue
            self._ib.updatePortfolioEvent -= self._onPortfolio
        except ValueError:
            pass

        log.info("AccountManager stopped.")

    def _onAccountValue(self, value: AccountValue) -> None:
        try:
            valFloat = float(value.value)
        except (ValueError, TypeError):
            return
        if self.onAccountUpdate:
            self.onAccountUpdate(value.tag, value.currency, valFloat)
        # IB sends one ExchangeRate per ledger currency, as '$LEDGER-ExchangeRate', plus a
        # BASE row that is always 1. The same prefix convention as Reconciler._tagName.
        tag = value.tag[len("$LEDGER-"):] if value.tag.startswith("$LEDGER-") else value.tag
        if (tag == "ExchangeRate" and value.currency not in ("", "BASE")
                and self.onExchangeRate):
            self.onExchangeRate(value.currency, valFloat)

    def _onPortfolio(self, item: PortfolioItem) -> None:
        self._feedPosition(item.contract, item.position)

    def _feedPosition(self, contract, position) -> None:
        if contract and contract.conId and self.onPositionUpdate:
            self.onPositionUpdate(contract.conId, position)
