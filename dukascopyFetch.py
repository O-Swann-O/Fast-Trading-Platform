import os
import time
import lzma
import http.client
import struct
import logging
import argparse
import urllib.parse
from datetime import datetime, timedelta

import backtestConfig as bt
import dataStore

log = logging.getLogger(__name__)

BASE     = "https://datafeed.dukascopy.com/datafeed"
PACE     = 0.75            # floor between requests, seconds
MAX_PACE = 15.0            # ceiling once the feed starts pushing back
TIMEOUT  = 60              # a throttled request can take 25s+ and still succeed
ABORT_AFTER = 25           # consecutive failures that mean the feed has cut us off
RESAMPLE = 1
_REC     = struct.Struct(">IIIff")


class DownloadFailed(Exception):
    pass


class FeedUnavailable(Exception):
    """The feed is refusing us. Stop, rather than burn the range writing nothing."""


_conn  = None
_pace  = PACE
_host  = urllib.parse.urlsplit(BASE).netloc


def _closeConn():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def _httpGet(path):
    """One GET on a kept-alive connection. Retries once if the socket went stale."""
    global _conn
    for attempt in (0, 1):
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(_host, timeout=TIMEOUT)
            _conn.request("GET", path, headers={"User-Agent": "Mozilla/5.0",
                                                "Connection": "keep-alive"})
            r    = _conn.getresponse()
            body = r.read()
            return r.status, body
        except Exception:
            _closeConn()
            if attempt:
                raise


def _scale(symbol):
    return 1000.0 if "JPY" in symbol else 100000.0


def _url(symbol, dt):
    return (f"{BASE}/{symbol}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _download(url):
    """Bytes for this hour, or None if the feed has no data for it.

    An hour with no data is served as 200 with an empty body, not 404, so an empty
    return and a missing hour are the same thing to the caller. Raises DownloadFailed
    only when the server would not answer at all.
    """
    global _pace
    path = urllib.parse.urlsplit(url).path
    last = None
    for attempt in range(4):
        try:
            status, body = _httpGet(path)
            if status == 404:
                _pace = max(PACE, _pace * 0.8)
                return None
            if status == 200:
                _pace = max(PACE, _pace * 0.8)          # ease back off after a good one
                return body
            last = f"HTTP {status}"
            if status in (429, 503, 502, 504):          # explicit back-pressure
                _pace = min(MAX_PACE, max(_pace * 2.0, 2.0))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            _pace = min(MAX_PACE, max(_pace * 1.5, 2.0))
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
    streak   = 0
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
            streak = 0
        except DownloadFailed as e:
            log.warning("hour refused, %s will not be written: %s", dt.date(), e)
            holes += 1
            dayHoles += 1
            streak += 1
            raw = None
            if streak >= ABORT_AFTER:
                _closeConn()
                raise FeedUnavailable(
                    f"{symbol}: {streak} consecutive hours refused, last at {dt}. "
                    f"The feed is not serving us; nothing further would be written. "
                    f"Check with Diagnostics/netProbe.py and retry later.")

        if raw:
            try:
                for ts, bid, ask in _decode_hour(raw, dt, scale):
                    if start <= ts < end:
                        buf[_floor(ts, RESAMPLE)] = (bid, ask)
            except lzma.LZMAError as e:
                log.warning("decode failed %s: %s", _url(symbol, dt), e)
                holes += 1
                dayHoles += 1
        time.sleep(_pace)
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
    if total == 0 and skipped == 0 and holes == 0:
        log.warning("%s: nothing fetched and nothing skipped — the feed served no data "
                    "for this entire range.", symbol)


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
    try:
        for i, (symbol, conId) in enumerate(targets, 1):
            log.info("[%d/%d] Dukascopy fetch: %s %s -> %s",
                     i, len(targets), symbol, bt.fetchStart, bt.fetchEnd)
            fetch_pair(symbol, conId, start, end, skipExisting)
    finally:
        _closeConn()


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
    try:
        run(only, skipExisting=not args.refetch)
    except FeedUnavailable as e:
        log.error("ABORTED: %s", e)
        raise SystemExit(2)