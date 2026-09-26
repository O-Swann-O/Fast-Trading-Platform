import asyncio
import logging

from ib_async import MarketOrder, LimitOrder, StopOrder, StopLimitOrder

log = logging.getLogger(__name__)

fillTimeout = 30
cancelWait  = 2
# Sent explicitly. Left blank, TWS fills it from the order preset and reports that as
# error 10349, which ib_async (not IB) turns into a 'Cancelled' for an order that is live.
orderTif    = "DAY"
# Matches ib_async's own OrderStatus.DoneStates, plus 'Rejected'.
TERMINAL    = ("Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected")
_KEEP       = 512      # resolved orders remembered, for fills that arrive after resolution


def _brokerReason(trade) -> str:
    status = trade.orderStatus.status
    coded  = [f"{getattr(e, 'errorCode', 0)}: {getattr(e, 'message', '')}"
              for e in reversed(getattr(trade, "log", None) or [])
              if getattr(e, "errorCode", 0)]
    if coded:
        return f"status {status} — " + "; ".join(coded)
    return f"status {status}"


class OrderManager:

    def __init__(self, ib, riskGate):
        self._ib          = ib
        self._gate        = riskGate
        self._active      = {}
        self._events      = {}
        self._resolved    = {}
        self._tasks       = set()
        self._cancelRequested = set()
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
        if not order.tif:
            order.tif = orderTif
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
            self._cancelRequested.add(orderId)
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
                self.onRejected(contractId, None, order.action, requestedQty, estPrice, str(e))
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
            self._cancelRequested.discard(orderId)
            self._release(contractId, order.action, requestedQty, estPrice)

        if unresolved:
            self._requestReconcile()

    def _requestReconcile(self) -> None:
        if self.onUnresolved:
            try:
                self.onUnresolved()
            except Exception as e:
                log.error("Unresolved-order callback failed: %s", e)

    def _remember(self, orderId, contractId, action, booked, avgPrice) -> None:
        self._resolved[orderId] = (contractId, action, booked, avgPrice)
        if len(self._resolved) > _KEEP:
            for old in list(self._resolved)[:_KEEP // 2]:
                del self._resolved[old]

    async def _awaitTerminal(self, trade, contractId, requestedQty, estPrice) -> bool:
        orderId = trade.order.orderId
        event   = self._events[orderId]

        try:
            await asyncio.wait_for(event.wait(), timeout=fillTimeout)
            self._reportTerminal(trade, contractId, orderId, requestedQty, estPrice)
            return False

        except asyncio.TimeoutError:
            log.warning("Order %s not terminal after %ds — cancelling.", orderId, fillTimeout)
            self._cancelRequested.add(orderId)
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
        avg    = float(trade.orderStatus.avgFillPrice)
        action = trade.order.action
        self._remember(orderId, contractId, action, filled, avg)    # what is booked below

        if status == "Filled" and self.onFill:
            self.onFill(contractId, action, filled, avg)
            return

        if filled > 0 and self.onPartial:
            self.onPartial(contractId, action, filled, avg, int(requestedQty) - filled)
            return

        unrequested = orderId not in self._cancelRequested
        if (status in ("Rejected", "Inactive") or unrequested) and self.onRejected:
            self.onRejected(contractId, orderId, trade.order.action, requestedQty, estPrice,
                            _brokerReason(trade))
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
        if status not in TERMINAL:
            return
        orderId = trade.order.orderId
        event   = self._events.get(orderId)
        if event is not None:
            if not event.is_set():
                event.set()
            return

        # Nobody is waiting on this order any more. ib_async can finish an order locally —
        # every IB error code it does not class as a warning becomes 'Cancelled' — while it
        # is still live at IB and fills later. The fill's status carries the order's
        # cumulative quantity and average price, so book exactly what was not booked.
        record = self._resolved.get(orderId)
        if record is None:
            log.error("Terminal status '%s' for unknown order %s — requesting reconciliation.",
                      status, orderId)
            self._requestReconcile()
            return
        contractId, action, booked, bookedAvg = record
        filled = int(trade.orderStatus.filled)
        if filled <= booked:
            return                                  # nothing new: a status-only change
        avg   = float(trade.orderStatus.avgFillPrice)
        qty   = filled - booked
        price = (avg * filled - bookedAvg * booked) / qty
        self._remember(orderId, contractId, action, filled, avg)
        log.warning("Late fill: order %s was resolved with %d filled, broker now reports %d "
                    "(status %s); booking %s %d @ %.5f", orderId, booked, filled, status,
                    action, qty, price)
        if self.onFill:
            self.onFill(contractId, action, qty, price)