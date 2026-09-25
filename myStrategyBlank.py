"""
Your strategy. Empty on purpose.

The contract, in full:

    compute(conIds, prices) -> (targets, confidences)

    conIds       int array, length n. Stable order for the whole run.
    prices       float64 array, length n. Mid price, or NaN if that instrument
                 has not printed within staleLimit seconds of this sample.
    targets      int array, length n. The position you want to HOLD in each
                 instrument, in units of its base currency. Signed. Absolute,
                 not a change. The value HOLD (import it from signalSource)
                 means "no opinion": whatever is held is left alone.
    confidences  float32 array, length n, 0.0 to 1.0. Currently only logged.

Called once every sampleInterval seconds (1s), on the first tick at or after each
grid point, so no sample fires while the market is shut.

Four things that will bite you if you ignore them:

  * Targets are absolute. Return the same number twice and nothing is ordered;
    the core diffs your target against inventory plus in-flight orders. So you
    never track your own position, and you never send an order twice.

  * NaN means invisible, not zero. Returning 0 for a pair that went quiet will
    liquidate it and pay the spread for no reason. Hold your previous target
    instead unless you actually want out. Your thin crosses are stale roughly
    a quarter of the time.

  * Return HOLD while you are warming up. You cannot see your own inventory, so
    0 is not "leave it alone" — it is "sell everything". A strategy that returns
    0 during warm-up liquidates the book every time the process restarts.

  * Risk is not your job. RiskGate handles position caps, order caps, minimum
    size, margin and the session. Ask for what you want; it will clip it.

Sizing: minOrderQty is 20,000 units and maxOrderNotional is 25,000 USD, so a
target under 20,000 units cannot be reached from flat and will be refused.

Cost floor: $2.00 per order. On 20,000-25,000 units that is over 1 bp round trip
for every pair in the universe, and more like 2.5 bp where the base currency is
cheap. That is commission alone, before spread. Your signal has to beat it.
"""
import numpy as np

from signalSource import SignalSource, HOLD


class MyStrategy(SignalSource):

    def __init__(self, size=25_000):
        self.size = int(size)

    def compute(self, conIds, prices):
        n           = conIds.size
        targets     = np.full(n, HOLD, dtype=np.int64)   # no opinion until you form one
        confidences = np.zeros(n, dtype=np.float32)

        # your logic here

        return targets, confidences