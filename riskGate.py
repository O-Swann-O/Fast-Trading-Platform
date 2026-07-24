import os
import logging

import logSetup

log = logging.getLogger(__name__)


class RiskGate:

    def __init__(self, state, session, killSwitchFile: str,
                 maxOrderNotional: float, maxPositionNotional: float,
                 minFreeMargin: float, maxTickJump: float = 0.05) -> None:
        self._state              = state
        self._session            = session
        self.killSwitchFile      = killSwitchFile
        self.maxOrderNotional    = maxOrderNotional
        self.maxPositionNotional = maxPositionNotional
        self.minFreeMargin       = minFreeMargin
        self.maxTickJump         = maxTickJump
        self._lastPrices         = {}

    def validateTick(self, contractId: int, price: float) -> bool:
        lastPrice = self._lastPrices.get(contractId)
        self._lastPrices[contractId] = price
        if lastPrice is not None and lastPrice > 0:
            jump = abs(price - lastPrice) / lastPrice
            if jump > self.maxTickJump:
                log.warning("Tick rejected %s: jump %.2f%% exceeds %.2f%%",
                            logSetup.name(contractId), jump * 100, self.maxTickJump * 100)
                return False
        return True

    def allowTrade(self, contractId: int, action: str, qty: int, estimatedPrice: float = 0.0) -> bool:
        if self.killSwitchFile and os.path.exists(self.killSwitchFile):
            log.error("Blocked: kill switch file present (%s)", self.killSwitchFile)
            return False

        if not self._session.isActive:
            log.warning("Blocked: market session closed")
            return False

        if qty <= 0:
            log.warning("Blocked: invalid quantity %d", qty)
            return False

        if action not in ("BUY", "SELL"):
            log.warning("Blocked: unknown action %s", action)
            return False

        orderNotional = self._state.estNotionalUSD(contractId, qty, estimatedPrice)
        if orderNotional <= 0:
            log.warning("Blocked %s: cannot price order notional", logSetup.name(contractId))
            return False

        if orderNotional > self.maxOrderNotional:
            log.warning("Blocked %s: order notional %.0f exceeds %.0f", logSetup.name(contractId),
                        orderNotional, self.maxOrderNotional)
            return False

        currentPos = (self._state.inventory.get(contractId, 0)
                      + self._state.pending_inventory.get(contractId, 0))
        newPos = currentPos + (qty if action == "BUY" else -qty)

        newNotional = self._state.estNotionalUSD(contractId, abs(newPos), estimatedPrice)
        if newNotional > self.maxPositionNotional:
            log.warning("Blocked %s: post-trade notional %.0f exceeds %.0f", logSetup.name(contractId),
                        newNotional, self.maxPositionNotional)
            return False

        curNotional = self._state.estNotionalUSD(contractId, abs(currentPos), estimatedPrice)
        marginDelta = (newNotional - curNotional) * self._state.marginRate
        freeMargin  = self._state.freeMarginUSD()
        if freeMargin - max(marginDelta, 0.0) < self.minFreeMargin:
            log.warning("Blocked %s: free margin %.0f insufficient (need %.0f + %.0f floor)", logSetup.name(contractId),
                        freeMargin, max(marginDelta, 0.0), self.minFreeMargin)
            return False

        return True