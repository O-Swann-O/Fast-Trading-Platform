"""
Is Dukascopy refusing us, or is the data actually missing?

dukascopyFetch cannot tell the difference: a 404 (no such file) is swallowed silently
and a 503 (server refusing) is logged as "hour unavailable". Both end up as a day that
never gets written. This asks the server directly, one request per URL, no retries.

The decisive case is CADCHF 2024-02-01: that day is already on disk, so the data
provably exists. If it returns anything other than 200, the problem is access, not data.

    python Diagnostics/dukascopyProbe.py
"""
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

here = os.path.dirname(os.path.abspath(__file__))
root = here if os.path.exists(os.path.join(here, "backtestConfig.py")) else os.path.dirname(here)
sys.path.insert(0, root)

from dukascopyFetch import _url, BASE

CASES = [
    ("EURUSD", datetime(2025, 1, 2, 10), "on disk, data provably exists"),
    ("CADCHF", datetime(2024, 2, 1, 10), "on disk, data provably exists"),
    ("CADCHF", datetime(2024, 4, 1, 10), "after the gap, never fetched"),
    ("CADJPY", datetime(2024, 4, 1, 10), "never fetched at all"),
    ("CHFJPY", datetime(2025, 6, 2, 10), "never fetched at all"),
    ("EURUSD", datetime(2024, 1, 1, 3),  "New Year's Day, expected absent"),
]

print(f"host: {BASE}\n")
print(f"{'pair':8s} {'when':17s} {'result':26s} {'bytes':>8s} {'secs':>6s}  note")
print("-" * 92)

codes = {}
for symbol, dt, note in CASES:
    url = _url(symbol, dt)
    t0  = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
        result, size = f"{r.status} OK", len(body)
    except urllib.error.HTTPError as e:
        result, size = f"{e.code} {e.reason}", 0
    except Exception as e:
        result, size = f"{type(e).__name__}: {str(e)[:18]}", 0
    el = time.perf_counter() - t0
    codes[result.split()[0]] = codes.get(result.split()[0], 0) + 1
    print(f"{symbol:8s} {dt.strftime('%Y-%m-%d %Hh'):17s} {result:26s} {size:>8,} {el:>6.1f}  {note}")
    time.sleep(1.0)

print()
known = [c for c in CASES[:2]]
ok    = codes.get("200", 0)
if ok == 0:
    print("Every request failed, including files that are provably on your disk.")
    print("This is access, not missing data: you are being refused or the feed is down.")
    print("Wait a few hours and re-run this probe before restarting the fetch.")
elif codes.get("503") or codes.get("TimeoutError") or codes.get("URLError"):
    print("Mixed: some requests served, some refused. The feed is throttling.")
    print("Raise PACE in dukascopyFetch.py and fetch one pair at a time.")
elif codes.get("404") and ok:
    print("Server is healthy and answering. A 404 here means that hour genuinely")
    print("has no data upstream, which no amount of retrying will change.")
else:
    print("Server is healthy and serving every file requested.")
