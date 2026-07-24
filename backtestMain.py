import os
import sys
import asyncio
import logging
import argparse

import config
import logSetup
import backtestConfig as bt
from clock import SimClock
from sessionManager import SessionManager
from fxRates import FxRates
from stateManager import StateManager
from signalSource import RingBufferSource
from tradingCore import TradingCore
from simBroker import SimBroker
import barReplay

log = logging.getLogger(__name__)

clock   = SimClock()
session = SessionManager(clock, config.tradingHoursUTC, forceActive=bt.forceSessionActive)
fx      = FxRates()
state   = StateManager(fx, config.marginRate)
sim     = SimBroker(
    conIdMap   = {f"{c.symbol}{c.currency}": cid for c, cid in bt.universe},
    halfSpread = bt.halfSpread,
)
core    = TradingCore(sim, clock, RingBufferSource(config.signalLookback), session, state)

_equity = []
_progress = {"day": None, "ticks": 0}


def _report():
    if len(_equity) < 2:
        print("\nNo equity samples recorded (need >=2).")
        return
    start, end = _equity[0], _equity[-1]
    rets = [(_equity[i] - _equity[i-1]) / _equity[i-1]
            for i in range(1, len(_equity)) if _equity[i-1]]
    mean = sum(rets) / len(rets) if rets else 0.0
    var  = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    std  = var ** 0.5
    sharpe = (mean / std) if std else 0.0
    peak, maxdd = _equity[0], 0.0
    for v in _equity:
        peak  = max(peak, v)
        maxdd = max(maxdd, (peak - v) / peak if peak else 0.0)
    print("\n================ BACKTEST RESULT ================")
    print(f"  equity samples : {len(_equity)}")
    print(f"  fills          : {state.fills}")
    print(f"  start equity   : {start:,.2f}")
    print(f"  end equity     : {end:,.2f}")
    print(f"  total return   : {(end/start - 1)*100:+.3f}%")
    print(f"  max drawdown   : {maxdd*100:.3f}%")
    print(f"  per-sample Sharpe: {sharpe:.4f}  (NOT annualised)")
    print("=================================================")


async def run(replay, pace):
    if not await core.setup([c for c, _ in bt.universe]):
        return
    core.start()
    state.seed("USD", bt.startingCash)

    log.info("Replaying ticks in-process (pace=%.2f, 0 = unthrottled).", pace)

    prev_ts    = None
    last_eq_ts = None
    for tick in replay:
        if pace > 0 and prev_ts is not None:
            dt = (tick.ts - prev_ts).total_seconds() / pace
            await asyncio.sleep(dt if dt > 0 else 0)
        else:
            await asyncio.sleep(0)
        prev_ts = tick.ts

        clock.advance(tick.ts)
        session.update()
        sim.feedTick(tick.conId, tick.bid, tick.ask, tick.ts)
        core.sampler.poll()

        if last_eq_ts is None or (tick.ts - last_eq_ts).total_seconds() >= 60:
            _equity.append(state.equity())
            last_eq_ts = tick.ts

        day = tick.ts.date()
        if _progress["day"] is None:
            _progress["day"] = day
        elif day != _progress["day"]:
            log.info("Replayed %s   ticks %s   %s",
                     _progress["day"], f"{_progress['ticks']:,}", core.summary())
            _progress["day"], _progress["ticks"] = day, 0
        _progress["ticks"] += 1

    if _progress["day"] is not None:
        log.info("Replayed %s   ticks %s   %s",
                 _progress["day"], f"{_progress['ticks']:,}", core.summary())
    for _ in range(4):
        await asyncio.sleep(0)
    await core.cancelAll()
    core.stop()
    if prev_ts is not None:
        _equity.append(state.equity())


def _checkVersions():
    required = {
        "StateManager.unpricedCurrencies": hasattr(state, "unpricedCurrencies"),
        "StateManager.reconcileCash":      hasattr(state, "reconcileCash"),
        "TradingCore.summary":             hasattr(core, "summary"),
        "FxRates.usdRate":                 hasattr(state.fx, "usdRate"),
    }
    missing = [name for name, ok in required.items() if not ok]
    if missing:
        raise SystemExit(
            "File version mismatch — these are missing: "
            + ", ".join(missing)
            + ". One or more project files are stale; update them together.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="dukascopy",
                    help=" | ".join(bt.stores) + " | path to a tick CSV")
    ap.add_argument("--from", dest="dfrom", default=bt.testStart, help="start date")
    ap.add_argument("--to", dest="dto", default=bt.testEnd, help="end date (inclusive)")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="wall-clock pacing multiple; 0 = unthrottled (results are identical either way)")
    args = ap.parse_args()

    if args.source in bt.stores:
        root = bt.stores[args.source]
        if not os.path.isdir(root):
            sys.exit(f"Store '{args.source}' not found at {root}")
        conIds = [cid for _, cid in bt.universe]
        replay = barReplay.load_duckdb(root, conIds, args.dfrom, args.dto)
    elif os.path.isfile(args.source):
        replay = barReplay.load_csv(args.source)
    else:
        sys.exit(f"Unknown source '{args.source}' (expected {' | '.join(bt.stores)} or a CSV path)")

    logSetup.setup()
    _checkVersions()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run(replay, args.pace))
    except KeyboardInterrupt:
        log.info("Interrupted — reporting partial result.")
    finally:
        _report()
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
