import logging

log = logging.getLogger(__name__)

class RiskGate:

    def __init__(self, stateManager, sessionManager, killSwitchActive: bool, maxOrderQty: int, maxPosition: int, minCash: float, maxTickJump: float = 0.05) -> None:
        self._state           = stateManager
        self._session         = sessionManager
        self.killSwitchActive = killSwitchActive
        self.maxOrderQty      = maxOrderQty
        self.maxPosition      = maxPosition
        self.minCash          = minCash
        self.maxTickJump      = maxTickJump
        self._lastPrices      = {}

    def validateTick(self, contractId: int, price: float) -> bool:
        lastPrice = self._lastPrices.get(contractId)
        self._lastPrices[contractId] = price
        if lastPrice is not None and lastPrice > 0:
            jump = abs(price - lastPrice) / lastPrice
            if jump > self.maxTickJump:
                log.warning("RiskGate TICK REJECTED: Contract %s price jump %.2f%% exceeds limit %.2f%%.",
                            contractId, jump * 100, self.maxTickJump * 100)
                return False
        return True

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
        pendingPos = self._state.pending_inventory.get(contractId, 0)
        assumedPos = currentPos + pendingPos
        cost       = qty * estimatedPrice

        if action == "BUY":
            newPos = assumedPos + qty
            availableCash = self._state.cash - self._state.reserved_cash
            if (availableCash - cost) < self.minCash:
                log.warning("RiskGate BLOCKED: Insufficient cash. Cost: %.2f, Avail: %.2f", cost, availableCash)
                return False
        elif action == "SELL":
            newPos = assumedPos - qty
        else:
            log.warning("RiskGate BLOCKED: Unknown action %s.", action)
            return False

        if abs(newPos) > self.maxPosition:
            log.warning("RiskGate BLOCKED: Post-trade position %d exceeds limit %d.", newPos, self.maxPosition)
            return False

        return True