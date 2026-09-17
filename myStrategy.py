"""
Currently in this file: the impulse-response measurement.

It does not trade. It fits the currency graph at every sample, finds pairs that
dislocate from it, and records what happens over the following half hour, so you can
see whether the dislocated pair comes back, whether the pairs left behind catch up,
or whether neither happens.

Run it the normal way:

    python backtestMain.py --from 2025-06-01 --to 2025-07-01

The report prints after the backtest summary. Nothing is recorded to results/, since
there are no fills to record.

Tune it with the constants below. Lowering Z_THRESHOLD gives more events and more
noise; raising MAXLAG looks further out but needs a longer run to fill the tail.

The maths, briefly. Each currency gets a potential phi; a pair quoted base/quote
should satisfy log(price) = phi[base] - phi[quote]. Stack that into A (+1 at base,
-1 at quote) and solve A phi = y in least squares. The residual r = y - A phi is the
part no set of currency values explains, and A^T r = 0 exactly, so r lives in the
21-dimensional cycle space. An event is an edge whose residual deviates from its own
recent average by more than Z_THRESHOLD sigma, and is the largest such deviation on
the graph at that instant.

What decays is the deviation from the edge's own average, not the raw residual: a
pair can carry a persistent bias from its spread that will never revert.
"""
import atexit
from collections import deque

import numpy as np

import logSetup
from signalSource import SignalSource

# ---------------------------------------------------------------- parameters
Z_THRESHOLD = 3.0      # sigma; an event fires above this
MAXLAG      = 360      # samples tracked forward (360 @ 5s = 30 min)
LOOKBACK    = 4320     # samples of residual history behind the z-score (6 h)
WARMUP      = 1440     # samples before any event is taken (2 h)
REFRACTORY  = 360      # samples before the same edge may fire again
COST_BP     = 3.1      # round-trip cost on your data, for the hurdle line


