import logging

log = logging.getLogger(__name__)

class RiskGate:

    def __init__(self, stateManager, sessionManager, killSwitchActive: bool, maxOrderQty: int, maxPosition: int, minCash: float) -> None:
        self._state           = stateManager
        self._session         = sessionManager
        self.killSwitchActive = killSwitchActive
        self.maxOrderQty      = maxOrderQty
        self.maxPosition      = maxPosition
        self.minCash          = minCash

    def allowTrade(self, contractId: int, action: str, qty: int, estimatedPrice: float = 0.0) -> bool:
        if self.killSwitchActive:
            log.error("RiskGate BLOCKED: Kill switch is ACTIVE.")
            return False

        if not self._session.isActive:
            log.warning("RiskGate BLOCKED: Market session is closed.")
            return False

        if qty <= 0:
            log.warning("RiskGate BLOCKED: Invalid quantity %d.", qty)
            return False

        if qty > self.maxOrderQty:
            log.warning("RiskGate BLOCKED: Quantity %d exceeds order limit %d.", qty, self.maxOrderQty)
            return False

        currentPos = self._state.inventory.get(contractId, 0)
        cost       = qty * estimatedPrice

        if action == "BUY":
            newPos = currentPos + qty
            if (self._state.cash - cost) < self.minCash:
                log.warning("RiskGate BLOCKED: Insufficient cash. Cost: %.2f, Cash: %.2f", cost, self._state.cash)
                return False
        elif action == "SELL":
            newPos = currentPos - qty
        else:
            log.warning("RiskGate BLOCKED: Unknown action %s.", action)
            return False

        if abs(newPos) > self.maxPosition:
            log.warning("RiskGate BLOCKED: Post-trade position %d exceeds limit %d.", newPos, self.maxPosition)
            return False

        return True