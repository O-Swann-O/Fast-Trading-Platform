"""
How much of the currency graph is actually visible at each sample?

A cycle strategy needs all three legs of a triangle fresh in the same sample; a
potentials fit needs the fresh subgraph to span and connect all 8 currencies.
Neither is guaranteed: thin pairs go quiet for seconds at a time, and they go quiet
together, in the same low-liquidity hours.

This replays the store's timestamps (not prices) through the sampler's own freshness
rule and reports what a strategy would have seen.

Rule replicated from SignalSampler._sample:
    stale = (now - lastUpdate) > staleLimit
with lastUpdate set to the tick's own timestamp, and the sample at time t taken
BEFORE ticks stamped t are applied. So a pair is fresh at t iff it printed a bar in
the half-open window [t - staleLimit, t).

    python Diagnostics/graphFreshness.py
    python Diagnostics/graphFreshness.py --from 2025-01-01 --to 2025-02-01
    python Diagnostics/graphFreshness.py --source ibkr
"""
import os
import sys
import glob
import argparse
from datetime import date, datetime, timedelta

here = os.path.dirname(os.path.abspath(__file__))
root = here if os.path.exists(os.path.join(here, "backtestConfig.py")) else os.path.dirname(here)
sys.path.insert(0, root)

import numpy as np
import duckdb
import backtestConfig as bt
from sessionManager import SessionManager

ap = argparse.ArgumentParser()
ap.add_argument("--source", default="dukascopy", choices=sorted(bt.stores))
ap.add_argument("--from", dest="start", default=None)
ap.add_argument("--to",   dest="end",   default=None)
ap.add_argument("--stale",    type=float, default=None, help="override staleLimit seconds")
ap.add_argument("--interval", type=float, default=None, help="override sampleInterval seconds")
args = ap.parse_args()

store    = bt.stores[args.source]
universe = bt.universeFor(args.source)[0] if hasattr(bt, "universeFor") else bt.universe
profile  = bt.profileFor(args.source)     if hasattr(bt, "profileFor")  else {}

import config
INTERVAL = int(args.interval or profile.get("sampleInterval") or config.sampleInterval)
STALE    = int(args.stale    or profile.get("staleLimit")     or config.staleLimit)
HOURS    = profile.get("tradingHoursUTC")

conIds = [cid for _, cid in universe]
names  = [(c.symbol + c.currency) if hasattr(c, "symbol") else str(cid) for c, cid in universe]
N      = len(conIds)
col    = {cid: i for i, cid in enumerate(conIds)}

# ---- graph structure ------------------------------------------------------------
ccys = sorted({c.symbol for c, _ in universe} | {c.currency for c, _ in universe})
node = {c: i for i, c in enumerate(ccys)}
ends = [(node[c.symbol], node[c.currency]) for c, _ in universe]

triangles = []
for a in range(len(ccys)):
    for b in range(a + 1, len(ccys)):
        for d in range(b + 1, len(ccys)):
            legs = []
            for pair in ((a, b), (b, d), (a, d)):
                for e, (u, v) in enumerate(ends):
                    if {u, v} == set(pair):
                        legs.append(e)
                        break
            if len(legs) == 3:
                triangles.append(tuple(legs))
triangles = np.array(triangles, dtype=np.int64) if triangles else np.zeros((0, 3), np.int64)

_connCache = {}