class MyStrategy(SignalSource):
    """Never trades. Fits the graph, detects dislocations, tracks what follows."""

    def __init__(self):
        self.n = MAXLAG + 1
        # accumulators: [lag] -> running sums, for the shocked edge and the laggard
        self.ownPnl   = np.zeros(self.n);  self.ownRes = np.zeros(self.n)
        self.nbrPnl   = np.zeros(self.n)
        self.ctlPnl   = np.zeros(self.n)
        self.ownBp    = np.zeros(self.n)   # raw move in bp: what you would actually earn
        self.nbrBp    = np.zeros(self.n)
        self.ctlBp    = np.zeros(self.n)
        self.count    = np.zeros(self.n)
        self.ctlCount = np.zeros(self.n)
        self.resCount = np.zeros(self.n)
        self.nbrCount = np.zeros(self.n)
        self.byHour   = np.zeros(24, dtype=np.int64)
        self.events   = 0
        self.samples  = 0

        self._ids = None
        self.clock = None
        _LIVE.append(self)
        self._rng = np.random.default_rng(0)
        self._pending = deque()
        self._ctlPending = deque()
        self._lastFire = None

    # ---------------------------------------------------------------- graph
    def _build(self, conIds):
        names = [logSetup.name(int(c)) for c in conIds]
        ccys  = sorted({n[:3] for n in names if len(n) == 6} |
                       {n[3:] for n in names if len(n) == 6})
        node  = {c: i for i, c in enumerate(ccys)}
        A = np.zeros((len(names), len(ccys)))
        for i, n in enumerate(names):
            if len(n) == 6:
                A[i, node[n[:3]]], A[i, node[n[3:]]] = 1.0, -1.0
        self._ids, self._A, self.names, self.ccys = conIds, A, names, ccys
        m = len(names)
        self._pinv   = {}
        self._res    = np.full((m, LOOKBACK), np.nan)
        self._head   = 0
        self._filled = 0
        self._sum    = np.zeros(m); self._sumsq = np.zeros(m); self._cnt = np.zeros(m, np.int64)
        self._lastFire = np.full(m, -10**9, dtype=np.int64)
        # which edges share a currency with each edge
        self._adj = np.zeros((m, m), dtype=bool)
        for i, a in enumerate(names):
            for j, b in enumerate(names):
                if i != j and ({a[:3], a[3:]} & {b[:3], b[3:]}):
                    self._adj[i, j] = True

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
        return A @ (P @ y[visible])

    def _push(self, r):
        old = self._res[:, self._head]
        g = ~np.isnan(old)
        if g.any():
            self._sum[g] -= old[g]; self._sumsq[g] -= old[g]*old[g]; self._cnt[g] -= 1
        nw = ~np.isnan(r)
        if nw.any():
            self._sum[nw] += r[nw]; self._sumsq[nw] += r[nw]*r[nw]; self._cnt[nw] += 1
        self._res[:, self._head] = r
        self._head = (self._head + 1) % LOOKBACK
        self._filled = min(self._filled + 1, LOOKBACK)

    # --------------------------------------------------------------- driver
    def compute(self, conIds, prices):
        conIds = np.asarray(conIds)
        prices = np.asarray(prices, dtype=np.float64)
        if self._ids is None or not np.array_equal(self._ids, conIds):
            self._build(conIds)
            if self.clock is None:
                try:
                    import backtestMain as bm
                    self.clock = bm.clock
                except Exception:
                    pass
        m = prices.size
        zeros = (np.zeros(m, np.int32), np.zeros(m, np.float32))

        ok = ~np.isnan(prices) & (prices > 0)
        y  = np.full(m, np.nan); y[ok] = np.log(prices[ok])
        fitted = self._fit(y, ok)
        r = np.full(m, np.nan)
        if fitted is not None:
            r[ok] = y[ok] - fitted

        self._push(r)
        self.samples += 1
        t = self.samples

        # advance every tracked event by one lag
        for store, pend in ((self.ownPnl, self._pending), (self.ctlPnl, self._ctlPending)):
            for ev in list(pend):
                lag = t - ev["t"]
                if lag > MAXLAG:
                    pend.remove(ev); continue
                k, s0, size = ev["edge"], ev["sign"], ev["size"]
                raw = -s0 * (y[k] - ev["y0"][k]) * 1e4          # bp actually earned
                pnl = raw / (size * 1e4)                        # fraction of the dislocation
                if ev["kind"] == "event":
                    if np.isfinite(pnl):
                        self.ownPnl[lag] += pnl
                        self.ownBp[lag]  += raw
                    # what decays is the deviation from this edge's own typical
                    # residual, not the residual itself: a pair can carry a
                    # persistent bias that is never going to mean-revert
                    dev = (r[k] - ev["mu0"][k]) / ev["dev0"]
                    if np.isfinite(dev):
                        self.ownRes[lag] += dev
                        self.resCount[lag] += 1
                    j = ev["nbr"]
                    if j >= 0:
                        nraw = -ev["nbrSign"] * (y[j] - ev["y0"][j]) * 1e4
                        if np.isfinite(nraw):
                            self.nbrBp[lag]    += nraw
                            self.nbrCount[lag] += 1
                    self.count[lag] += 1
                else:
                    if np.isfinite(raw):
                        self.ctlBp[lag]    += raw
                        self.ctlCount[lag] += 1

        if self._filled < WARMUP:
            return zeros

        with np.errstate(invalid="ignore", divide="ignore"):
            n  = np.maximum(self._cnt, 1)
            mu = self._sum / n
            sd = np.sqrt(np.maximum(self._sumsq / n - mu*mu, 0.0))
            z  = (r - mu) / sd
        z = np.where((self._cnt >= WARMUP // 4) & (sd > 0), z, np.nan)

        # control: a random edge carrying an ordinary residual. Normalising by a
        # near-zero denominator would make this explode, so require a real one.
        if self._rng.random() < 0.01:
            ctl = np.where(~np.isnan(z) & (np.abs(z) > 0.5) & (np.abs(z) < 1.5))[0]
            if ctl.size:
                k = int(self._rng.choice(ctl))
                d = r[k] - mu[k]
                if np.isfinite(d) and abs(d) > 0:
                    self._ctlPending.append(dict(kind="ctl", t=t, edge=k,
                                                 sign=np.sign(d), size=abs(d),
                                                 y0=y.copy()))

        usable = ~np.isnan(z)
        if not usable.any():
            return zeros
        k = int(np.nanargmax(np.abs(np.where(usable, z, np.nan))))
        if abs(z[k]) < Z_THRESHOLD or t - self._lastFire[k] < REFRACTORY:
            return zeros

        # the laggard: among edges sharing a currency, the one pulled furthest the
        # other way by this dislocation
        dev  = r - mu
        cand = np.where(self._adj[k] & ~np.isnan(dev) & (np.sign(dev) == -np.sign(dev[k])))[0]
        j    = int(cand[np.argmax(np.abs(dev[cand]))]) if cand.size else -1

        dev0 = dev[k]                      # |dev0| = |z_k| * sigma_k, never zero
        if not np.isfinite(dev0) or dev0 == 0:
            return zeros

        self._lastFire[k] = t
        self.events += 1
        if self.clock is not None:
            self.byHour[self.clock.now().hour] += 1
        self._pending.append(dict(kind="event", t=t, edge=k, sign=np.sign(dev0),
                                  size=abs(dev0), y0=y.copy(), mu0=mu.copy(), dev0=dev0,
                                  nbr=j, nbrSign=(np.sign(dev[j]) if j >= 0 else 0.0)))
        return zeros

    # --------------------------------------------------------------- report
    def report(self, interval):
        if not self.events:
            print("No events detected. Lower --z or widen the date range.")
            return
        print(f"\n{'='*78}\nIMPULSE RESPONSE\n{'='*78}")
        print(f"samples {self.samples:,}   events {self.events:,}   "
              f"({self.events/max(self.samples,1)*100:.3f}% of samples)")
        print(f"threshold |z| >= {Z_THRESHOLD}, refractory {REFRACTORY} samples, "
              f"tracked {MAXLAG} samples forward\n")
        print("bp columns are the actual mid-to-mid move, so they compare directly against")
        print("the ~3.1 bp cost of a round trip. 'frac' is the share of the dislocation")
        print("captured: 1.00 would mean the pair returned exactly to its own average.\n")
        print(f"  {'lag':>8s} {'resid left':>11s} {'fade shocked':>21s} {'fade laggard':>14s} "
              f"{'control':>10s}")
        print(f"  {'':>8s} {'':>11s} {'frac':>9s} {'bp':>11s} {'bp':>14s} {'bp':>10s}")
        for lag in sorted({0, 6, 12, 24, 60, 120, 180, 240, 360, 720}):
            if lag > MAXLAG or self.count[lag] == 0:
                continue
            c   = self.count[lag]
            res = self.ownRes[lag] / self.resCount[lag] if self.resCount[lag] else float("nan")
            own = self.ownPnl[lag] / c

            ownbp = self.ownBp[lag] / c
            nbrbp = self.nbrBp[lag] / self.nbrCount[lag] if self.nbrCount[lag] else float("nan")
            ctlbp = self.ctlBp[lag] / self.ctlCount[lag] if self.ctlCount[lag] else float("nan")
            mark = ""
            if max(ownbp, nbrbp) > COST_BP + ctlbp:
                mark = "  <- clears cost above the control"
            print(f"  {lag*interval:>6.0f}s {res:>11.2f} {own:>9.2f} {ownbp:>10.2f}  "
                  f"{nbrbp:>13.2f} {ctlbp:>9.2f}{mark}")
        print(f"\n  'resid left' 1.00 = dislocation unchanged, 0.00 = fully closed.")
        print(f"  'fade shocked' positive = the dislocated pair came back.")
        print(f"  'fade laggard' positive = the pair left behind caught up.")
        print(f"  'control' is a random edge at a random time; treat it as the noise floor.")
        print(f"  a row is tradeable only if a bp column beats control by more than "
              f"{COST_BP} bp.")
        if self.byHour.any():
            print("\nEVENTS BY HOUR (UTC)   dislocations are not spread evenly through the day")
            peak = self.byHour.max()
            for h in range(24):
                if self.byHour[h]:
                    bar = "#" * int(round(40 * self.byHour[h] / peak))
                    print(f"  {h:>2d} {self.byHour[h]:>7,}  {bar}")

def _install():
    """Print the report when the process ends, however backtestMain finishes."""
    def emit():
        for s in _LIVE:
            try:
                import backtestMain as bm
                s.report(bm.core._interval if bm.core else 5.0)
            except Exception as e:
                print(f"(impulse report unavailable: {e})")
    atexit.register(emit)


_LIVE = []
_install()