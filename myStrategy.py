r"""
Slow currency-potential strategy.

The cycle space turned out to be noise at your resolution: residuals decayed 80% in
30 seconds while fading them earned 0.13 bp against a 3.1 bp cost. This works in the
other subspace. Same fit, opposite half of the decomposition.

    log(price_e) = phi[base] - phi[quote] + r_e
                   \_______  _________/    \_/
                           \/            the part that was noise
                    the part with the variance in it

phi is solved from all 28 pairs at once, and because the pseudoinverse picks the
mean-zero gauge, phi[c] is exactly how strong currency c is against the basket of the
other seven. Cross-sectional by construction, no numeraire to choose.

THE SIGNAL is the oldest idea in FX: currencies trend. Score each currency by a fast
EMA of its potential minus a slow one, go long the strongest against the weakest, and
hold. Nothing clever. The point is to find out whether anything at this speed clears
your costs, because the measurements say nothing faster can.

WHY SO SLOW. At 25,000 units a round trip costs about 3.1 bp. To want the edge at
roughly triple the cost you need ~9 bp a trade, which is most of a 30-minute move,
a sixth of a daily move, and under a tenth of a weekly one. The arithmetic only works
at multi-day holds. Rebalancing weekly with 2 positions is 104 round trips a year,
about 0.4% of equity in costs.

BEFORE YOU RUN IT. maxOrderNotional = 25,000 blocks all six GBP-base pairs and
CHFJPY, because 25,000 USD buys fewer than the 20,000-unit minimum. That is a quarter
of the graph, including every direct route to GBP. Raise it to 28,000 or this
strategy will silently skip whole currencies.

Needs months, not weeks: with a 20-day slow EMA and a 40-day warm-up, a one-month
backtest never places a trade.
"""
import atexit
import sys
from collections import Counter, deque

import numpy as np

import config
import logSetup
from signalSource import SignalSource

def _resolveInterval(source):
    """The sample interval actually in force.

    config.sampleInterval is the LIVE value; the backtest profile overrides it, so
    reading config gives the wrong number in a backtest and every day-based parameter
    comes out wrong by that ratio. Find the core that owns this strategy instead.
    Scanning sys.modules rather than importing by name matters because `python
    backtestMain.py` registers that module as __main__, not as backtestMain.
    """
    for mod in list(sys.modules.values()):
        try:
            core = getattr(mod, "core", None)
            if core is not None and getattr(core, "_source", None) is source:
                iv = getattr(core, "_interval", None)
                if iv:
                    return float(iv), True
        except Exception:
            continue
    return float(config.sampleInterval), False


