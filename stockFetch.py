import os
import json
import asyncio
import logging
import argparse
from datetime import datetime, timedelta, timezone

from ib_async import IB, Stock

import config
import backtestConfig as bt
import dataStore
import logSetup

log = logging.getLogger(__name__)

BARSIZE    = "5 mins"
WHATTOSHOW = "TRADES"
USE_RTH    = True
PACE       = 11.0
BAR_SECS   = 300

SYMBOL_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sp500Symbols.json")
UNIVERSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockUniverse.py")


def _months(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    while cur < end:
        nxt = datetime(cur.year + (cur.month == 12),
                       1 if cur.month == 12 else cur.month + 1, 1, tzinfo=timezone.utc)
        yield cur, min(nxt, end)
        cur = nxt


def _monthKey(dt: datetime) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _exists(root: str, symbol: str, key: str) -> bool:
    return os.path.exists(os.path.join(root, symbol, f"{key}.parquet"))


def _toNaiveUtc(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if d.tzinfo is not None:
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def loadSymbols(limit=None, only=None):
    if not os.path.exists(SYMBOL_FILE):
        raise SystemExit(f"{SYMBOL_FILE} not found. Run diagnostics/fetchSP500Symbols.py first.")
    with open(SYMBOL_FILE) as f:
        data = json.load(f)
    symbols = data["ib"]
    if only:
        symbols = [s for s in symbols if s in only]
    if limit:
        symbols = symbols[:limit]
    return symbols


def writeUniverse(pairs) -> None:
    lines = ["from ib_async import Stock", "", "universe = ["]
    for symbol, conId in pairs:
        lines.append(f'    (Stock("{symbol}", "SMART", "USD"), {conId}),')
    lines += ["]", "", "halfSpread = {cid: 0.0 for _, cid in universe}", ""]
    with open(UNIVERSE_FILE, "w") as f:
        f.write("\n".join(lines))
    log.info("Wrote %s with %d instruments.", UNIVERSE_FILE, len(pairs))


async def qualify(ib, symbols):
    pairs, bad = [], []
    for i, symbol in enumerate(symbols, 1):
        try:
            q = await ib.qualifyContractsAsync(Stock(symbol, "SMART", "USD"))
        except Exception as e:
            bad.append((symbol, str(e)[:50]))
            continue
        if not q:
            bad.append((symbol, "not qualified"))
            continue
        pairs.append((symbol, q[0].conId))
        if i % 50 == 0:
            log.info("Qualified %d/%d ...", i, len(symbols))
        await asyncio.sleep(0.05)
    if bad:
        log.warning("%d symbol(s) failed to qualify: %s",
                    len(bad), ", ".join(s for s, _ in bad[:10]))
    return pairs


async def fetchSymbol(ib, symbol, conId, start, end, root, skipExisting=True):
    contract = Stock(symbol, "SMART", "USD")
    contract.conId = conId
    written = skipped = failed = 0

    for mStart, mEnd in _months(start, end):
        key = _monthKey(mStart)
        if skipExisting and _exists(root, symbol, key):
            skipped += 1
            continue

        days = (mEnd - mStart).days
        try:
            bars = await ib.reqHistoricalDataAsync(
                contract, endDateTime=mEnd, durationStr=f"{days} D",
                barSizeSetting=BARSIZE, whatToShow=WHATTOSHOW, useRTH=USE_RTH)
        except Exception as e:
            log.warning("%s %s request failed: %s", symbol, key, str(e)[:70])
            failed += 1
            await asyncio.sleep(PACE)
            continue

        await asyncio.sleep(PACE)

        if not bars:
            log.warning("%s %s returned no bars.", symbol, key)
            failed += 1
            continue

        rows = []
        for b in bars:
            ts = _toNaiveUtc(b.date) + timedelta(seconds=BAR_SECS)
            if ts < mStart.replace(tzinfo=None) or ts > mEnd.replace(tzinfo=None):
                continue
            px = float(b.close)
            rows.append({"time": ts, "conId": conId, "bid": px, "ask": px})

        if rows:
            rows.sort(key=lambda r: r["time"])
            dataStore.write_day(root, symbol, key, rows)
            written += 1
            log.info("%s: wrote %s (%d bars)", symbol, key, len(rows))

    return written, skipped, failed


async def run(args):
    symbols = loadSymbols(args.limit,
                          {s.strip().upper() for s in args.symbols.split(",")} if args.symbols else None)
    root  = bt.stores["ibkr"]
    start = datetime.fromisoformat(args.dfrom).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(args.dto).replace(tzinfo=timezone.utc)

    nMonths = sum(1 for _ in _months(start, end))
    log.info("Plan: %d symbols x %d months = %d requests, ~%.1f h at %.0fs pacing.",
             len(symbols), nMonths, len(symbols) * nMonths,
             len(symbols) * nMonths * PACE / 3600, PACE)

    ib = IB()
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 40, timeout=config.connectTimeout)
    log.info("Connected. Qualifying %d symbols...", len(symbols))

    pairs = await qualify(ib, symbols)
    log.info("Qualified %d/%d symbols.", len(pairs), len(symbols))
    writeUniverse(pairs)

    if args.qualify_only:
        ib.disconnect()
        return

    totalW = totalS = totalF = 0
    for i, (symbol, conId) in enumerate(pairs, 1):
        w, s, f = await fetchSymbol(ib, symbol, conId, start, end, root, not args.refetch)
        totalW, totalS, totalF = totalW + w, totalS + s, totalF + f
        log.info("[%d/%d] %s done: %d written, %d skipped, %d failed.",
                 i, len(pairs), symbol, w, s, f)

    log.info("Complete: %d months written, %d skipped, %d failed.", totalW, totalS, totalF)
    ib.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2024-01-01")
    ap.add_argument("--to", dest="dto", default="2026-06-01")
    ap.add_argument("--limit", type=int, default=None, help="first N symbols only")
    ap.add_argument("--symbols", default=None, help="comma-separated subset")
    ap.add_argument("--refetch", action="store_true", help="re-download months already on disk")
    ap.add_argument("--qualify-only", action="store_true", help="write stockUniverse.py and stop")
    args = ap.parse_args()

    logSetup.setup()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()