import math
import time
import asyncio
import logging

from ib_async import Stock

import config
from brokerBoundary import BrokerBoundary
from dataFeeder import DataFeeder
from orderManager import OrderManager
from stateManager import StateManager
from sessionManager import SessionManager
from riskGate import RiskGate
from reconciler import Reconciler
from accountManager import AccountManager
from contractRegistry import ContractRegistry
from engineBridge import EngineBridge

log = logging.getLogger(__name__)

state      = StateManager()
broker     = BrokerBoundary()
feeder     = DataFeeder(broker.ib)
session    = SessionManager(config.sessionStart, config.sessionEnd)
reconciler = Reconciler(broker.ib, state, config.reconcileInterval)
account    = AccountManager(broker.ib)
registry   = ContractRegistry(broker.ib)
bridge     = EngineBridge(config.dspHost, config.dspDataPort, config.dspSignalPort)

gate = RiskGate(
    stateManager     = state,
    sessionManager   = session,
    killSwitchActive = config.killSwitchActive,
    maxOrderQty      = config.maxOrderQty,
    maxPosition      = config.maxPosition,
    minCash          = config.minCash,
    maxTickJump      = config.maxTickJump
)

orders = OrderManager(broker.ib, gate)

async def onConnected():
    log.info("Broker Connected.")
    for contract in config.tradeUniverse:
        await registry.register(contract) 
    
    await account.start()
    feeder.start()
    
    for contract in registry.getAll():
        feeder.subscribe(contract.conId, contract)
        
    orders.start()
    reconciler.start()

async def onDisconnected():
    log.info("Broker Disconnected.")
    await orders.cancelAll()
    orders.stop()
    feeder.stop()
    reconciler.stop()
    account.stop()

async def onSessionStart():
    log.info("Market session started. System is active.")

async def onSessionEnd():
    log.info("Market session ended. Halting system.")
    await orders.cancelAll()

def onTick(contractId, ticker):
    price = ticker.marketPrice()
    
    if not math.isnan(price):
        if gate.validateTick(contractId, price):
            state.ticks[contractId] = ticker
            bridge.streamTick(contractId, price, int(ticker.time.timestamp()))

def onTargetPosition(conId, targetPos, confidence, timestamp):
    age = int(time.time()) - timestamp
    if age > config.maxSignalAge:
        log.warning("Signal rejected: Contract %s signal is %ds old (limit %ds).", conId, age, config.maxSignalAge)
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
            log.info("Signal generated: %s | Target: %d | Action: %s %d (Alpha: %.2f)", conId, targetPos, action, qty, confidence)
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
    current = state.inventory.get(contractId, 0)
    if action == "BUY":
        state.inventory[contractId] = current + qty
        state.cash -= qty * price
    elif action == "SELL":
        state.inventory[contractId] = current - qty
        state.cash += qty * price

    releasePending(contractId, action, qty, estPrice)

def onPartial(contractId, action, filledQty, avgPrice, remainingQty, estPrice):
    onFill(contractId, action, filledQty, avgPrice, estPrice)
    releasePending(contractId, action, remainingQty, estPrice)

def onCancelled(contractId, orderId, action, qty, estPrice):
    log.warning("Order cancelled — contract %s order %s", contractId, orderId)
    releasePending(contractId, action, qty, estPrice)

def onRejected(contractId, orderId, action, qty, estPrice):
    log.error("Order rejected — contract %s order %s", contractId, orderId)
    releasePending(contractId, action, qty, estPrice)

def onDriftCorrected(driftType, asset, oldVal, newVal):
    if driftType == "INVENTORY":
        log.warning("Drift corrected [INVENTORY] contract %s: %d -> %d", asset, oldVal, newVal)
    elif driftType == "CASH":
        log.warning("Drift corrected [CASH]: %.2f -> %.2f", oldVal, newVal)

def onAccountUpdate(tag, value):
    if tag == "AvailableFunds":
        state.cash = value

def onPositionUpdate(contractId, position):
    if contractId not in state.inventory and position != 0:
        state.inventory[contractId] = position

broker.onConnected       = onConnected
broker.onDisconnected    = onDisconnected
feeder.onTick            = onTick
bridge.onTargetPosition  = onTargetPosition
orders.onAccepted        = onAccepted
orders.onFill            = onFill
orders.onPartial         = onPartial
orders.onCancelled       = onCancelled
orders.onRejected        = onRejected
session.onSessionStart   = onSessionStart
session.onSessionEnd     = onSessionEnd
account.onAccountUpdate      = onAccountUpdate
account.onPositionUpdate     = onPositionUpdate
reconciler.onDriftCorrected  = onDriftCorrected

_shuttingDown = False

async def shutdown():
    global _shuttingDown
    if _shuttingDown:
        return
    _shuttingDown = True

    log.info("Shutdown initiated — cancelling open orders...")
    await orders.cancelAll()
    orders.stop()
    feeder.stop()
    reconciler.stop()
    account.stop()
    session.stop()
    broker.stop()
    log.info("Shutdown complete.")

async def main():
    session.start()
    await bridge.start()
    try:
        await broker.run()
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s - %(message)s")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        loop.run_until_complete(shutdown())
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()