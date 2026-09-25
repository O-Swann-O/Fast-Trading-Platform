"""Every account value tag IB sends, grouped, with rate-like tags flagged.

Read-only: connects as clientId + 22, requests account updates, prints, disconnects.
Safe to run alongside main.py (clientId 1) and probeAccount.py (clientId 21).

    python Diagnostics/accountTags.py
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = _HERE if os.path.exists(os.path.join(_HERE, "backtestConfig.py")) else os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio
import logging
from collections import defaultdict

from ib_async import IB

import config

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s - %(message)s")

RATE_HINTS = ("rate", "exchange", "fx")


async def main():
    ib = IB()
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 22, timeout=config.connectTimeout)
    try:
        accounts = ib.managedAccounts()
        acct = accounts[0] if accounts else ""
        try:
            # Known to time out (see probeAccount). ib_async has already fetched the
            # account values during connect, so the timeout loses nothing.
            await asyncio.wait_for(ib.reqAccountUpdatesAsync(acct), timeout=15)
        except asyncio.TimeoutError:
            print("reqAccountUpdatesAsync timed out (expected); using values fetched at connect\n")
        await asyncio.sleep(2)

        values = ib.accountValues()
        byTag = defaultdict(list)
        for v in values:
            byTag[v.tag].append((v.currency, v.value))

        print(f"account {acct}: {len(values)} values, {len(byTag)} distinct tags\n")

        flagged = [t for t in byTag if any(h in t.lower() for h in RATE_HINTS)]
        print("--- tags that look like rates ---")
        if not flagged:
            print("  none")
        for tag in sorted(flagged):
            for ccy, val in sorted(byTag[tag]):
                print(f"  {tag:<30} {ccy:<6} {val}")

        print("\n--- every tag, with the currencies it appears in ---")
        for tag in sorted(byTag):
            ccys = sorted({c or "-" for c, _ in byTag[tag]})
            print(f"  {tag:<34} {', '.join(ccys)}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())