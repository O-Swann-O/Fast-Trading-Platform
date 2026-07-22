import asyncio
import logging

import config
from brokerBoundary import BrokerBoundary
from accountManager import AccountManager
from reconciler import Reconciler
from clock import WallClock
from sessionManager import SessionManager
from fxRates import FxRates
from stateManager import StateManager
from signalSource import RingBufferSource
from tradingCore import TradingCore

log = logging.getLogger(__name__)

broker     = BrokerBoundary()
clock      = WallClock()
session    = SessionManager(clock, config.tradingHoursUTC)
fx         = FxRates()
state      = StateManager(fx, config.marginRate)
core       = TradingCore(broker.ib, clock, RingBufferSource(config.signalLookback), session, state)
account    = AccountManager(broker.ib)
reconciler = Reconciler(broker.ib, state, config.reconcileInterval)

CASH_TAG = "CashBalance"
_seededCurrencies = set()


def _tagName(tag: str) -> str:
    return tag[len("$LEDGER-"):] if tag.startswith("$LEDGER-") else tag


async def onConnected():
    log.info("Broker Connected.")
    if not await core.setup(config.tradeUniverse):
        log.error("Core setup failed — system idle.")
        return
    log.info("Contracts qualified. Starting account subscriptions...")
    account.start()
    log.info("Account subscriptions done. Subscribing market data...")
    core.start()
    core.sampler.start()
    reconciler.start()
    log.info("System live: %d instruments, sampler and reconciler running.",
             len(core.registry.getAll()))

def _safe(step, label):
    try:
        step()
    except Exception as e:
        log.error("Teardown step '%s' failed: %s", label, e)


async def onDisconnected():
    log.info("Broker Disconnected.")
    if core.sampler:
        _safe(core.sampler.stop, "sampler")
    try:
        await core.cancelAll()
    except Exception as e:
        log.error("cancelAll failed on disconnect: %s", e)
    _safe(core.stop, "core")
    _safe(reconciler.stop, "reconciler")
    _safe(account.stop, "account")

async def onSessionStart():
    log.info("Market session started. System is active.")

async def onSessionEnd():
    log.info("Market session ended. Halting system.")
    await core.cancelAll()

def onAccountUpdate(tag, currency, value):
    if _tagName(tag) != CASH_TAG:
        return
    if currency in ("", "BASE") or currency in _seededCurrencies:
        return
    if value == 0.0:
        return
    state.seed(currency, value)
    _seededCurrencies.add(currency)
    convertible = currency == "USD" or currency in state.fx._ccyPair
    if convertible:
        log.info("Book seeded: %s %.2f", currency, value)
    else:
        log.warning("Seeded %s %.2f but no traded pair can convert it to USD; "
                    "it is excluded from equity.", currency, value)

def onPositionUpdate(contractId, position):
    if contractId not in state.inventory and position != 0:
        state.reconcilePosition(contractId, position)

def onDriftCorrected(driftType, asset, oldVal, newVal):
    if driftType == "INVENTORY":
        log.warning("Drift corrected [INVENTORY] contract %s: %d -> %d", asset, oldVal, newVal)
    elif driftType == "CASH":
        log.warning("Drift corrected [CASH %s]: %.2f -> %.2f", asset, oldVal, newVal)


broker.onConnected          = onConnected
broker.onDisconnected       = onDisconnected
session.onSessionStart      = onSessionStart
session.onSessionEnd        = onSessionEnd
account.onAccountUpdate     = onAccountUpdate
account.onPositionUpdate    = onPositionUpdate
reconciler.onDriftCorrected = onDriftCorrected

_shuttingDown = False


async def shutdown():
    global _shuttingDown
    if _shuttingDown:
        return
    _shuttingDown = True

    log.info("Shutdown initiated — cancelling open orders...")
    if core.sampler:
        core.sampler.stop()
    await core.cancelAll()
    core.stop()
    reconciler.stop()
    account.stop()
    session.stop()
    broker.stop()
    log.info("Shutdown complete.")


async def main():
    session.start()
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