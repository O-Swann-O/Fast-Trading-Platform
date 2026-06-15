import logging
from eventkit import Event

log = logging.getLogger(__name__)


class _Status:
    __slots__ = ["status", "filled", "avgFillPrice"]
    def __init__(self):
        self.status       = "PendingSubmit"
        self.filled       = 0
        self.avgFillPrice = 0.0


class _Trade:
    __slots__ = ["order", "orderStatus"]
    def __init__(self, order):
        self.order       = order
        self.orderStatus = _Status()


class _Ticker:
    __slots__ = ["contract", "time", "_price"]
    def __init__(self, contract, price, time):
        self.contract = contract
        self.time     = time
        self._price   = price
    def marketPrice(self):
        return self._price


class SimBroker:

    def __init__(self, conIdMap, halfSpread):
        self._conIdMap   = conIdMap
        self._halfSpread = halfSpread
        self.orderStatusEvent    = Event("orderStatusEvent")
        self.pendingTickersEvent = Event("pendingTickersEvent")
        self._contracts = {}
        self._open      = {}
        self._nextId    = 1

    async def qualifyContractsAsync(self, contract):
        key   = f"{contract.symbol}{contract.currency}"
        conId = self._conIdMap.get(key)
        if conId is None:
            log.error("SimBroker: no conId mapped for %s", key)
            return []
        contract.conId = conId
        if not contract.exchange:
            contract.exchange = "SIM"
        if not getattr(contract, "localSymbol", ""):
            contract.localSymbol = f"{contract.symbol}.{contract.currency}"
        self._contracts[conId] = contract
        return [contract]

    def reqMktData(self, contract, genericTickList, snapshot, regulatorySnapshot):
        pass

    def cancelMktData(self, contract):
        pass

    def placeOrder(self, contract, order):
        order.orderId = self._nextId
        self._nextId += 1
        order.conId   = contract.conId
        trade = _Trade(order)
        trade.orderStatus.status = "Submitted"
        self._open[order.orderId] = trade
        return trade

    def cancelOrder(self, order):
        trade = self._open.pop(order.orderId, None)
        if trade is None:
            return
        trade.orderStatus.status = "Cancelled"
        self.orderStatusEvent.emit(trade)

    def feedTick(self, conId, bid, ask, ts):
        self._match(conId, bid, ask)
        contract = self._contracts.get(conId)
        if contract is not None:
            mid = (bid + ask) * 0.5
            self.pendingTickersEvent.emit([_Ticker(contract, mid, ts)])

    def _match(self, conId, bid, ask):
        if not self._open:
            return
        hs = self._halfSpread.get(conId, 0.0)
        for orderId in list(self._open):
            trade = self._open[orderId]
            if trade.order.conId != conId:
                continue
            qty  = int(trade.order.totalQuantity)
            fill = (ask + hs) if trade.order.action == "BUY" else (bid - hs)
            trade.orderStatus.status       = "Filled"
            trade.orderStatus.filled       = qty
            trade.orderStatus.avgFillPrice = fill
            self._open.pop(orderId, None)
            self.orderStatusEvent.emit(trade)