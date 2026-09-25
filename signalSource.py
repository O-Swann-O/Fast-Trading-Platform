import numpy as np

# A target of HOLD means "no opinion for this instrument": the core leaves whatever is
# held alone, exactly as it does for a stale price. It exists because a strategy cannot
# see its own inventory, so during warm-up — or after a restart — it has no honest way to
# ask for the position it already has. Returning 0 there would flatten the book.
HOLD = -2**31


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
        self._lastIds = None

    def _reset(self, conIds: np.ndarray) -> None:
        self._keys  = conIds.astype(np.uint32).copy()
        self._buf   = np.full((conIds.size, self.lookback), np.nan, dtype=np.float64)
        self._head  = 0
        self._count = 0

    def compute(self, conIds: np.ndarray, prices: np.ndarray):
        if conIds is not self._lastIds:
            conIds = np.asarray(conIds)
            if (self._keys is None
                    or self._keys.size != conIds.size
                    or not np.array_equal(self._keys, conIds.astype(np.uint32))):
                self._reset(conIds)
            self._lastIds = conIds
        prices = np.asarray(prices, dtype=np.float64)

        self._buf[:, self._head] = prices
        self._head  = (self._head + 1) % self.lookback
        self._count = min(self._count + 1, self.lookback)

        n = conIds.size
        targets     = np.full(n, HOLD, dtype=np.int64)   # a placeholder must never trade
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
        targets     = np.full(n, HOLD, dtype=np.int64)   # untouched unless named
        confidences = np.zeros(n, dtype=np.float32)
        for i, cid in enumerate(conIds):
            if int(cid) in self._targets and not np.isnan(prices[i]):
                targets[i]     = self._targets[int(cid)]
                confidences[i] = self._confidence
        return targets, confidences
