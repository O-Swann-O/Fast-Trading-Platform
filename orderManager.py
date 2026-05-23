import asyncio
import logging

from ib_async import MarketOrder, LimitOrder, StopOrder, StopLimitOrder

log = logging.getLogger(__name__)

fillTimeout = 30
cancelWait  = 2

class OrderManager:

    def __init__(self, ib, riskGate):
        self._ib         = ib
        self._gate       = riskGate
        self._active     = {}
        self._events     = {}
        self.onAccepted  = None
        self.onFill      = None
        self.onPartial   = None
        self.onCancelled = None
        self.onRejected  = None

    def start(self):
        self._ib.orderStatusEvent += self._onOrderStatus

    def stop(self):
        self._ib.orderStatusEvent -= self._onOrderStatus

    def submitMarket(self, contractId, contract, action, qty, estPrice=0.0):
        if not self._gate.allowTrade(contractId, action, qty, estPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, estPrice)
        order = MarketOrder(action, qty)
        return asyncio.create_task(self._place(contractId, contract, order, qty, estPrice))

    def submitLimit(self, contractId, contract, action, qty, limitPrice):
        if not self._gate.allowTrade(contractId, action, qty, limitPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, limitPrice)
        order = LimitOrder(action, qty, limitPrice)
        return asyncio.create_task(self._place(contractId, contract, order, qty, limitPrice))

    def submitStopOrder(self, contractId, contract, action, qty, stopPrice):
        if not self._gate.allowTrade(contractId, action, qty, stopPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, stopPrice)
        order = StopOrder(action, qty, stopPrice)
        return asyncio.create_task(self._place(contractId, contract, order, qty, stopPrice))

    def submitStopLimitOrder(self, contractId, contract, action, qty, stopPrice, limitPrice):
        if not self._gate.allowTrade(contractId, action, qty, limitPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, limitPrice)
        order = StopLimitOrder(action, qty, stopPrice, limitPrice)
        return asyncio.create_task(self._place(contractId, contract, order, qty, limitPrice))

    async def cancel(self, orderId):
        trade = self._active.get(orderId)
        if trade:
            self._ib.cancelOrder(trade.order)
            await asyncio.sleep(cancelWait)

    async def cancelAll(self):
        for orderId in list(self._active):
            await self.cancel(orderId)

    async def _place(self, contractId, contract, order, requestedQty, estPrice):
        trade    = self._ib.placeOrder(contract, order)
        orderId  = trade.order.orderId
        self._active[orderId] = trade
        self._events[orderId] = asyncio.Event()

        try:
            await self._awaitTerminal(trade, contractId, requestedQty, estPrice)
        finally:
            self._active.pop(orderId, None)
            self._events.pop(orderId, None)

    async def _awaitTerminal(self, trade, contractId, requestedQty, estPrice):
        orderId = trade.order.orderId
        event   = self._events[orderId]

        try:
            await asyncio.wait_for(event.wait(), timeout=fillTimeout)

            status = trade.orderStatus.status
            if status == "Filled":
                if self.onFill:
                    self.onFill(
                        contractId,
                        trade.order.action,
                        int(trade.orderStatus.filled),
                        float(trade.orderStatus.avgFillPrice),
                        estPrice
                    )
                return

            if status in ("Cancelled", "Inactive"):
                filled = int(trade.orderStatus.filled)
                if filled > 0 and self.onPartial:
                    self.onPartial(
                        contractId,
                        trade.order.action,
                        filled,
                        float(trade.orderStatus.avgFillPrice),
                        int(requestedQty) - filled,
                        estPrice
                    )
                elif self.onCancelled:
                    self.onCancelled(contractId, orderId, trade.order.action, requestedQty, estPrice)
                return

            if status == "Rejected":
                if self.onRejected:
                    self.onRejected(contractId, orderId, trade.order.action, requestedQty, estPrice)
                return

        except asyncio.TimeoutError:
            self._ib.cancelOrder(trade.order)
            await asyncio.sleep(cancelWait)

            filled = int(trade.orderStatus.filled)
            if filled > 0 and self.onPartial:
                self.onPartial(
                    contractId,
                    trade.order.action,
                    filled,
                    float(trade.orderStatus.avgFillPrice),
                    int(requestedQty) - filled,
                    estPrice
                )
            elif self.onCancelled:
                self.onCancelled(contractId, orderId, trade.order.action, requestedQty, estPrice)

    def _onOrderStatus(self, trade):
        status = trade.orderStatus.status
        if status in ("Filled", "Cancelled", "Inactive", "Rejected"):
            orderId = trade.order.orderId
            event   = self._events.get(orderId)
            if event and not event.is_set():
                event.set()