def spansAndConnects(mask: int) -> bool:
    """Do the fresh edges connect all currencies into one component?"""
    hit = _connCache.get(mask)
    if hit is not None:
        return hit
    parent = list(range(len(ccys)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen, comps = set(), len(ccys)
    for e, (u, v) in enumerate(ends):
        if mask >> e & 1:
            seen.add(u)
            seen.add(v)
            ru, rv = find(u), find(v)
            if ru != rv:
                parent[ru] = rv
                comps -= 1
    ok = len(seen) == len(ccys) and comps == 1
    _connCache[mask] = ok
    return ok


# ---- day range ------------------------------------------------------------------
days = sorted({os.path.basename(f)[:-8] for f in glob.glob(os.path.join(store, "*", "*.parquet"))})
if args.start:
    days = [d for d in days if d >= args.start]
if args.end:
    days = [d for d in days if d <= args.end]
if not days:
    sys.exit("No day files in that range.")

print(f"store        : {store}")
print(f"range        : {days[0]} .. {days[-1]}  ({len(days):,} day files)")
print(f"universe     : {N} pairs, {len(ccys)} currencies, {len(triangles)} triangles")
print(f"sampling     : every {INTERVAL}s, stale after {STALE}s -> fresh window [t-{STALE}, t)\n")

con     = duckdb.connect()
freshHist   = np.zeros(N + 1, dtype=np.int64)
triHist     = np.zeros(len(triangles) + 1, dtype=np.int64)
perHourSum  = np.zeros(24, dtype=np.int64)
perHourN    = np.zeros(24, dtype=np.int64)
perHourTri  = np.zeros(24, dtype=np.int64)
perPairFresh= np.zeros(N, dtype=np.int64)
connected   = 0
totalSamples= 0
SEC_PER_DAY = 86_400
ph = ",".join("?" * N)

for k, day in enumerate(days, 1):
    files = sorted(glob.glob(os.path.join(store, "*", f"{day}.parquet")))
    if not files:
        continue
    rows = con.execute(
        f"SELECT DISTINCT CAST(epoch(time) AS BIGINT) AS s, conId "
        f"FROM read_parquet(?) WHERE conId IN ({ph})", [files, *conIds]).fetchnumpy()
    if rows["s"].size == 0:
        continue

    base = int((datetime.strptime(day, "%Y-%m-%d") - datetime(1970, 1, 1)).total_seconds())
    sec  = (rows["s"] - base).astype(np.int64)
    idx  = np.array([col[int(c)] for c in rows["conId"]], dtype=np.int64)
    keep = (sec >= 0) & (sec < SEC_PER_DAY)
    sec, idx = sec[keep], idx[keep]

    present = np.zeros((SEC_PER_DAY, N), dtype=np.int8)
    present[sec, idx] = 1

    # rolling "any tick in the last STALE seconds", exclusive of the sample second
    cs = np.zeros((SEC_PER_DAY + 1, N), dtype=np.int32)
    np.cumsum(present, axis=0, out=cs[1:])

    ts     = np.arange(0, SEC_PER_DAY, INTERVAL, dtype=np.int64)
    lo     = np.maximum(ts - STALE, 0)
    fresh  = (cs[ts] - cs[lo]) > 0                      # [nSamples, N] bool

    stamps = [datetime(1970, 1, 1) + timedelta(seconds=int(base + t)) for t in ts]
    open_  = np.array([SessionManager._fxOpen(s) and
                       (HOURS is None or HOURS[0] <= s.time() < HOURS[1]) for s in stamps])
    if not open_.any():
        continue
    fresh  = fresh[open_]
    hours  = np.array([s.hour for s in stamps])[open_]

    counts = fresh.sum(axis=1)
    freshHist += np.bincount(counts, minlength=N + 1)
    perPairFresh += fresh.sum(axis=0)
    np.add.at(perHourSum, hours, counts)
    np.add.at(perHourN,   hours, 1)

    if len(triangles):
        triOk = (fresh[:, triangles[:, 0]] & fresh[:, triangles[:, 1]]
                 & fresh[:, triangles[:, 2]]).sum(axis=1)
        triHist += np.bincount(triOk, minlength=len(triangles) + 1)
        np.add.at(perHourTri, hours, triOk)

    bits = (fresh * (1 << np.arange(N, dtype=np.int64))).sum(axis=1)
    for m in np.unique(bits):
        if spansAndConnects(int(m)):
            connected += int((bits == m).sum())

    totalSamples += fresh.shape[0]
    if k % 50 == 0:
        print(f"  ...{k:,}/{len(days):,} days")

if not totalSamples:
    sys.exit("No in-session samples found.")


def pct(hist, q):
    cum = np.cumsum(hist)
    return int(np.searchsorted(cum, cum[-1] * q / 100.0))


print(f"\nin-session samples analysed: {totalSamples:,}\n")
print("FRESH PAIRS PER SAMPLE")
for q in (5, 25, 50, 75, 95):
    print(f"  p{q:<3d} {pct(freshHist, q):>3d} of {N}")
print(f"  mean {float((np.arange(N + 1) * freshHist).sum()) / totalSamples:.1f} of {N}")
print(f"  all {N} fresh: {freshHist[N] / totalSamples:6.2%}")

print("\nTRIANGLES COMPUTABLE PER SAMPLE")
for q in (5, 25, 50, 75, 95):
    print(f"  p{q:<3d} {pct(triHist, q):>3d} of {len(triangles)}")
print(f"  mean {float((np.arange(len(triangles) + 1) * triHist).sum()) / totalSamples:.1f}"
      f" of {len(triangles)}")
print(f"  none computable: {triHist[0] / totalSamples:6.2%}")

print(f"\nFRESH SUBGRAPH SPANS AND CONNECTS ALL {len(ccys)} CURRENCIES")
print(f"  {connected / totalSamples:6.2%} of samples   "
      f"(a potentials fit is well-posed only here)")

print("\nBY HOUR (UTC)")
print(f"  {'hh':>3s} {'samples':>10s} {'mean fresh':>11s} {'mean triangles':>15s}")
for h in range(24):
    if perHourN[h]:
        print(f"  {h:>3d} {perHourN[h]:>10,} {perHourSum[h]/perHourN[h]:>11.1f} "
              f"{perHourTri[h]/perHourN[h]:>15.1f}")

print("\nPER PAIR, FRACTION OF SAMPLES FRESH")
order = np.argsort(perPairFresh)
for i in order:
    bar = "#" * int(round(20 * perPairFresh[i] / totalSamples))
    print(f"  {names[i]:8s} {perPairFresh[i]/totalSamples:6.1%}  {bar}")
