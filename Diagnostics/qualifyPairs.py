import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = _HERE if os.path.exists(os.path.join(_HERE, "backtestConfig.py")) else os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio
import logging
import argparse

from ib_async import IB, Forex

import config

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s - %(message)s")

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]

CROSSES = [
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF",
    "CHFJPY",
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=["majors", "crosses", "all"])
    ap.add_argument("--pairs", default=None, help="comma-separated override, e.g. EURGBP,GBPJPY")
    args = ap.parse_args()

    if args.pairs:
        symbols = [s.strip().upper() for s in args.pairs.split(",") if s.strip()]
    elif args.set == "majors":
        symbols = MAJORS
    elif args.set == "crosses":
        symbols = CROSSES
    else:
        symbols = MAJORS + CROSSES

    ib = IB()
    print(f"connecting to {config.host}:{config.port} ...")
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 30, timeout=config.connectTimeout)
    print(f"connected. qualifying {len(symbols)} pairs\n")

    ok, bad = [], []
    for sym in symbols:
        try:
            q = await ib.qualifyContractsAsync(Forex(sym, "IDEALPRO"))
        except Exception as e:
            bad.append((sym, str(e)[:60]))
            continue
        if not q:
            bad.append((sym, "not qualified"))
            continue
        c = q[0]
        ok.append((sym, c.symbol, c.currency, c.conId))
        await asyncio.sleep(0.05)

    width = max((len(s) for s, *_ in ok), default=6)
    print("=" * 60)
    print("PASTE INTO backtestConfig.universe")
    print("=" * 60)
    print("universe = [")
    for sym, base, quote, conId in ok:
        pad = " " * (width - len(sym))
        print(f'    (Forex("{sym}", "IDEALPRO"),{pad} {conId}),')
    print("]")

    print("\n" + "=" * 60)
    print("PASTE INTO backtestConfig.halfSpread")
    print("=" * 60)
    print("halfSpread = {")
    for sym, base, quote, conId in ok:
        pad = " " * (width - len(sym))
        print(f"    {conId}: 0.0,{pad}  # {sym}")
    print("}")

    print("\n" + "=" * 60)
    print("PASTE INTO config.tradeUniverse")
    print("=" * 60)
    print("tradeUniverse = [")
    for sym, base, quote, conId in ok:
        print(f'    Forex("{sym}", "IDEALPRO"),')
    print("]")

    currencies = sorted({b for _, b, _, _ in ok} | {q for _, _, q, _ in ok})
    edges = len(ok)
    nodes = len(currencies)
    print(f"\ngraph: {nodes} currencies, {edges} pairs, "
          f"{edges - nodes + 1} independent cycles")
    print(f"currencies: {', '.join(currencies)}")

    if bad:
        print(f"\nFAILED ({len(bad)}):")
        for sym, why in bad:
            print(f"  {sym}: {why}")

    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
