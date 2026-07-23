import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import duckdb

import backtestConfig as bt


def _count(con, pattern, start=None, end=None):
    where = ""
    args  = []
    if start and end:
        where = "WHERE time >= CAST(? AS TIMESTAMP) AND time < CAST(? AS DATE) + INTERVAL 1 DAY"
        args  = [start, end]
    sql = f"""
        SELECT COUNT(*), COUNT(DISTINCT conId), MIN(time), MAX(time)
        FROM read_parquet('{pattern}')
        {where}
    """
    return con.execute(sql, args).fetchone()


def _by_day(con, pattern, start, end):
    sql = f"""
        SELECT CAST(time AS DATE) AS d, COUNT(*) AS n, COUNT(DISTINCT conId) AS pairs
        FROM read_parquet('{pattern}')
        WHERE time >= CAST(? AS TIMESTAMP) AND time < CAST(? AS DATE) + INTERVAL 1 DAY
        GROUP BY 1 ORDER BY 1
    """
    return con.execute(sql, [start, end]).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy")
    ap.add_argument("--from", dest="dfrom", default=bt.testStart)
    ap.add_argument("--to", dest="dto", default=bt.testEnd)
    args = ap.parse_args()

    root = bt.stores.get(args.source, args.source)
    if not os.path.isdir(root):
        sys.exit(f"Store not found: {root}")

    native  = os.path.join(root, "*", "*.parquet")
    forward = native.replace("\\", "/")
    con     = duckdb.connect()

    print(f"store: {root}")
    print(f"files on disk (os.walk): "
          f"{sum(len([f for f in fs if f.endswith('.parquet')]) for _, _, fs in os.walk(root))}")
    print()

    print("GLOB PATTERN COMPARISON")
    for label, pattern in (("os.path.join", native), ("forward slash", forward)):
        try:
            n, pairs, lo, hi = _count(con, pattern)
            print(f"  {label:<14} rows={n:>12,}  pairs={pairs}  {lo} .. {hi}")
        except Exception as e:
            print(f"  {label:<14} FAILED: {str(e)[:80]}")
    print()

    print(f"RANGE {args.dfrom} .. {args.dto}")
    n, pairs, lo, hi = _count(con, forward, args.dfrom, args.dto)
    print(f"  rows={n:,}  pairs={pairs}  {lo} .. {hi}")
    print(f"  expected equity samples ~{n and int((hi - lo).total_seconds() // 60):,}")
    print()

    rows = _by_day(con, forward, args.dfrom, args.dto)
    print(f"DAILY COVERAGE ({len(rows)} days with data)")
    for d, n, pairs in rows:
        flag = "" if pairs == len(bt.universe) else f"  <-- only {pairs}/{len(bt.universe)} pairs"
        print(f"  {d}  rows={n:>9,}  pairs={pairs}{flag}")


if __name__ == "__main__":
    main()