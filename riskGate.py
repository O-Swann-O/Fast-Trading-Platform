import os
import logging

import logSetup

log = logging.getLogger(__name__)


class RiskGate:

    def __init__(self, state, session, killSwitchFile: str,
                 maxOrderNotional: float, maxPositionNotional: float,
                 minFreeMargin: float, maxTickJump: float = 0.05,
                 minOrderQty: int = 0) -> None:
        self._state              = state
        self._session            = session
        self.killSwitchFile      = killSwitchFile
        self.maxOrderNotional    = maxOrderNotional
        self.maxPositionNotional = maxPositionNotional
        self.minFreeMargin       = minFreeMargin
        self.minOrderQty         = minOrderQty
        self.maxTickJump         = maxTickJump
        self._lastPrices         = {}
        self._pendingJump        = {}
        self._blockReason        = {}   # contractId -> last reason logged
        self._blockCount         = {}   # contractId -> repeats suppressed since

    def validateTick(self, contractId: int, price: float) -> bool:
        lastPrice = self._lastPrices.get(contractId)
        if lastPrice is None or lastPrice <= 0:
            self._lastPrices[contractId] = price
            self._pendingJump.pop(contractId, None)
            return True

        if abs(price - lastPrice) / lastPrice <= self.maxTickJump:
            self._lastPrices[contractId] = price
            self._pendingJump.pop(contractId, None)
            return True

        candidate = self._pendingJump.get(contractId)
        if candidate and abs(price - candidate) / candidate <= self.maxTickJump:
            log.warning("Level shift confirmed %s: %.5f -> %.5f, accepting",
                        logSetup.name(contractId), lastPrice, price)
            self._lastPrices[contractId] = price
            self._pendingJump.pop(contractId, None)
            return True

        self._pendingJump[contractId] = price
        log.warning("Tick rejected %s: jump %.2f%% exceeds %.2f%%",
                    logSetup.name(contractId), abs(price - lastPrice) / lastPrice * 100,
                    self.maxTickJump * 100)
        return False

    def maxQtyFor(self, contractId: int, estimatedPrice: float) -> int:
        unit = self._state.estNotionalUSD(contractId, 1, estimatedPrice)
        if unit <= 0:
            return 0
        return int(self.maxOrderNotional / unit)

    def meetsMinimum(self, qty: int) -> bool:
        return qty >= self.minOrderQty

    def maxQtyToPositionCap(self, contractId: int, currentPos: int,
                            action: str, estimatedPrice: float) -> int:
        unit = self._state.estNotionalUSD(contractId, 1, estimatedPrice)
        if unit <= 0:
            return 0
        maxUnits = int(self.maxPositionNotional / unit)
        signed   = 1 if action == "BUY" else -1
        if currentPos == 0 or (signed > 0) == (currentPos > 0):
            return max(0, maxUnits - abs(currentPos))
        return abs(currentPos) + maxUnits

    def _deny(self, contractId, reason, *args) -> bool:
        """Log a block once. Identical repeats are counted, not printed: a wedged
        book re-blocks every instrument on every sample, which is hundreds of
        thousands of lines a day and slows a backtest to a crawl."""
        if self._blockReason.get(contractId) == reason:
            self._blockCount[contractId] = self._blockCount.get(contractId, 0) + 1
            log.debug(reason, *args)
            return False
        self._blockReason[contractId] = reason
        self._blockCount[contractId]  = 0
        log.warning(reason, *args)
        return False

    def _allow(self, contractId) -> bool:
        n = self._blockCount.pop(contractId, None)
        if self._blockReason.pop(contractId, None) is not None and n:
            log.info("%s clear after %d suppressed block(s)", logSetup.name(contractId), n)
        return True

    def allowTrade(self, contractId: int, action: str, qty: int, estimatedPrice: float = 0.0) -> bool:
        if self.killSwitchFile and os.path.exists(self.killSwitchFile):
            return self._deny(contractId, "Blocked: kill switch file present (%s)",
                              self.killSwitchFile)

        if not self._session.isActive:
            return self._deny(contractId, "Blocked: market session closed")

        if qty <= 0:
            return self._deny(contractId, "Blocked: invalid quantity %d", qty)

        if action not in ("BUY", "SELL"):
            return self._deny(contractId, "Blocked: unknown action %s", action)

        orderNotional = self._state.estNotionalUSD(contractId, qty, estimatedPrice)
        if orderNotional <= 0:
            return self._deny(contractId, "Blocked %s: cannot price order notional",
                              logSetup.name(contractId))

        if orderNotional > self.maxOrderNotional:
            return self._deny(contractId, "Blocked %s: order notional %.0f exceeds %.0f", logSetup.name(contractId),
                              orderNotional, self.maxOrderNotional)

        currentPos = (self._state.inventory.get(contractId, 0)
                      + self._state.pending_inventory.get(contractId, 0))
        newPos = currentPos + (qty if action == "BUY" else -qty)

        reducing = abs(newPos) < abs(currentPos) and newPos * currentPos >= 0
        if reducing:
            return self._allow(contractId)

        newNotional = self._state.estNotionalUSD(contractId, abs(newPos), estimatedPrice)
        if newNotional > self.maxPositionNotional:
            return self._deny(contractId, "Blocked %s: post-trade notional %.0f exceeds %.0f", logSetup.name(contractId),
                              newNotional, self.maxPositionNotional)

        curNotional = self._state.estNotionalUSD(contractId, abs(currentPos), estimatedPrice)
        marginDelta = (newNotional - curNotional) * self._state.marginRate
        freeMargin  = self._state.freeMarginUSD()
        if freeMargin - max(marginDelta, 0.0) < self.minFreeMargin:
            return self._deny(contractId,
                              "Blocked %s: free margin %.0f insufficient (need %.0f + %.0f floor)",
                              logSetup.name(contractId), freeMargin, max(marginDelta, 0.0),
                              self.minFreeMargin)

        return self._allow(contractId)
