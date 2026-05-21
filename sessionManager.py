import asyncio
import logging
from datetime import datetime, time

log = logging.getLogger(__name__)


class SessionManager:

    def __init__(self, startTime: time, endTime: time) -> None:
        self.startTime      = startTime
        self.endTime        = endTime
        self.isActive       = False
        self.onSessionStart = None
        self.onSessionEnd   = None
        self._running       = False
        self._task          = None

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._runLoop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _runLoop(self) -> None:
        while self._running:
            now           = datetime.now()
            currentTime   = now.time()
            isWeekday     = now.weekday() < 5
            isWithinHours = isWeekday and (self.startTime <= currentTime < self.endTime)

            if isWithinHours and not self.isActive:
                self.isActive = True
                await self._fire(self.onSessionStart)
                
            elif not isWithinHours and self.isActive:
                self.isActive = False
                await self._fire(self.onSessionEnd)

            await asyncio.sleep(1)

    async def _fire(self, callback) -> None:
        if not callback:
            return
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback()
            else:
                callback()
        except Exception as e:
            log.error("Session error: %s", e)