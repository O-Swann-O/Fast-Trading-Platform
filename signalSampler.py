import asyncio
import logging
from datetime import timezone

import numpy as np

from clock import Clock
from signalSource import SignalSource

log = logging.getLogger(__name__)


class SignalSampler:

    def __init__(self, source: SignalSource, clock: Clock, conIds: list,
                 sampleInterval: float = 0.1, staleLimit: float = 5.0,
                 maxCatchup: int = 1000):
        self._source     = source
        self._clock      = clock
        self._conIds     = np.array(conIds, dtype=np.uint32)
        self._interval   = sampleInterval
        self._staleLimit = staleLimit
        self._maxCatchup = maxCatchup

        n = self._conIds.size
        self._index      = {int(cid): i for i, cid in enumerate(self._conIds)}
        self._latestMid  = np.full(n, np.nan, dtype=np.float32)
        self._lastUpdate = np.full(n, -np.inf)
        self._nextSample = None

        self.onTargetPosition = None
        self._running = False
        self._task    = None

    def onTick(self, conId: int, price: float) -> None:
        i = self._index.get(conId)
        if i is None:
            return
        self._latestMid[i]  = price
        self._lastUpdate[i] = self._epoch()

    def _epoch(self) -> float:
        now = self._clock.now()
        if now is None:
            return -np.inf
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.timestamp()

    def poll(self) -> None:
        if self._clock.now() is None:
            return
        nowS = self._epoch()

        if self._nextSample is None:
            self._nextSample = nowS + self._interval
            return

        if (nowS - self._nextSample) / self._interval > self._maxCatchup:
            self._nextSample = nowS + self._interval
            self._sample(nowS)
            return

        while nowS >= self._nextSample:
            self._nextSample += self._interval
            self._sample(nowS)

    def _sample(self, nowS: float) -> None:
        stale  = (nowS - self._lastUpdate) > self._staleLimit
        prices = self._latestMid.copy()
        prices[stale] = np.nan

        targets, confidences = self._source.compute(self._conIds, prices)

        emitTs = self._clock.timestamp()
        if self.onTargetPosition:
            for i in range(self._conIds.size):
                if stale[i]:
                    continue
                self.onTargetPosition(int(self._conIds[i]), int(targets[i]),
                                      float(confidences[i]), emitTs)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task    = asyncio.create_task(self._runLoop())
            log.info("Sampler started: %d instruments every %.1fs.",
                     self._conIds.size, self._interval)

    def stop(self) -> None:
        if self._running:
            log.info("Sampler stopped.")
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _runLoop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                self.poll()
            except Exception as e:
                log.error("Sampler failed: %s", e)
