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
        self._ignored         = set()
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

    def auditNow(self) -> None:
        try:
            self._reconcile()
        except Exception as e:
            log.error("Startup audit failed: %s", e)

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

    def _brokerTruth(self):
        reported  = [p for p in self._ib.positions() if p.contract and p.contract.conId]
        positions = {}
        baseLegs  = {}
        for p in reported:
            conId = p.contract.conId
            if not self._state.fx.isRegistered(conId):
                if conId not in self._ignored:
                    self._ignored.add(conId)
                    log.warning("Ignoring broker position %s %s (conId %s): not in the traded "
                                "universe, so it is left out of the book.",
                                p.contract.localSymbol or p.contract.symbol, p.position, conId)
                continue
            qty = int(p.position)
            positions[conId] = qty
            if p.contract.secType == "CASH":
                base = self._state.fx.baseOf(conId)
                baseLegs[base] = baseLegs.get(base, 0) + qty

        cash = self._brokerCash()
        for ccy, held in baseLegs.items():
            if ccy in cash:
                cash[ccy] -= held
        return reported, positions, cash

    def seed(self) -> None:
        _, positions, cash = self._brokerTruth()
        for conId, qty in positions.items():
            if qty:
                self._state.reconcilePosition(conId, qty)
                log.info("Position seeded: %s %d", logSetup.name(conId), qty)
        for ccy, amount in cash.items():
            if amount:
                self._state.reconcileCash(ccy, amount)
                log.info("Book seeded: %s %.2f", ccy, amount)
        excluded = self._state.unconvertibleCurrencies()
        if excluded:
            log.warning("No traded pair converts %s to USD; excluded from equity.",
                        ", ".join(excluded))

    def _reconcile(self) -> None:
        driftFound = False

        reported, brokerPositions, brokerCash = self._brokerTruth()

        held = [c for c, q in self._state.inventory.items() if q]
        if held and not reported:
            log.error("Broker reports no positions while the book holds %d instrument(s). "
                      "Treating this as a position-feed failure, not as truth; "
                      "positions left untouched. Check Virtual FX Tracking in account settings.",
                      len(held))
            return

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
            for ccy, trueCash in brokerCash.items():
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
