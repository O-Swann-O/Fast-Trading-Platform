import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ib_async import IB

import config
import backtestConfig as bt
import dataStore

log = logging.getLogger(__name__)

BARSIZE    = "1 min"
CHUNK      = "1 W"
WHATTOSHOW = "BID_ASK"
PACE       = 1.0
BAR_SECS   = 60


def _to_naive_utc(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    if d.tzinfo is not None:
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


async def _fetch_contract(ib, contract, conId, start, end):
    symbol      = f"{contract.symbol}{contract.currency}"
    start_naive = start.replace(tzinfo=None)
    end_naive   = end.replace(tzinfo=None)
    rows_by_day = {}
    seen        = set()
    cursor      = end

    while cursor > start:
        bars = await ib.reqHistoricalDataAsync(
            contract, endDateTime=cursor, durationStr=CHUNK,
            barSizeSetting=BARSIZE, whatToShow=WHATTOSHOW, useRTH=False)
        await asyncio.sleep(PACE)
        if not bars:
            break

        for b in bars:
            ts = _to_naive_utc(b.date) + timedelta(seconds=BAR_SECS)
            if ts < start_naive or ts > end_naive or ts in seen:
                continue
            seen.add(ts)
            day = ts.date().isoformat()
            rows_by_day.setdefault(day, []).append(
                {"time": ts, "conId": conId, "bid": float(b.open), "ask": float(b.close)})

        earliest = _to_naive_utc(bars[0].date).replace(tzinfo=timezone.utc)
        nxt = earliest - timedelta(seconds=1)
        if nxt >= cursor:
            break
        cursor = nxt

    total = 0
    for day in sorted(rows_by_day):
        rows = sorted(rows_by_day[day], key=lambda r: r["time"])
        dataStore.write_day(bt.dataRoot, symbol, day, rows)
        log.info("%s: wrote %s (%d bars)", symbol, day, len(rows))
        total += len(rows)
    log.info("Fetched %d bars total for %s", total, symbol)


async def run():
    ib = IB()
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 10, timeout=config.connectTimeout)
    start = datetime.fromisoformat(bt.fetchStart).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(bt.fetchEnd).replace(tzinfo=timezone.utc)
    for contract, conId in bt.universe:
        q = await ib.qualifyContractsAsync(contract)
        if not q:
            log.error("Could not qualify %s", contract)
            continue
        log.info("Starting fetch: %s [%s bars] %s -> %s",
                 contract.symbol, BARSIZE, bt.fetchStart, bt.fetchEnd)
        await _fetch_contract(ib, q[0], conId, start, end)
    ib.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
    asyncio.run(run())