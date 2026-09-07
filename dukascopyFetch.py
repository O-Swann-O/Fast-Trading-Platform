import os
import time
import lzma
import struct
import logging
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import backtestConfig as bt
import dataStore

log = logging.getLogger(__name__)

BASE     = "https://datafeed.dukascopy.com/datafeed"
PACE     = 0.75
RESAMPLE = 1
_REC     = struct.Struct(">IIIff")


class DownloadFailed(Exception):
    pass


def _scale(symbol):
    return 1000.0 if "JPY" in symbol else 100000.0


def _url(symbol, dt):
    return (f"{BASE}/{symbol}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _download(url):
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(3.0 * (attempt + 1))
    raise DownloadFailed(f"{url}: {last}")


def _decompress(raw):
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            return lzma.decompress(raw, format=fmt)
        except lzma.LZMAError:
            continue
    raise lzma.LZMAError("bi5 decompress failed")


def _decode_hour(raw, hour_start, scale):
    data = _decompress(raw)
    out  = []
    for off in range(0, len(data) - 19, 20):
        ms, ask_i, bid_i, _av, _bv = _REC.unpack_from(data, off)
        ts = hour_start + timedelta(milliseconds=ms)
        out.append((ts, bid_i / scale, ask_i / scale))
    return out


def _floor(ts, secs):
    return ts.replace(second=ts.second - (ts.second % secs), microsecond=0)


def _day_exists(symbol, day):
    return os.path.exists(os.path.join(bt.dataRoot, symbol, f"{day.isoformat()}.parquet"))


def _flush(symbol, conId, day, buf):
    if not buf:
        return 0
    rows = [{"time": k, "conId": conId, "bid": v[0], "ask": v[1]}
            for k, v in sorted(buf.items())]
    dataStore.write_day(bt.dataRoot, symbol, day.isoformat(), rows)
    log.info("%s: wrote %s (%d rows)", symbol, day.isoformat(), len(rows))
    return len(rows)


def fetch_pair(symbol, conId, start, end, skipExisting=True):
    scale   = _scale(symbol)
    dt      = start.replace(minute=0, second=0, microsecond=0)
    cur_day  = None
    buf      = {}
    total    = 0
    skipped  = 0
    holes    = 0
    dayHoles = 0
    while dt < end:
        if cur_day is None:
            cur_day = dt.date()
        if dt.date() != cur_day:
            if dayHoles:
                log.warning("%s: %s INCOMPLETE (%d hour(s) failed) - not written, will retry",
                            symbol, cur_day.isoformat(), dayHoles)
            else:
                total += _flush(symbol, conId, cur_day, buf)
            buf, cur_day, dayHoles = {}, dt.date(), 0

        if dt.weekday() == 5:
            dt = datetime.combine(dt.date() + timedelta(days=1), datetime.min.time())
            buf, cur_day = {}, None
            continue

        if skipExisting and _day_exists(symbol, dt.date()):
            skipped += 1
            dt = datetime.combine(dt.date() + timedelta(days=1), datetime.min.time())
            buf, cur_day = {}, None
            continue

        try:
            raw = _download(_url(symbol, dt))
        except DownloadFailed as e:
            log.warning("hour unavailable, %s will not be written: %s", dt.date(), e)
            holes += 1
            dayHoles += 1
            raw = None

        if raw:
            try:
                for ts, bid, ask in _decode_hour(raw, dt, scale):
                    if start <= ts < end:
                        buf[_floor(ts, RESAMPLE)] = (bid, ask)
            except lzma.LZMAError as e:
                log.warning("decode failed %s: %s", _url(symbol, dt), e)
                holes += 1
                dayHoles += 1
        time.sleep(PACE)
        dt += timedelta(hours=1)
    if cur_day is not None:
        if dayHoles:
            log.warning("%s: %s INCOMPLETE (%d hour(s) failed) - not written, will retry",
                        symbol, cur_day.isoformat(), dayHoles)
        else:
            total += _flush(symbol, conId, cur_day, buf)
    if holes:
        log.warning("%s: %d hour(s) failed; affected days left unwritten for retry.", symbol, holes)
    log.info("Fetched %d rows for %s (%d days already on disk, skipped)", total, symbol, skipped)


def run(only=None, skipExisting=True):
    start = datetime.fromisoformat(bt.fetchStart)
    end   = datetime.fromisoformat(bt.fetchEnd)
    targets = []
    for contract, conId in bt.universe:
        symbol = f"{contract.symbol}{contract.currency}"
        if only and symbol not in only:
            continue
        targets.append((symbol, conId))

    log.info("Fetching %d pair(s): %s", len(targets), ", ".join(s for s, _ in targets))
    for i, (symbol, conId) in enumerate(targets, 1):
        log.info("[%d/%d] Dukascopy fetch: %s %s -> %s",
                 i, len(targets), symbol, bt.fetchStart, bt.fetchEnd)
        fetch_pair(symbol, conId, start, end, skipExisting)


if __name__ == "__main__":
    import logSetup
    logSetup.setup()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=None,
                    help="comma-separated symbols, e.g. EURGBP,GBPJPY (default: all in universe)")
    ap.add_argument("--refetch", action="store_true",
                    help="re-download days already present on disk")
    args = ap.parse_args()
    only = {s.strip().upper() for s in args.pairs.split(",")} if args.pairs else None
    run(only, skipExisting=not args.refetch)