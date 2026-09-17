"""
Slow graph placeholder: fit currency potentials from every visible pair, trade the
pairs that sit furthest from what the whole graph implies.

This is a working skeleton with no claim to edge. It exists so the graph machinery —
the fit, the residual, the neutrality property, the cost budget — is in front of you
in runnable form.

THE IDEA

Eight currencies, 28 pairs. Give each currency a potential phi. A pair quoted as
base/quote should satisfy

    log(price) = phi[base] - phi[quote]

Stack one row per visible pair into A (+1 at base, -1 at quote) and solve A phi = y
in least squares. Two useful things fall out:

  phi   the market's view of each currency, from all pairs at once. Seven free
        numbers (one is an arbitrary level), estimated from up to 28 observations,
        so it stays well-posed even with pairs missing. Your freshness run put the
        visible graph connected 94.63% of samples.

  r     the residual, y - A phi: how far each pair sits from the graph consensus.
        This is the cycle-space component, the part no set of potentials explains.

WHY THE RESIDUAL IS THE INTERESTING OBJECT

A^T r = 0 exactly, for the least-squares fit. So a position vector proportional to r
has zero net exposure to every currency, by construction rather than by hedging. Trade
the whole residual vector and you are currency-neutral automatically.

This placeholder does NOT do that: holding all 28 legs costs 28 x $2 every rebalance,
which your cost budget cannot carry. It takes the few largest residuals instead, and
accepts the resulting currency exposure. Whether to pay for full neutrality is a real
decision and it is yours.

WHY IT IS SLOW

Measured on your data: a round trip costs about 3.1 bps on a 25,000-unit position,
$4 commission plus roughly $3.80 of spread. Ten round trips a day across all 28 pairs
is about 2% of equity a month. So this holds for hours, not seconds: four-hour minimum,
one-day maximum, and at most a handful of positions at once.

A raw instantaneous residual is mostly stale quotes and spread, not signal — a leg read
six seconds late manufactures a residual that is not there. So the residual is
standardised against its own multi-hour history and only wide, persistent deviations
are traded.

WHAT TO REPLACE

_score() is the signal. Everything else is plumbing you can keep.
"""
import numpy as np

import logSetup
from signalSource import RingBufferSource


