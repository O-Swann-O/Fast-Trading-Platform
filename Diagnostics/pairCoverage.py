"""
Per-pair coverage of the tick store, checked against the configured universe.

Answers three things storeAudit does not break down by pair:
  - which universe pairs have no data at all
  - which have shorter history than the rest (late start / early end)
  - which are missing sessions inside their own date range

    python Diagnostics/pairCoverage.py
    python Diagnostics/pairCoverage.py --source ibkr
"""
import os
import sys
import glob
import argparse

here = os.path.dirname(os.path.abspath(__file__))
root = here if os.path.exists(os.path.join(here, "backtestConfig.py")) else os.path.dirname(here)
sys.path.insert(0, root)

import duckdb
import backtestConfig as bt

ap = argparse.ArgumentParser()
ap.add_argument("--source", default="dukascopy", choices=sorted(bt.stores))
args = ap.parse_args()

store             = bt.stores[args.source]
universe, _       = bt.universeFor(args.source) if hasattr(bt, "universeFor") else (bt.universe, None)
names             = {cid: (c.symbol + c.currency if hasattr(c, "symbol") else str(cid))
                     for c, cid in universe}

con  = duckdb.connect()
rows = con.execute(f"""
    SELECT conId, count(*), count(DISTINCT CAST(time AS DATE)), min(time), max(time)
    FROM read_parquet('{os.path.join(store, "*", "*.parquet").replace(os.sep, "/")}')
    GROUP BY conId
""").fetchall()
byId = {int(r[0]): r for r in rows}

# the widest date span seen anywhere in the store is the yardstick
allDays = con.execute(f"""
    SELECT count(DISTINCT CAST(time AS DATE))
    FROM read_parquet('{os.path.join(store, "*", "*.parquet").replace(os.sep, "/")}')
""").fetchone()[0]

print(f"store          : {store}")
print(f"universe pairs : {len(universe)}")
print(f"pairs in data  : {len(byId)}")
print(f"session days   : {allDays:,} (union across all pairs)\n")
print(f"{'pair':10s} {'rows':>14s} {'days':>7s} {'cover':>7s}  {'first':10s} {'last':10s}")
print("-" * 62)

missing, thin = [], []
for c, cid in universe:
    name = names[cid]
    if cid not in byId:
        missing.append(name)
        print(f"{name:10s} {'-':>14s} {'-':>7s} {'-':>7s}  {'MISSING ENTIRELY'}")
        continue
    _, n, days, lo, hi = byId[cid]
    cover = days / allDays
    flag  = "  <--" if cover < 0.98 else ""
    if cover < 0.98:
        thin.append((name, days, allDays - days))
    print(f"{name:10s} {n:>14,} {days:>7,} {cover:>6.1%}  {str(lo)[:10]} {str(hi)[:10]}{flag}")

extra = sorted(set(byId) - {cid for _, cid in universe})
print()
if missing:
    print(f"MISSING from the store ({len(missing)}): {', '.join(missing)}")
if thin:
    print(f"THIN coverage ({len(thin)}):")
    for name, days, gap in sorted(thin, key=lambda t: t[1]):
        print(f"  {name:10s} {days:,} days, {gap:,} sessions short of the fullest pair")
if extra:
    print(f"IN DATA but not in the universe: {extra}")
if not (missing or thin or extra):
    print("All universe pairs present with full coverage.")

nodes = set()
for c, cid in universe:
    if cid in byId and hasattr(c, "symbol"):
        nodes.add(c.symbol); nodes.add(c.currency)
edges = sum(1 for _, cid in universe if cid in byId)
print(f"\ngraph: {len(nodes)} currencies, {edges} pairs -> "
      f"{edges - len(nodes) + 1} independent cycles (complete graph would give "
      f"{len(nodes)*(len(nodes)-1)//2 - len(nodes) + 1})")
