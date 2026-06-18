import time
import lzma
import struct
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import backtestConfig as bt
import dataStore

log = logging.getLogger(__name__)

BASE     = "https://datafeed.dukascopy.com/datafeed"
PACE     = 0.1
RESAMPLE = 1
_REC     = struct.Struct(">IIIff")


def _scale(symbol):
    return 1000.0 if "JPY" in symbol else 100000.0


def _url(symbol, dt):
    return (f"{BASE}/{symbol}/{dt.year:04d}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _download(url):
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.0)
    log.warning("download failed %s: %s", url, last)
    return None


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


def _flush(symbol, conId, day, buf):
    if not buf:
        return 0
    rows = [{"time": k, "conId": conId, "bid": v[0], "ask": v[1]}
            for k, v in sorted(buf.items())]
    dataStore.write_day(bt.dataRoot, symbol, day.isoformat(), rows)
    log.info("%s: wrote %s (%d rows)", symbol, day.isoformat(), len(rows))
    return len(rows)


def fetch_pair(symbol, conId, start, end):
    scale   = _scale(symbol)
    dt      = start.replace(minute=0, second=0, microsecond=0)
    cur_day = None
    buf     = {}
    total   = 0
    while dt < end:
        if cur_day is None:
            cur_day = dt.date()
        if dt.date() != cur_day:
            total += _flush(symbol, conId, cur_day, buf)
            buf, cur_day = {}, dt.date()
        raw = _download(_url(symbol, dt))
        if raw:
            try:
                for ts, bid, ask in _decode_hour(raw, dt, scale):
                    if start <= ts < end:
                        buf[_floor(ts, RESAMPLE)] = (bid, ask)
            except lzma.LZMAError as e:
                log.warning("decode failed %s: %s", _url(symbol, dt), e)
        time.sleep(PACE)
        dt += timedelta(hours=1)
    total += _flush(symbol, conId, cur_day, buf)
    log.info("Fetched %d rows for %s", total, symbol)


def run():
    start = datetime.fromisoformat(bt.fetchStart)
    end   = datetime.fromisoformat(bt.fetchEnd)
    for contract, conId in bt.universe:
        symbol = f"{contract.symbol}{contract.currency}"
        log.info("Starting Dukascopy fetch: %s %s -> %s", symbol, bt.fetchStart, bt.fetchEnd)
        fetch_pair(symbol, conId, start, end)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
    run()