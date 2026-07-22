import logging
from ib_async import IB, AccountValue, PortfolioItem

log = logging.getLogger(__name__)


class AccountManager:

    def __init__(self, ib: IB) -> None:
        self._ib              = ib
        self._account         = ""
        self.onAccountUpdate  = None
        self.onPositionUpdate = None

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

        values = self._ib.accountValues()
        items  = self._ib.portfolio()
        log.info("AccountManager subscribed: replaying %d account values, %d portfolio items.",
                 len(values), len(items))

        for value in values:
            self._onAccountValue(value)
        for item in items:
            self._onPortfolio(item)

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
        if not self.onAccountUpdate:
            return
        try:
            valFloat = float(value.value)
        except (ValueError, TypeError):
            return
        self.onAccountUpdate(value.tag, value.currency, valFloat)

    def _onPortfolio(self, item: PortfolioItem) -> None:
        contractId = item.contract.conId
        position   = item.position

        if contractId and self.onPositionUpdate:
            self.onPositionUpdate(contractId, position)