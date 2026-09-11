import os
import sys
import asyncio
import inspect
from datetime import datetime
import logging
import argparse

import config
import logSetup
import backtestConfig as bt
from recorder import Recorder
from clock import SimClock
from sessionManager import SessionManager
from fxRates import FxRates
from stateManager import StateManager
from signalSource import RingBufferSource
from tradingCore import TradingCore
from simBroker import SimBroker
import barReplay
import dataStore

log = logging.getLogger(__name__)

clock   = None
session = None
fx      = None
state   = None
sim     = None
core    = None

universe   = None
_recorder  = None
_progress  = {"day": None, "ticks": 0}
_equity    = {"n": 0, "first": None, "last": None, "peak": None, "maxdd": 0.0}


def makeSignalSource():
    return RingBufferSource(config.signalLookback)


def build(source: str) -> None:
    global clock, session, fx, state, sim, core, universe

    universe, halfSpread = bt.universeFor(source)
    profile = bt.profileFor(source)

    clock   = SimClock()
    session = SessionManager(clock, profile["tradingHoursUTC"],
                             forceActive=bt.forceSessionActive)
    fx      = FxRates()
    state   = StateManager(fx, config.marginRate)
    sim     = SimBroker(
        conIdMap      = {f"{c.symbol}{c.currency}": cid for c, cid in universe},
        halfSpread    = halfSpread,
        commissionBps = bt.commissionBps,
        commissionMin = bt.commissionMin,
        notionalUSD   = state.estNotionalUSD,
    )
    core    = TradingCore(sim, clock, makeSignalSource(), session, state,
                          sampleInterval=profile["sampleInterval"],
                          staleLimit=profile.get("staleLimit"))

    log.info("Universe '%s': %d instruments, sampling every %.0fs, stale after %.0fs, hours %s",
             source, len(universe), profile["sampleInterval"], core._staleLimit,
             profile["tradingHoursUTC"] or "FX week")


def _sampleEquity(value: float) -> None:
    e = _equity
    e["n"] += 1
    if e["first"] is None:
        e["first"] = e["peak"] = value
    e["last"] = value
    if value > e["peak"]:
        e["peak"] = value
    elif e["peak"]:
        e["maxdd"] = max(e["maxdd"], (e["peak"] - value) / e["peak"])


def _report():
    e = _equity
    if e["n"] < 2:
        print("\nNo equity samples recorded (need >=2).")
        return
    start, end = e["first"], e["last"]
    print("\n================ BACKTEST RESULT ================")
    print(f"  equity samples : {e['n']:,}")
    print(f"  fills          : {state.fills:,}")
    print(f"  commission     : {state.commission:,.2f}")
    print(f"  start equity   : {start:,.2f}")
    print(f"  end equity     : {end:,.2f}")
    print(f"  total return   : {(end/start - 1)*100:+.3f}%")
    print(f"  max drawdown   : {e['maxdd']*100:.3f}%")
    print("  full statistics: python analyze.py")
    print("=================================================")


_SETTLE_SPINS = 5


async def _settle(orders) -> None:
    for _ in range(_SETTLE_SPINS):
        if not orders.pending:
            return
        await asyncio.sleep(0)


async def _closeGroup(ts, lastEquityTs, orders):
    await _settle(orders)
    if lastEquityTs is None or (ts - lastEquityTs).total_seconds() >= 60:
        eq = state.equity()
        _sampleEquity(eq)
        if _recorder:
            _recorder.equity(ts, eq)
        return ts
    return lastEquityTs


async def run(replay, pace):
    if not await core.setup([c for c, _ in universe]):
        return
    core.recorder = _recorder
    core.start()
    state.seed("USD", bt.startingCash)

    log.info("Replaying ticks in-process (pace=%.2f, 0 = unthrottled).", pace)

    orders       = core.orders
    groupTs      = None
    lastEquityTs = None
    for tick in replay:
        if tick.ts != groupTs:
            if groupTs is not None:
                lastEquityTs = await _closeGroup(groupTs, lastEquityTs, orders)
                if pace > 0:
                    dt = (tick.ts - groupTs).total_seconds() / pace
                    await asyncio.sleep(dt if dt > 0 else 0)

            day = tick.ts.date()
            if _progress["day"] is None:
                _progress["day"] = day
            elif day != _progress["day"]:
                log.info("Replayed %s   ticks %s   %s",
                         _progress["day"], f"{_progress['ticks']:,}", core.summary())
                _progress["day"], _progress["ticks"] = day, 0

            groupTs = tick.ts
            clock.advance(groupTs)
            session.update()
            core.sampler.poll()
            await _settle(orders)

        sim.feedTick(tick.conId, tick.bid, tick.ask, tick.ts)
        _progress["ticks"] += 1

    if groupTs is not None:
        await _closeGroup(groupTs, lastEquityTs, orders)
        log.info("Replayed %s   ticks %s   %s",
                 _progress["day"], f"{_progress['ticks']:,}", core.summary())
    await core.cancelAll()
    core.stop()
    if groupTs is not None:
        eq = state.equity()
        _sampleEquity(eq)
        if _recorder:
            _recorder.equity(groupTs, eq)