class MyStrategy(SignalSource):

    def __init__(self, size=25_000, fastDays=3.0, slowDays=20.0, warmupDays=40.0,
                 rebalanceDays=5.0, nPairs=2, minScore=0.0, delaySamples=0):
        self.size      = int(size)
        self.nPairs    = int(nPairs)
        self.minScore  = float(minScore)     # skip a rebalance if the spread is thin
        self._days     = (fastDays, slowDays, warmupDays, rebalanceDays)
        self._perDay   = None                # resolved on the first sample

        # Lookahead test. delaySamples holds every target back that many samples
        # before it is acted on, so the strategy trades on information it provably
        # had earlier. A real signal decays gently; one that is reading the future
        # collapses. Compare delaySamples=0 against 12 (a minute) and 60 (five).
        self._delay    = int(delaySamples)
        self._queue    = deque()

        self._ids = None
        self._n = 0
        self.rebalances = 0
        self.picks = Counter()
        _LIVE.append(self)

    # ------------------------------------------------------------------ graph
    def _build(self, conIds):
        names = [logSetup.name(int(c)) for c in conIds]
        ccys  = sorted({n[:3] for n in names if len(n) == 6} |
                       {n[3:] for n in names if len(n) == 6})
        node  = {c: i for i, c in enumerate(ccys)}
        A = np.zeros((len(names), len(ccys)))
        route = {}                                   # (from, to) -> (pair index, sign)
        for i, n in enumerate(names):
            if len(n) != 6:
                continue
            b, q = n[:3], n[3:]
            A[i, node[b]], A[i, node[q]] = 1.0, -1.0
            route[(b, q)] = (i, +1)                  # long b vs q  -> BUY  this pair
            route[(q, b)] = (i, -1)                  # long q vs b  -> SELL this pair
        self._ids, self._A, self.names, self.ccys, self._route = conIds, A, names, ccys, route
        fast, slow, warm, reb = self._days
        interval, found = _resolveInterval(self)
        if not found:
            print(f"WARNING: could not find the running core; assuming a "
                  f"{interval:g}s sample interval. Day-based parameters will be wrong "
                  f"if the profile differs.")
        self._interval = interval
        self._perDay = 86_400.0 / interval
        self._aFast  = 1.0 - 0.5 ** (1.0 / max(fast * self._perDay, 1.0))
        self._aSlow  = 1.0 - 0.5 ** (1.0 / max(slow * self._perDay, 1.0))
        self._warmup = int(warm * self._perDay)
        self._every  = max(int(reb * self._perDay), 1)
        self._pinv  = {}
        self._fast  = None
        self._slow  = None
        self._target = np.zeros(len(names), dtype=np.int64)

    def _fit(self, y, visible):
        A = self._A[visible]
        if A.shape[0] < len(self.ccys) - 1:
            return None
        key = visible.tobytes()
        P = self._pinv.get(key)
        if P is None:
            P = np.linalg.pinv(A)
            if len(self._pinv) < 4096:
                self._pinv[key] = P
        return P @ y[visible]

    # ----------------------------------------------------------------- signal
    def compute(self, conIds, prices):
        conIds = np.asarray(conIds)
        prices = np.asarray(prices, dtype=np.float64)
        if self._ids is None or not np.array_equal(self._ids, conIds):
            self._build(conIds)
        conf = np.zeros(prices.size, dtype=np.float32)

        ok = ~np.isnan(prices) & (prices > 0)
        y  = np.full(prices.size, np.nan)
        y[ok] = np.log(prices[ok])
        phi = self._fit(y, ok)
        if phi is None:                              # graph disconnected: hold
            return self._target.astype(np.int32), conf

        if self._fast is None:
            self._fast, self._slow = phi.copy(), phi.copy()
        else:
            self._fast += self._aFast * (phi - self._fast)
            self._slow += self._aSlow * (phi - self._slow)
        self._n += 1

        if self._n < self._warmup or self._n % self._every:
            self._target = self._release(None)
            return self._target.astype(np.int32), conf

        # cross-sectional trend: how far each currency has pulled away from its own
        # slow level, relative to the basket
        score = self._fast - self._slow
        order = np.argsort(-score)                   # strongest first
        target = np.zeros(prices.size, dtype=np.int64)
        used, taken = set(), 0
        for k in range(len(self.ccys) // 2):
            if taken >= self.nPairs:
                break
            strong, weak = self.ccys[order[k]], self.ccys[order[-1 - k]]
            if strong in used or weak in used:
                continue
            if score[order[k]] - score[order[-1 - k]] < self.minScore:
                break
            hit = self._route.get((strong, weak))
            if hit is None:
                continue
            i, sign = hit
            if np.isnan(prices[i]):                  # cannot price it now; skip
                continue
            target[i] = sign * self.size
            conf[i]   = 1.0
            used.update((strong, weak))
            taken += 1
            self.picks[f"{'+' if sign > 0 else '-'}{self.names[i]}"] += 1

        self.rebalances += 1
        self._target = self._release(target)
        return self._target.astype(np.int32), conf

    def _release(self, newTarget):
        """Hold a new target for delaySamples before acting on it."""
        if self._delay <= 0:
            return self._target if newTarget is None else newTarget
        if newTarget is not None:
            self._queue.append((self._n + self._delay, newTarget))
        while self._queue and self._queue[0][0] <= self._n:
            self._target = self._queue.popleft()[1]
        return self._target

    # ----------------------------------------------------------------- report
    def report(self):
        if not self.rebalances:
            perDay = max(self._perDay or 1.0, 1.0)
            have   = self._n / perDay
            need   = self._warmup / perDay
            print(f"\nNo rebalance happened.")
            print(f"  warm-up needs {need:.0f} trading days; this range gave {have:.1f}.")
            print(f"  FX trades about 5 days a week, so allow ~{need*7/5:.0f} calendar days "
                  f"before the first trade, plus however long you want to measure.")
            print(f"  (sampling resolved to {86_400/perDay:.0f}s)")
            return
        print(f"\n{'='*60}\nSLOW POTENTIALS\n{'='*60}")
        print(f"rebalances {self.rebalances:,}   every "
              f"{self._every/self._perDay:.1f} days   {self.nPairs} pair(s) held   "
              f"sampling {self._interval:.0f}s"
              + (f"   DELAYED {self._delay} samples"
                 f" ({self._delay*86_400/self._perDay/60:.0f} min)" if self._delay else ""))
        print("most-selected legs (+ = long the pair, - = short):")
        for leg, n in self.picks.most_common(10):
            print(f"  {leg:>9s} {n:>5,}  ({n/self.rebalances:.0%} of rebalances)")


_LIVE = []


def _emit():
    for s in _LIVE:
        try:
            s.report()
        except Exception as e:
            print(f"(report unavailable: {e})")


atexit.register(_emit)