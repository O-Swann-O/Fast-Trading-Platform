"""
Verify that every day file contains only timestamps from its own date.

The per-day replay reader relies on day files being disjoint in time: it sorts each
day independently and concatenates them, which is only equivalent to a global sort if
no file straddles a day boundary. This reads parquet footers, not data, so it is fast.

    python Diagnostics/checkDayFiles.py                 # dukascopy store
    python Diagnostics/checkDayFiles.py --source ibkr
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

store = bt.stores[args.source]
files = sorted(glob.glob(os.path.join(store, "*", "*.parquet")))
if not files:
    sys.exit(f"No parquet files under {store}")

con, bad, empty = duckdb.connect(), [], []
for i, path in enumerate(files, 1):
    day = os.path.basename(path)[:-len(".parquet")]
    lo, hi = con.execute("SELECT min(time), max(time) FROM read_parquet(?)", [path]).fetchone()
    if lo is None:
        empty.append(path)
    elif str(lo)[:10] != day or str(hi)[:10] != day:
        bad.append((path, lo, hi))
    if i % 2000 == 0:
        print(f"  ...{i:,}/{len(files):,}")

print(f"\nStore      : {store}")
print(f"Files      : {len(files):,}")
print(f"Empty      : {len(empty)}")
print(f"Straddling : {len(bad)}")
for path, lo, hi in bad[:20]:
    print(f"  {os.path.relpath(path, store)}  min {lo}  max {hi}")
if len(bad) > 20:
    print(f"  ... and {len(bad) - 20} more")

print("\nSafe for the per-day reader." if not bad else
      "\nNOT safe: the per-day reader would emit these ticks out of order.")
