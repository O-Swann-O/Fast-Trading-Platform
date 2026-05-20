class DataFeeder:

    def __init__(self, ib):
        self._ib          = ib
        self._contractMap = {}
        self.onTick       = None

    def start(self):
        self._ib.pendingTickersEvent += self._onTickers

    def stop(self):
        self._ib.pendingTickersEvent -= self._onTickers

    def subscribe(self, contractId, contract):
        self._contractMap[contract] = contractId
        self._ib.reqMktData(contract, "", False, False)

    def unsubscribe(self, contract):
        self._ib.cancelMktData(contract)
        self._contractMap.pop(contract, None)

    def _onTickers(self, tickers):
        for ticker in tickers:
            contractId = self._contractMap.get(ticker.contract)
            if contractId is not None and self.onTick:
                self.onTick(contractId, ticker)