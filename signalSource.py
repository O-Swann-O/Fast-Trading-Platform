import numpy as np


class SignalSource:

    def compute(self, conIds: np.ndarray, prices: np.ndarray):
        raise NotImplementedError


class RingBufferSource(SignalSource):

    def __init__(self, lookback: int = 600):
        self.lookback = lookback
        self._keys    = None
        self._buf     = None
        self._head    = 0
        self._count   = 0

    def _reset(self, conIds: np.ndarray) -> None:
        self._keys  = conIds.astype(np.uint32).copy()
        self._buf   = np.full((conIds.size, self.lookback), np.nan, dtype=np.float32)
        self._head  = 0
        self._count = 0

    def compute(self, conIds: np.ndarray, prices: np.ndarray):
        conIds = np.asarray(conIds)
        prices = np.asarray(prices, dtype=np.float32)

        if (self._keys is None
                or self._keys.size != conIds.size
                or not np.array_equal(self._keys, conIds.astype(np.uint32))):
            self._reset(conIds)

        self._buf[:, self._head] = prices
        self._head  = (self._head + 1) % self.lookback
        self._count = min(self._count + 1, self.lookback)

        n = conIds.size
        targets     = np.zeros(n, dtype=np.int32)
        confidences = np.zeros(n, dtype=np.float32)
        return targets, confidences

    def window(self) -> np.ndarray:
        if self._count < self.lookback:
            return self._buf[:, :self._count]
        return np.roll(self._buf, -self._head, axis=1)


class FixedTargetSource(SignalSource):

    def __init__(self, targets: dict, confidence: float = 1.0):
        self._targets    = targets
        self._confidence = confidence

    def compute(self, conIds: np.ndarray, prices: np.ndarray):
        conIds      = np.asarray(conIds)
        n           = conIds.size
        targets     = np.zeros(n, dtype=np.int32)
        confidences = np.zeros(n, dtype=np.float32)
        for i, cid in enumerate(conIds):
            if int(cid) in self._targets and not np.isnan(prices[i]):
                targets[i]     = self._targets[int(cid)]
                confidences[i] = self._confidence
        return targets, confidences