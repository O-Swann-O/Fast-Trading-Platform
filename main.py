import asyncio
import logging

import config
import logSetup
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


_heartbeat = None


async def _heartbeatLoop():
    while True:
        await asyncio.sleep(60)
        log.info("Status: %s", core.summary())


async def onConnected():
    global _heartbeat
    log.info("Broker connected.")
    if not await core.setup(config.tradeUniverse):
        log.error("Core setup failed — system idle.")
        return
    log.info("Contracts qualified. Starting account subscriptions...")
    account.start()
    log.info("Account subscriptions done. Subscribing market data...")
    core.start()
    core.sampler.start()
    reconciler.start()
    _heartbeat = asyncio.create_task(_heartbeatLoop())
    log.info("System live: %d instruments. %s", len(core.registry.getAll()), core.summary())

def _safe(step, label):
    try:
        step()
    except Exception as e:
        log.error("Teardown step '%s' failed: %s", label, e)


async def onDisconnected():
    global _heartbeat
    log.info("Broker disconnected.")
    if _heartbeat:
        _heartbeat.cancel()
        _heartbeat = None
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
    global _shuttingDown, _heartbeat
    if _shuttingDown:
        return
    _shuttingDown = True

    log.info("Shutdown initiated — cancelling open orders...")
    if _heartbeat:
        _heartbeat.cancel()
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


def _checkVersions():
    required = {
        "StateManager.unpricedCurrencies": hasattr(state, "unpricedCurrencies"),
        "StateManager.reconcileCash":      hasattr(state, "reconcileCash"),
        "TradingCore.summary":             hasattr(core, "summary"),
        "FxRates.usdRate":                 hasattr(state.fx, "usdRate"),
        "AccountManager (sync start)":     not __import__("asyncio").iscoroutinefunction(account.start),
        "BrokerBoundary.attempt counter":  hasattr(broker, "_attempt"),
    }
    missing = [name for name, ok in required.items() if not ok]
    if missing:
        raise SystemExit(
            "File version mismatch — these are missing: "
            + ", ".join(missing)
            + ". One or more project files are stale; update them together.")


if __name__ == "__main__":
    logSetup.setup()
    _checkVersions()

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
