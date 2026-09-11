import asyncio
import logging

from ib_async import MarketOrder, LimitOrder, StopOrder, StopLimitOrder

log = logging.getLogger(__name__)

fillTimeout = 30
cancelWait  = 2


class OrderManager:

    def __init__(self, ib, riskGate):
        self._ib          = ib
        self._gate        = riskGate
        self._active      = {}
        self._events      = {}
        self._tasks       = set()
        self.onAccepted   = None
        self.onFill       = None
        self.onPartial    = None
        self.onCancelled  = None
        self.onRejected   = None
        self.onReleased   = None
        self.onCommission = None
        self.onUnresolved = None

    @property
    def pending(self) -> bool:
        return bool(self._tasks)

    def _commissionEvent(self):
        return getattr(self._ib, "commissionReportEvent", None)

    def start(self):
        self._ib.orderStatusEvent += self._onOrderStatus
        event = self._commissionEvent()
        if event is not None:
            event += self._onCommissionReport

    def stop(self):
        try:
            self._ib.orderStatusEvent -= self._onOrderStatus
        except ValueError:
            pass
        event = self._commissionEvent()
        if event is not None:
            try:
                event -= self._onCommissionReport
            except ValueError:
                pass
        for task in list(self._tasks):
            task.cancel()

    def _launch(self, contractId, contract, order, qty, estPrice):
        started = [False]
        task    = asyncio.create_task(
            self._place(contractId, contract, order, qty, estPrice, started))
        self._tasks.add(task)

        def _done(finished):
            self._tasks.discard(finished)
            if not started[0]:
                log.warning("Order task for contract %s never started; releasing reservation.",
                            contractId)
                self._release(contractId, order.action, qty, estPrice)

        task.add_done_callback(_done)
        return task

    def submitMarket(self, contractId, contract, action, qty, estPrice=0.0):
        if not self._gate.allowTrade(contractId, action, qty, estPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, estPrice)
        return self._launch(contractId, contract, MarketOrder(action, qty), qty, estPrice)

    def submitLimit(self, contractId, contract, action, qty, limitPrice):
        if not self._gate.allowTrade(contractId, action, qty, limitPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, limitPrice)
        return self._launch(contractId, contract, LimitOrder(action, qty, limitPrice),
                            qty, limitPrice)

    def submitStopOrder(self, contractId, contract, action, qty, stopPrice):
        if not self._gate.allowTrade(contractId, action, qty, stopPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, stopPrice)
        return self._launch(contractId, contract, StopOrder(action, qty, stopPrice),
                            qty, stopPrice)

    def submitStopLimitOrder(self, contractId, contract, action, qty, stopPrice, limitPrice):
        if not self._gate.allowTrade(contractId, action, qty, limitPrice):
            return None
        if self.onAccepted:
            self.onAccepted(contractId, action, qty, limitPrice)
        return self._launch(contractId, contract,
                            StopLimitOrder(action, qty, stopPrice, limitPrice),
                            qty, limitPrice)

    async def cancel(self, orderId):
        trade = self._active.get(orderId)
        if trade:
            try:
                self._ib.cancelOrder(trade.order)
            except Exception as e:
                log.warning("Cancel failed for order %s: %s", orderId, e)
                return
            await asyncio.sleep(cancelWait)

    async def cancelAll(self):
        pending = list(self._active)
        if not pending:
            return
        log.info("Cancelling %d open order(s): %s", len(pending), pending)
        for orderId in pending:
            await self.cancel(orderId)
        log.info("Cancel pass complete.")

    def _release(self, contractId, action, requestedQty, estPrice) -> None:
        if self.onReleased:
            try:
                self.onReleased(contractId, action, requestedQty, estPrice)
            except Exception as e:
                log.error("Release callback failed for contract %s: %s", contractId, e)

    async def _place(self, contractId, contract, order, requestedQty, estPrice, started):
        started[0] = True
        try:
            trade = self._ib.placeOrder(contract, order)
        except Exception as e:
            log.error("Order placement failed for contract %s: %s", contractId, e)
            if self.onRejected:
                self.onRejected(contractId, None, order.action, requestedQty, estPrice)
            self._release(contractId, order.action, requestedQty, estPrice)
            return

        orderId = trade.order.orderId
        self._active[orderId] = trade
        self._events[orderId] = asyncio.Event()

        unresolved = False
        try:
            unresolved = await self._awaitTerminal(trade, contractId, requestedQty, estPrice)
        finally:
            self._active.pop(orderId, None)
            self._events.pop(orderId, None)
            self._release(contractId, order.action, requestedQty, estPrice)

        if unresolved and self.onUnresolved:
            try:
                self.onUnresolved()
            except Exception as e:
                log.error("Unresolved-order callback failed: %s", e)

    async def _awaitTerminal(self, trade, contractId, requestedQty, estPrice) -> bool:
        orderId = trade.order.orderId
        event   = self._events[orderId]

        try:
            await asyncio.wait_for(event.wait(), timeout=fillTimeout)
            self._reportTerminal(trade, contractId, orderId, requestedQty, estPrice)
            return False

        except asyncio.TimeoutError:
            log.warning("Order %s not terminal after %ds — cancelling.", orderId, fillTimeout)
            try:
                self._ib.cancelOrder(trade.order)
            except Exception as e:
                log.warning("Timeout-cancel failed for order %s: %s", orderId, e)

            resolved = True
            try:
                await asyncio.wait_for(event.wait(), timeout=cancelWait)
            except asyncio.TimeoutError:
                resolved = False

            self._reportTerminal(trade, contractId, orderId, requestedQty, estPrice)
            if not resolved:
                log.error("Order %s final state unknown after cancel; "
                          "requesting reconciliation.", orderId)
            return not resolved

    def _reportTerminal(self, trade, contractId, orderId, requestedQty, estPrice) -> None:
        status = trade.orderStatus.status
        filled = int(trade.orderStatus.filled)

        if status == "Filled" and self.onFill:
            self.onFill(contractId, trade.order.action, filled,
                        float(trade.orderStatus.avgFillPrice))
            return

        if filled > 0 and self.onPartial:
            self.onPartial(contractId, trade.order.action, filled,
                           float(trade.orderStatus.avgFillPrice),
                           int(requestedQty) - filled)
            return

        if status == "Rejected" and self.onRejected:
            self.onRejected(contractId, orderId, trade.order.action, requestedQty, estPrice)
            return

        if self.onCancelled:
            self.onCancelled(contractId, orderId, trade.order.action, requestedQty, estPrice)

    def _onCommissionReport(self, trade, fill, report) -> None:
        if not self.onCommission:
            return
        try:
            amount = float(getattr(report, "commission", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        if not amount:
            return
        contract = getattr(fill, "contract", None)
        conId    = getattr(contract, "conId", None) if contract is not None else None
        if conId is None:
            return
        currency = getattr(report, "currency", "") or "USD"
        self.onCommission(int(conId), amount, currency)

    def _onOrderStatus(self, trade):
        status = trade.orderStatus.status
        if status in ("Filled", "Cancelled", "Inactive", "Rejected"):
            orderId = trade.order.orderId
            event   = self._events.get(orderId)
            if event and not event.is_set():
                event.set()