def _fxMappingOk() -> bool:
    probe = FxRates()
    probe.registerInstrument(1, "EUR", "USD")
    probe.registerInstrument(2, "EUR", "GBP")
    probe.onPrice(1, 1.14)
    probe.onPrice(2, 0.85)
    return probe.usdRate("EUR") == 1.14 and probe.usdRate("GBP") is None

def _checkVersions():
    required = {
        "StateManager.pendingCurrencies":   hasattr(state, "pendingCurrencies"),
        "FxRates.canConvert":              hasattr(state.fx, "canConvert"),
        "StateManager.reconcileCash":      hasattr(state, "reconcileCash"),
        "StateManager.applyFill":          hasattr(state, "applyFill"),
        "StateManager.releasePending":     hasattr(state, "releasePending"),
        "TradingCore.summary":             hasattr(core, "summary"),
        "OrderManager.pending":            hasattr(type(core.orders), "pending"),
        "FxRates.usdRate":                 hasattr(state.fx, "usdRate"),
        "FxRates cross-pair mapping":      _fxMappingOk(),
        "FxRates.isRegistered":            hasattr(state.fx, "isRegistered"),
        "TradingCore staleLimit":          hasattr(core, "_staleLimit"),
        "TradingCore reject reason":       "reason" in inspect.signature(core._onRejected).parameters,
        "OrderManager cancel tracking":    hasattr(core.orders, "_cancelRequested"),
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
    ap.add_argument("--name", default=None, help="run name under results/ (default: timestamp)")
    ap.add_argument("--no-save", action="store_true", help="do not record this run to disk")
    args = ap.parse_args()

    logSetup.setup()

    if args.dfrom > args.dto:
        sys.exit(f"Empty range: --from {args.dfrom} is after --to {args.dto}")

    if args.source in bt.stores:
        root = bt.stores[args.source]
        if not os.path.isdir(root):
            sys.exit(f"Store '{args.source}' not found at {root}")
        build(args.source)
        conIds = [cid for _, cid in universe]
        if not dataStore.has_data(root, conIds, args.dfrom, args.dto):
            sys.exit(f"No data in store '{args.source}' for {args.dfrom} .. {args.dto} "
                     f"across {len(conIds)} instrument(s). "
                     f"Check the date range, or that the store matches the universe.")
        replay = barReplay.load_duckdb(root, conIds, args.dfrom, args.dto)
    elif os.path.isfile(args.source):
        build("dukascopy")
        replay = barReplay.load_csv(args.source)
    else:
        sys.exit(f"Unknown source '{args.source}' (expected {' | '.join(bt.stores)} or a CSV path)")

    _checkVersions()

    global _recorder
    if not args.no_save:
        name    = args.name or datetime.now().strftime("%Y%m%d-%H%M%S")
        runDir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", name)
        _recorder = Recorder(runDir)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run(replay, args.pace))
    except KeyboardInterrupt:
        log.info("Interrupted — reporting partial result.")
    finally:
        _report()
        if _recorder:
            _recorder.finish({
                "name":         args.name or os.path.basename(_recorder.outDir),
                "mode":         "backtest",
                "source":       args.source,
                "from":         args.dfrom,
                "to":           args.dto,
                "startingCash": bt.startingCash,
                "signalSource": type(core._source).__name__,
                "instruments":  len(core.registry.getAll()),
                "marks":        core.marks(),
                "symbols":      core.symbols(),
                "quoteCcy":     core.quoteCurrencies(),
                "quoteRates":   core.quoteRates(),
                "commission":   state.commission,
                "endEquity":    state.equity(),
                "cashBy":       state.cashBy,
                "positions":    {k: v for k, v in state.inventory.items() if v},
                "config": {
                    "sampleInterval":      core._interval,
                    "staleLimit":          core._staleLimit,
                    "signalLookback":      config.signalLookback,
                    "marginRate":          config.marginRate,
                    "maxOrderNotional":    config.maxOrderNotional,
                    "maxPositionNotional": config.maxPositionNotional,
                    "minFreeMargin":       config.minFreeMargin,
                },
            })
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
