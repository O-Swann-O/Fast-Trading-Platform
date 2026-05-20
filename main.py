import asyncio
import logging

from brokerBoundary import BrokerBoundary
from dataFeeder import DataFeeder
from orderManager import OrderManager
from stateManager import StateManager

log = logging.getLogger(__name__)

state  = StateManager()
broker = BrokerBoundary()
feeder = DataFeeder(broker.ib)
orders = OrderManager(broker.ib)


async def onConnected():
    log.info("Connected.")
    feeder.start()
    orders.start()


async def onDisconnected():
    log.info("Disconnected.")
    feeder.stop()
    orders.stop()


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


broker.onConnected    = onConnected
broker.onDisconnected = onDisconnected
feeder.onTick         = onTick
orders.onFill         = onFill
orders.onPartial      = onPartial
orders.onCancelled    = onCancelled
orders.onRejected     = onRejected


async def main():
    try:
        await broker.run()
    except KeyboardInterrupt:
        orders.stop()
        feeder.stop()
        broker.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(main())