import sys
import time
import math
import asyncio
import logging
import argparse

import config
import backtestConfig as bt
from stateManager import StateManager
from riskGate import RiskGate
from orderManager import OrderManager
from dataFeeder import DataFeeder
from contractRegistry import ContractRegistry
from engineBridge import EngineBridge
from simBroker import SimBroker
import barReplay

log = logging.getLogger(__name__)


class SimSession:
    def __init__(self):
        self.isActive = False
    def update(self, ts):
        if bt.forceSessionActive:
            self.isActive = True
            return
        weekday = ts.weekday() < 5
        self.isActive = weekday and (config.sessionStart <= ts.time() < config.sessionEnd)


state    = StateManager()
session  = SimSession()
sim      = SimBroker(
    conIdMap   = {f"{c.symbol}{c.currency}": cid for c, cid in bt.universe},
    halfSpread = bt.halfSpread,
)
registry = ContractRegistry(sim)
feeder   = DataFeeder(sim)
bridge   = EngineBridge(config.dspHost, config.dspDataPort, config.dspSignalPort)

gate = RiskGate(
    stateManager     = state,
    sessionManager   = session,
    killSwitchActive = config.killSwitchActive,
    maxOrderQty      = config.maxOrderQty,
    maxPosition      = config.maxPosition,
    minCash          = config.minCash,
    maxTickJump      = config.maxTickJump,
)
orders = OrderManager(sim, gate)

_fills  = 0
_equity = []


def onTick(contractId, ticker):
    price = ticker.marketPrice()
    if not math.isnan(price):
        if gate.validateTick(contractId, price):
            state.ticks[contractId] = ticker
            bridge.streamTick(contractId, price, int(ticker.time.timestamp()))

def onTargetPosition(conId, targetPos, confidence, timestamp):
    age = int(time.time()) - timestamp
    if age > config.maxSignalAge:
        log.warning("Signal rejected: Contract %s signal is %ds old (limit %ds).",
                    conId, age, config.maxSignalAge)
        return
    currentPos = state.inventory.get(conId, 0)
    pendingPos = state.pending_inventory.get(conId, 0)
    assumedPos = currentPos + pendingPos
    delta      = targetPos - assumedPos
    if delta != 0:
        action = "BUY" if delta > 0 else "SELL"
        qty    = abs(delta)
        contract = registry.getById(conId)
        if contract:
            ticker = state.ticks.get(conId)
            estPrice = ticker.marketPrice() if ticker and not math.isnan(ticker.marketPrice()) else 0.0
            orders.submitMarket(conId, contract, action, qty, estPrice)
        else:
            log.error("Signal rejected: Unknown contract ID %s", conId)

def onAccepted(contractId, action, qty, estPrice):
    pending = state.pending_inventory.get(contractId, 0)
    state.pending_inventory[contractId] = pending + (qty if action == "BUY" else -qty)
    if action == "BUY":
        state.reserved_cash += (qty * estPrice)

def releasePending(contractId, action, qty, estPrice):
    pending = state.pending_inventory.get(contractId, 0)
    state.pending_inventory[contractId] = pending - (qty if action == "BUY" else -qty)
    if action == "BUY":
        state.reserved_cash -= (qty * estPrice)

def onFill(contractId, action, qty, price, estPrice):
    global _fills
    current = state.inventory.get(contractId, 0)
    if action == "BUY":
        state.inventory[contractId] = current + qty
        state.cash -= qty * price
    elif action == "SELL":
        state.inventory[contractId] = current - qty
        state.cash += qty * price
    releasePending(contractId, action, qty, estPrice)
    _fills += 1

def onPartial(contractId, action, filledQty, avgPrice, remainingQty, estPrice):
    onFill(contractId, action, filledQty, avgPrice, estPrice)
    releasePending(contractId, action, remainingQty, estPrice)

def onCancelled(contractId, orderId, action, qty, estPrice):
    releasePending(contractId, action, qty, estPrice)

