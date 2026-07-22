import asyncio
import logging
from datetime import time, timezone

log = logging.getLogger(__name__)

FX_CLOSE_UTC = time(21, 0)


class SessionManager:

    def __init__(self, clock, tradingHoursUTC=None, forceActive: bool = False):
        self._clock         = clock
        self._hours         = tradingHoursUTC
        self._force         = forceActive
        self.isActive       = False
        self.onSessionStart = None
        self.onSessionEnd   = None
        self._running       = False
        self._task          = None

    @staticmethod
    def _fxOpen(t) -> bool:
        wd, tt = t.weekday(), t.time()
        if wd == 5:
            return False
        if wd == 4 and tt >= FX_CLOSE_UTC:
            return False
        if wd == 6 and tt < FX_CLOSE_UTC:
            return False
        return True

    def update(self) -> None:
        now = self._clock.now()
        if now is None:
            return
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc)

        if self._force:
            active = True
        else:
            active = self._fxOpen(now)
            if active and self._hours:
                lo, hi = self._hours
                active = lo <= now.time() < hi

        if active and not self.isActive:
            self.isActive = True
            self._fire(self.onSessionStart)
        elif not active and self.isActive:
            self.isActive = False
            self._fire(self.onSessionEnd)

    def _fire(self, callback) -> None:
        if not callback:
            return
        try:
            if asyncio.iscoroutinefunction(callback):
                asyncio.get_running_loop().create_task(callback())
            else:
                callback()
        except RuntimeError:
            log.error("Session callback skipped: no running event loop.")
        except Exception as e:
            log.error("Session callback failed: %s", e)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._runLoop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _runLoop(self) -> None:
        while self._running:
            self.update()
            await asyncio.sleep(1)
