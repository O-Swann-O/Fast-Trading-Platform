import asyncio
import logging

log = logging.getLogger(__name__)


class Reconciler:

    def __init__(self, ib, state, intervalSeconds: int = 300) -> None:
        self._ib              = ib
        self._state           = state
        self._interval        = intervalSeconds
        self._running         = False
        self._task            = None
        self.onDriftCorrected = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._auditLoop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _auditLoop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                self._reconcile()
            except Exception as e:
                log.error("Reconciler failed during audit: %s", e)

    def _inFlight(self, contractId) -> bool:
        return self._state.pending_inventory.get(contractId, 0) != 0

    def _reconcile(self) -> None:
        driftFound = False

        brokerPositions = {p.contract.conId: int(p.position)
                           for p in self._ib.positions() if p.contract}

        for contractId, internalQty in list(self._state.inventory.items()):
            if self._inFlight(contractId):
                log.debug("Reconcile skipped %s: order in flight.", contractId)
                continue
            trueQty = brokerPositions.get(contractId, 0)
            if internalQty != trueQty:
                log.warning("Inventory drift on %s: Internal=%d, Broker=%d. Overwriting.",
                            contractId, internalQty, trueQty)
                self._state.reconcilePosition(contractId, trueQty)
                driftFound = True
                self._fireDriftCallback("INVENTORY", contractId, internalQty, trueQty)

        for contractId, trueQty in brokerPositions.items():
            if contractId in self._state.inventory or trueQty == 0:
                continue
            if self._inFlight(contractId):
                continue
            log.warning("Untracked position on %s: Broker=%d. Adding to state.", contractId, trueQty)
            self._state.reconcilePosition(contractId, trueQty)
            driftFound = True
            self._fireDriftCallback("INVENTORY", contractId, 0, trueQty)

        for val in self._ib.accountValues():
            if val.tag == "NetLiquidation" and val.currency == "BASE":
                trueEquity     = float(val.value)
                internalEquity = self._state.equity()
                if abs(internalEquity - trueEquity) > 0.05:
                    log.warning("Equity drift: Internal=%.2f, Broker=%.2f. Adjusting.",
                                internalEquity, trueEquity)
                    self._state.adjustEquityUSD(trueEquity - internalEquity)
                    driftFound = True
                    self._fireDriftCallback("EQUITY", "BASE", internalEquity, trueEquity)
                break

        if not driftFound:
            log.debug("Audit complete: State matches broker.")

    def _fireDriftCallback(self, driftType, asset, oldVal, newVal) -> None:
        if self.onDriftCorrected:
            self.onDriftCorrected(driftType, asset, oldVal, newVal)
