class DataFeeder:

    def __init__(self, ib):
        self._ib    = ib
        self.onTick = None

    def start(self):
        self._ib.pendingTickersEvent += self._onTickers

    def stop(self):
        self._ib.pendingTickersEvent -= self._onTickers

    def subscribe(self, contractId, contract):
        self._ib.reqMktData(contract, "", False, False)

    def unsubscribe(self, contract):
        self._ib.cancelMktData(contract)

    def _onTickers(self, tickers):
        for ticker in tickers:
            if ticker.contract and ticker.contract.conId and self.onTick:
                self.onTick(ticker.contract.conId, ticker)