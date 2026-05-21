import logging
from ib_async import IB, AccountValue, PortfolioItem

log = logging.getLogger(__name__)


class AccountManager:

    def __init__(self, ib: IB) -> None:
        self._ib              = ib
        self._account         = ""
        self.onAccountUpdate  = None
        self.onPositionUpdate = None

    async def start(self) -> None:
        accounts = self._ib.managedAccounts()
        if not accounts:
            log.error("No managed accounts found. Cannot start AccountManager.")
            return

        self._account = accounts[0]
        log.info("Starting AccountManager for account: %s", self._account)

        self._ib.accountValueEvent    += self._onAccountValue
        self._ib.updatePortfolioEvent += self._onPortfolio

        await self._ib.reqAccountUpdatesAsync(self._account)

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
        if value.currency == "BASE" and self.onAccountUpdate:
            try:
                valFloat = float(value.value)
                self.onAccountUpdate(value.tag, valFloat)
            except ValueError:
                pass

    def _onPortfolio(self, item: PortfolioItem) -> None:
        contractId = item.contract.conId
        position   = item.position

        if contractId and self.onPositionUpdate:
            self.onPositionUpdate(contractId, position)