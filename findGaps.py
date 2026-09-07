import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import duckdb

import backtestConfig as bt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy")
    ap.add_argument("--min-hours", type=int, default=22,
                    help="weekday with fewer distinct hours is treated as incomplete")
    ap.add_argument("--pairs", default=None, help="comma-separated symbols to check")
    ap.add_argument("--delete", action="store_true",
                    help="delete incomplete day files so the fetcher retries them")
    args = ap.parse_args()

    root = bt.stores.get(args.source, args.source)
    if not os.path.isdir(root):
        sys.exit(f"Store not found: {root}")

    only = {s.strip().upper() for s in args.pairs.split(",")} if args.pairs else None
    symbols = sorted(d for d in os.listdir(root)
                     if os.path.isdir(os.path.join(root, d)) and (not only or d in only))

    con = duckdb.connect()
    total_bad = 0
    total_days = 0

    for symbol in symbols:
        pattern = os.path.join(root, symbol, "*.parquet").replace("\\", "/")
        try:
            rows = con.execute(f"""
                SELECT CAST(time AS DATE) AS d,
                       COUNT(DISTINCT date_part('hour', time)) AS hours,
                       COUNT(*) AS n
                FROM read_parquet('{pattern}')
                GROUP BY 1 ORDER BY 1
            """).fetchall()
        except Exception as e:
            print(f"{symbol}: read failed ({str(e)[:60]})")
            continue

        bad = [(d, h, n) for d, h, n in rows
               if d.weekday() < 5 and h < args.min_hours]
        total_days += len(rows)
        total_bad  += len(bad)

        if not bad:
            print(f"{symbol}: {len(rows)} days, all complete")
            continue

        print(f"{symbol}: {len(rows)} days, {len(bad)} INCOMPLETE")
        for d, h, n in bad[:8]:
            print(f"    {d}  {h}/24 hours  {n:,} rows")
        if len(bad) > 8:
            print(f"    ... and {len(bad) - 8} more")

        if args.delete:
            for d, _, _ in bad:
                path = os.path.join(root, symbol, f"{d.isoformat()}.parquet")
                if os.path.exists(path):
                    os.remove(path)
            print(f"    deleted {len(bad)} file(s) for refetch")

    print()
    print(f"TOTAL: {total_days} day-files, {total_bad} incomplete")
    if total_bad and not args.delete:
        print("Re-run with --delete to remove them, then re-run dukascopyFetch.py")


if __name__ == "__main__":
    main()