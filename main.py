import sys
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

log = logging.getLogger(__name__)

state = StateManager()
broker = BrokerBoundary()
feeder = DataFeeder(broker.ib)
session = SessionManager(config.sessionStart, config.sessionEnd)
reconciler = Reconciler(broker.ib, state, config.reconcileInterval)
account = AccountManager(broker.ib)
registry = ContractRegistry(broker.ib)

gate = RiskGate(
    stateManager     = state,
    sessionManager   = session,
    killSwitchActive = config.killSwitchActive,
    maxOrderQty      = config.maxOrderQty,
    maxPosition      = config.maxPosition,
    minCash          = config.minCash
)

orders = OrderManager(broker.ib, gate)

async def onConnected():
    log.info("Broker Connected.")
    
    for sym, exch, curr in config.tradeUniverse:
        await registry.register(Stock(sym, exch, curr))
    
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
    state.ticks[contractId] = ticker

def onFill(contractId, action, qty, price):
    current = state.inventory.get(contractId, 0)
    if action == "BUY":
        state.inventory[contractId] = current + qty
        state.cash -= qty * price
    elif action == "SELL":
        state.inventory[contractId] = current - qty
        state.cash += qty * price

def onPartial(contractId, action, filledQty, avgPrice, remainingQty):
    log.warning("Partial fill — %s %s: filled %d, remaining %d", action, contractId, filledQty, remainingQty)
    onFill(contractId, action, filledQty, avgPrice)

def onCancelled(contractId, orderId):
    log.warning("Order cancelled — contract %s order %s", contractId, orderId)

def onRejected(contractId, orderId):
    log.error("Order rejected — contract %s order %s", contractId, orderId)

def onDriftCorrected(driftType, asset, oldVal, newVal):
    log.warning("Reconciler corrected %s for %s: %s -> %s", driftType, asset, oldVal, newVal)

def onAccountUpdate(tag, value):
    if tag == "AvailableFunds":
        log.info("Initial cash seeded: %.2f", value)
        state.cash = value

def onPositionUpdate(contractId, position):
    if position != 0:
        log.info("Initial position seeded: %s -> %d", contractId, position)
        state.inventory[contractId] = position

broker.onConnected = onConnected
broker.onDisconnected = onDisconnected
feeder.onTick = onTick
orders.onFill = onFill
orders.onPartial = onPartial
orders.onCancelled = onCancelled
orders.onRejected = onRejected
session.onSessionStart = onSessionStart
session.onSessionEnd = onSessionEnd
reconciler.onDriftCorrected = onDriftCorrected
account.onAccountUpdate = onAccountUpdate
account.onPositionUpdate = onPositionUpdate

async def main():
    session.start()
    await broker.run()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("System shutting down manually.")
        session.stop()
        reconciler.stop()
        account.stop()