import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ib_async import IB

import config
import backtestConfig as bt
import dataStore

log = logging.getLogger(__name__)

BATCH = 1000
PACE  = 0.15


async def _fetch_contract(ib, contract, conId, start, end):
    symbol = f"{contract.symbol}{contract.currency}"
    cur, day, buf, total = start, None, [], 0
    while cur < end:
        ticks = await ib.reqHistoricalTicksAsync(contract, cur, "", BATCH, "BID_ASK", False)
        if not ticks:
            break
        for t in ticks:
            if t.time > end:
                break
            d = t.time.date().isoformat()
            if day is None:
                day = d
            if d != day:
                dataStore.write_day(bt.dataRoot, symbol, day, buf)
                total += len(buf)
                buf, day = [], d
            buf.append({"time": t.time.replace(tzinfo=None), "conId": conId,
                        "bid": float(t.priceBid), "ask": float(t.priceAsk)})
        nxt = ticks[-1].time + timedelta(seconds=1)
        if nxt <= cur:
            break
        cur = nxt
        await asyncio.sleep(PACE)
    if buf and day is not None:
        dataStore.write_day(bt.dataRoot, symbol, day, buf)
        total += len(buf)
    log.info("Fetched %d ticks for %s into %s", total, symbol, bt.dataRoot)


async def run():
    ib = IB()
    await ib.connectAsync(config.host, config.port, clientId=config.clientId + 10,
                          timeout=config.connectTimeout)
    start = datetime.fromisoformat(bt.fetchStart).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(bt.fetchEnd).replace(tzinfo=timezone.utc)
    for contract, conId in bt.universe:
        q = await ib.qualifyContractsAsync(contract)
        if not q:
            log.error("Could not qualify %s", contract)
            continue
        await _fetch_contract(ib, q[0], conId, start, end)
    ib.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
    asyncio.run(run())