def onRejected(contractId, orderId, action, qty, estPrice):
    releasePending(contractId, action, qty, estPrice)


feeder.onTick           = onTick
bridge.onTargetPosition = onTargetPosition
orders.onAccepted       = onAccepted
orders.onFill           = onFill
orders.onPartial        = onPartial
orders.onCancelled      = onCancelled
orders.onRejected       = onRejected


def _markToMarket():
    val = state.cash
    for cid, qty in state.inventory.items():
        ticker = state.ticks.get(cid)
        if ticker is not None and qty:
            px = ticker.marketPrice()
            if not math.isnan(px):
                val += qty * px
    return val

def _report():
    if len(_equity) < 2:
        print("\nNo equity samples recorded (need >=2).")
        return
    eq = [e for _, e in _equity]
    start, end = eq[0], eq[-1]
    rets = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(eq)) if eq[i-1]]
    mean = sum(rets) / len(rets) if rets else 0.0
    var  = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    std  = var ** 0.5
    sharpe = (mean / std) if std else 0.0
    peak, maxdd = eq[0], 0.0
    for v in eq:
        peak  = max(peak, v)
        maxdd = max(maxdd, (peak - v) / peak if peak else 0.0)
    print("\n================ BACKTEST RESULT ================")
    print(f"  equity samples : {len(eq)}")
    print(f"  fills          : {_fills}")
    print(f"  start equity   : {start:,.2f}")
    print(f"  end equity     : {end:,.2f}")
    print(f"  total return   : {(end/start - 1)*100:+.3f}%")
    print(f"  max drawdown   : {maxdd*100:.3f}%")
    print(f"  per-sample Sharpe: {sharpe:.4f}  (NOT annualised)")
    print("=================================================")


async def run(replay, speed):
    await bridge.start()
    for contract, _ in bt.universe:
        await registry.register(contract)
    feeder.start()
    for contract in registry.getAll():
        feeder.subscribe(contract.conId, contract)
    orders.start()
    state.cash = bt.startingCash

    log.info("Replaying ticks over UDP %s:%d, listening for signals on %d. MATLAB engine must be running.",
             config.dspHost, config.dspDataPort, config.dspSignalPort)

    prev_ts    = None
    last_eq_ts = None
    for tick in replay:
        if prev_ts is not None:
            dt = (tick.ts - prev_ts).total_seconds() / speed
            await asyncio.sleep(dt if dt > 0 else 0)
        prev_ts = tick.ts
        session.update(tick.ts)
        sim.feedTick(tick.conId, tick.bid, tick.ask, tick.ts)
        if last_eq_ts is None or (tick.ts - last_eq_ts).total_seconds() >= 60:
            _equity.append((tick.ts, _markToMarket()))
            last_eq_ts = tick.ts

    await asyncio.sleep(0.5)
    await orders.cancelAll()
    orders.stop()
    feeder.stop()
    if prev_ts is not None:
        _equity.append((prev_ts, _markToMarket()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=bt.tickPath, help="historical tick CSV")
    ap.add_argument("--duckdb", action="store_true", help="read ticks from the Parquet store via DuckDB")
    ap.add_argument("--root", default=bt.dataRoot, help="Parquet store root")
    ap.add_argument("--from", dest="dfrom", default=bt.fetchStart, help="start date (DuckDB source)")
    ap.add_argument("--to", dest="dto", default=bt.fetchEnd, help="end date (DuckDB source)")
    ap.add_argument("--speed", type=float, default=bt.speed, help="replay speed multiple (1.0 = real-time)")
    args = ap.parse_args()

    if args.duckdb:
        conIds = [cid for _, cid in bt.universe]
        replay = barReplay.load_duckdb(args.root, conIds, args.dfrom, args.dto)
    else:
        replay = barReplay.load_csv(args.csv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run(replay, args.speed))
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