class MyStrategy(RingBufferSource):

    def __init__(self, size=25_000, lookback=4320, entry=3.0, exitZ=0.5,
                 minHold=2880, maxHold=17280, maxPositions=4, minSamples=1440):
        super().__init__(lookback)
        self.size         = int(size)
        self.entry        = float(entry)      # open beyond this many sigma
        self.exitZ        = float(exitZ)      # close once back inside this
        self.minHold      = int(minHold)      # samples; 2880 @ 5s = 4 hours
        self.maxHold      = int(maxHold)      # samples; 17280 @ 5s = 24 hours
        self.maxPositions = int(maxPositions)
        self.minSamples   = int(minSamples)   # warm-up before trading

        self._ids     = None
        self._A       = None                  # [nPairs, nCcy] incidence matrix
        self._ccys    = []
        self._pinv    = {}                    # visibility mask -> pinv(A[mask])
        self._res     = None                  # residual ring buffer
        self._head    = 0
        self._filled  = 0
        self._sum     = None                  # rolling sums, so the window stats are
        self._sumsq   = None                  # O(nPairs) per sample, not O(nPairs*lookback)
        self._cnt     = None
        self._since   = 0
        self._target  = None
        self._age     = None

    # ------------------------------------------------------------------ graph
    def _buildGraph(self, conIds):
        """Incidence matrix from the conId -> 'EURUSD' names registered at setup."""
        names = [logSetup.name(int(c)) for c in conIds]
        ccys  = sorted({n[:3] for n in names if len(n) == 6}
                       | {n[3:] for n in names if len(n) == 6})
        node  = {c: i for i, c in enumerate(ccys)}
        A = np.zeros((len(names), len(ccys)))
        for i, n in enumerate(names):
            if len(n) == 6 and n[:3] in node and n[3:] in node:
                A[i, node[n[:3]]] = 1.0
                A[i, node[n[3:]]] = -1.0
        self._ids, self._A, self._ccys = conIds, A, ccys
        self._pinv = {}
        n = len(names)
        self._res    = np.full((n, self.lookback), np.nan)
        self._head   = 0
        self._filled = 0
        self._sum    = np.zeros(n)
        self._sumsq  = np.zeros(n)
        self._cnt    = np.zeros(n, dtype=np.int64)
        self._since  = 0
        self._target = np.zeros(n, dtype=np.int64)
        self._age    = np.zeros(n, dtype=np.int64)

    def _fit(self, y, visible):
        """Least-squares potentials over the visible edges; returns fitted log prices."""
        A   = self._A[visible]
        if A.shape[0] < len(self._ccys) - 1:      # too few edges to pin the graph
            return None
        key = visible.tobytes()
        P   = self._pinv.get(key)
        if P is None:
            P = np.linalg.pinv(A)
            if len(self._pinv) < 4096:            # visibility patterns repeat a lot
                self._pinv[key] = P
        return A @ (P @ y[visible])

    # ----------------------------------------------------------------- signal
    def _score(self, conIds, prices):
        """Standardised graph residual per pair; NaN where not scoreable.

        Replace this. The residual is one choice; the potentials themselves are the
        other, and they measure something different — a currency moving as a whole
        rather than one pair drifting from the consensus.
        """
        y       = np.full(prices.size, np.nan)
        ok      = ~np.isnan(prices) & (prices > 0)
        y[ok]   = np.log(prices[ok])
        visible = ok & self._A.any(axis=1)

        fitted = self._fit(y, visible)
        r      = np.full(prices.size, np.nan)
        if fitted is not None:
            r[visible] = y[visible] - fitted

        self._push(r)
        if self._filled < self.minSamples:
            return np.full(prices.size, np.nan)

        with np.errstate(invalid="ignore", divide="ignore"):
            n  = np.maximum(self._cnt, 1)
            mu = self._sum / n
            var = np.maximum(self._sumsq / n - mu * mu, 0.0)
            sd = np.sqrt(var)
            z  = (r - mu) / sd
        return np.where((self._cnt >= self.minSamples // 4) & (sd > 0), z, np.nan)

    def _push(self, r):
        """Add r to the ring, evicting the oldest column, keeping running sums."""
        old = self._res[:, self._head]
        gone = ~np.isnan(old)
        if gone.any():
            self._sum[gone]   -= old[gone]
            self._sumsq[gone] -= old[gone] * old[gone]
            self._cnt[gone]   -= 1
        new = ~np.isnan(r)
        if new.any():
            self._sum[new]   += r[new]
            self._sumsq[new] += r[new] * r[new]
            self._cnt[new]   += 1
        self._res[:, self._head] = r
        self._head   = (self._head + 1) % self.lookback
        self._filled = min(self._filled + 1, self.lookback)

        # running sums drift over millions of updates; rebuild once per window
        self._since += 1
        if self._since >= self.lookback:
            self._since = 0
            hist = self._res if self._filled >= self.lookback else self._res[:, :self._filled]
            with np.errstate(invalid="ignore"):
                self._sum[:]   = np.nansum(hist, axis=1)
                self._sumsq[:] = np.nansum(hist * hist, axis=1)
                self._cnt[:]   = np.sum(~np.isnan(hist), axis=1)

    # --------------------------------------------------------------- plumbing
    def compute(self, conIds, prices):
        super().compute(conIds, prices)
        conIds = np.asarray(conIds)
        prices = np.asarray(prices, dtype=np.float64)
        if self._ids is None or not np.array_equal(self._ids, conIds):
            self._buildGraph(conIds)

        z      = self._score(conIds, prices)
        usable = ~np.isnan(z)
        target = self._target.copy()
        held   = target != 0
        self._age[held] += 1
        self._age[~held] = 0

        # close: signal spent and held long enough, or held too long either way
        done   = held & (self._age >= self.maxHold)
        spent  = held & usable & (np.abs(z) < self.exitZ) & (self._age >= self.minHold)
        target = np.where(done | spent, 0, target)

        # open the widest few, subject to the position cap
        room = self.maxPositions - int(np.count_nonzero(target))
        if room > 0:
            cand = np.where(usable & (target == 0) & (np.abs(z) >= self.entry))[0]
            if cand.size:
                pick = cand[np.argsort(-np.abs(z[cand]))[:room]]
                target[pick] = (-np.sign(z[pick]) * self.size).astype(np.int64)
                self._age[pick] = 0

        self._target = target
        conf = np.zeros(prices.size, dtype=np.float32)
        np.copyto(conf, np.minimum(np.abs(np.nan_to_num(z)) / self.entry, 1.0).astype(np.float32),
                  where=usable)
        return target.astype(np.int32), conf