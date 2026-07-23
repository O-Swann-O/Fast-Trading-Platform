import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")

import backtestConfig as bt
import backtestMain as m
from signalSource import FixedTargetSource
import barReplay

TARGET_CONID = 14433401
TARGET_QTY   = 10_000

m.core._source = FixedTargetSource({TARGET_CONID: TARGET_QTY})

replay = barReplay.load_duckdb(
    bt.stores["dukascopy"],
    [cid for _, cid in bt.universe],
    bt.testStart, bt.testEnd,
)

asyncio.new_event_loop().run_until_complete(m.run(replay, 0.0))

s = m.state
print("\n================= FORCED-FILL AUDIT =================")
print(f"  fills             : {s.fills}  (expect 1)")
print(f"  inventory         : {s.inventory}")
print(f"  pending_inventory : {s.pending_inventory}  (expect all 0)")
print(f"  reservedMargin    : {s.reservedMargin:.6f}  (expect ~0)")
print(f"  cash by currency  : {s.cashBy}")
print(f"  equity()          : {s.equity():.2f}")
print(f"  gross notional    : {s.grossNotionalUSD():.2f}")
print(f"  free margin       : {s.freeMarginUSD():.2f}")
print("=======================================================")
print("\nHand-check: cash change should equal -qty * fill_price.")
print("Fill price is the ask (BUY) shown in the 'Signal generated' log line above,")
print("times the qty, minus starting cash. equity() should equal cash + qty * latest mid.")