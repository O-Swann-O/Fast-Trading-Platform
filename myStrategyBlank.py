"""
Your strategy. Empty on purpose.

The contract, in full:

    compute(conIds, prices) -> (targets, confidences)

    conIds       int array, length n. Stable order for the whole run.
    prices       float64 array, length n. Mid price, or NaN if that instrument
                 has not printed within staleLimit seconds of this sample.
    targets      int32 array, length n. The position you want to HOLD in each
                 instrument, in units of its base currency. Signed. Absolute,
                 not a change.
    confidences  float32 array, length n, 0.0 to 1.0. Currently only logged.

Called every sampleInterval seconds (5s for FX) while the session is open.

Three things that will bite you if you ignore them:

  * Targets are absolute. Return the same number twice and nothing is ordered;
    the core diffs your target against inventory plus in-flight orders. So you
    never track your own position, and you never send an order twice.

  * NaN means invisible, not zero. Returning 0 for a pair that went quiet will
    liquidate it and pay the spread for no reason. Hold your previous target
    instead unless you actually want out. Your thin crosses are stale roughly
    a quarter of the time.

  * Risk is not your job. RiskGate handles position caps, order caps, minimum
    size, margin and the session. Ask for what you want; it will clip it.

Sizing: minOrderQty is 20,000 units and maxOrderNotional is 25,000 USD, so a
target under 20,000 units cannot be reached from flat and will be refused.

Cost floor: $2.00 per order, which on a 25,000-unit position is about 0.8 bps
round trip. Your signal has to beat that before anything else.
"""
import numpy as np

from signalSource import SignalSource


class MyStrategy(SignalSource):

    def __init__(self, size=25_000):
        self.size = int(size)

    def compute(self, conIds, prices):
        n           = conIds.size
        targets     = np.zeros(n, dtype=np.int32)
        confidences = np.zeros(n, dtype=np.float32)

        # your logic here

        return targets, confidences