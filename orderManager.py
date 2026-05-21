import asyncio
import logging

from ib_async import MarketOrder, LimitOrder, StopOrder, StopLimitOrder

log = logging.getLogger(__name__)

fillTimeout    = 30
cancelWait     = 2


class OrderManager:

    def __init__(self, ib):
        self._ib       = ib
        self._active   = {}
        self._events   = {}
        self.onFill    = None
        self.onPartial = None
        self.onCancelled = None
        self.onRejected  = None

    def start(self):
        self._ib.orderStatusEvent += self._onOrderStatus

    def stop(self):
        self._ib.orderStatusEvent -= self._onOrderStatus

    async def market(self, contractId, contract, action, qty):
        order = MarketOrder(action, qty)
        return await self._place(contractId, contract, order, qty)

    async def limit(self, contractId, contract, action, qty, price):
        order = LimitOrder(action, qty, price)
        return await self._place(contractId, contract, order, qty)

    async def stopOrder(self, contractId, contract, action, qty, price):
        order = StopOrder(action, qty, price)
        return await self._place(contractId, contract, order, qty)

    async def stopLimitOrder(self, contractId, contract, action, qty, stopPrice, limitPrice):
        order = StopLimitOrder(action, qty, stopPrice, limitPrice)
        return await self._place(contractId, contract, order, qty)

    async def cancel(self, orderId):
        trade = self._active.get(orderId)
        if trade:
            self._ib.cancelOrder(trade.order)
            await asyncio.sleep(cancelWait)

    async def cancelAll(self):
        for orderId in list(self._active):
            await self.cancel(orderId)

    async def _place(self, contractId, contract, order, requestedQty):
        trade    = self._ib.placeOrder(contract, order)
        orderId  = trade.order.orderId
        self._active[orderId] = trade
        self._events[orderId] = asyncio.Event()

        try:
            await self._awaitTerminal(trade, contractId, requestedQty)
        finally:
            self._active.pop(orderId, None)
            self._events.pop(orderId, None)

    async def _awaitTerminal(self, trade, contractId, requestedQty):
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
                    )
                elif self.onCancelled:
                    self.onCancelled(contractId, orderId)
                return

            if status == "Rejected":
                if self.onRejected:
                    self.onRejected(contractId, orderId)
                return

        except asyncio.TimeoutError:
            log.warning("Order %s timed out — cancelling remainder.", orderId)
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
                )
            elif self.onCancelled:
                self.onCancelled(contractId, orderId)

    def _onOrderStatus(self, trade):
        status = trade.orderStatus.status
        if status in ("Filled", "Cancelled", "Inactive", "Rejected"):
            orderId = trade.order.orderId
            event   = self._events.get(orderId)
            if event and not event.is_set():
                event.set()