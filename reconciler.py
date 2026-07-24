import asyncio
import logging

import logSetup

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
            log.info("Reconciler started, auditing every %ds.", self._interval)

    def stop(self) -> None:
        if self._running:
            log.info("Reconciler stopped.")
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

    @staticmethod
    def _tagName(tag: str) -> str:
        return tag[len("$LEDGER-"):] if tag.startswith("$LEDGER-") else tag

    def _brokerCash(self) -> dict:
        cash = {}
        for val in self._ib.accountValues():
            if self._tagName(val.tag) != "CashBalance":
                continue
            if val.currency in ("", "BASE"):
                continue
            try:
                cash[val.currency] = float(val.value)
            except (ValueError, TypeError):
                continue
        return cash

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
                log.warning("Inventory drift %s: internal %d, broker %d — overwriting",
                            logSetup.name(contractId), internalQty, trueQty)
                self._state.reconcilePosition(contractId, trueQty)
                driftFound = True
                self._fireDriftCallback("INVENTORY", contractId, internalQty, trueQty)

        for contractId, trueQty in brokerPositions.items():
            if contractId in self._state.inventory or trueQty == 0:
                continue
            if self._inFlight(contractId):
                continue
            log.warning("Untracked position %s: broker %d — adding to state", logSetup.name(contractId), trueQty)
            self._state.reconcilePosition(contractId, trueQty)
            driftFound = True
            self._fireDriftCallback("INVENTORY", contractId, 0, trueQty)

        anyInFlight = any(q != 0 for q in self._state.pending_inventory.values())
        if anyInFlight:
            log.debug("Cash audit skipped: orders in flight.")
        else:
            for ccy, trueCash in self._brokerCash().items():
                internalCash = self._state.cashBy.get(ccy, 0.0)
                if abs(internalCash - trueCash) > 0.05:
                    log.warning("Cash drift in %s: Internal=%.2f, Broker=%.2f. Overwriting.",
                                ccy, internalCash, trueCash)
                    self._state.reconcileCash(ccy, trueCash)
                    driftFound = True
                    self._fireDriftCallback("CASH", ccy, internalCash, trueCash)

        if not driftFound:
            log.info("Audit OK: %d instruments, cash matched", len(brokerPositions) or len(self._state.inventory))

    def _fireDriftCallback(self, driftType, asset, oldVal, newVal) -> None:
        if self.onDriftCorrected:
            self.onDriftCorrected(driftType, asset, oldVal, newVal)
