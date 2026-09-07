import numpy as np
from signalSource import SignalSource


class MyStrategy(SignalSource):

    def __init__(self, size=25_000):
        self.size = size

    def compute(self, conIds, prices):
        n = conIds.size
        targets     = np.zeros(n, dtype=np.int32)
        confidences = np.zeros(n, dtype=np.float32)
        # your logic here
        return targets, confidences