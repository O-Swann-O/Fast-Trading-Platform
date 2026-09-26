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

Called once per config.sampleInterval, live and in backtest alike, on the first tick
at or after each grid point. With no ticks there are no calls. After a quiet spell
the missed grid points are called back to back on the next tick, all prices NaN; a
gap longer than 1,000 intervals (a weekend) collapses into a single call.

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

Sizing: with config.minOrderQty at 20,000 units and config.maxOrderNotional at
25,000 USD, a target under 20,000 units cannot be reached from flat, GBP-base pairs
cannot trade at all (20,000 GBP is more than 25,000 USD), and CHFJPY only while
20,000 CHF stays under it.

Cost floor: $2.00 per order, $4 per round trip. That is 1.6 bp at the 25,000 USD
order cap, rising to 3.4 bp for a minimum-size NZD-base order. Commission alone,
before spread. Your signal has to beat it.
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