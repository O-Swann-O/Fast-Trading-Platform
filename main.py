import asyncio
import logging

import os
from datetime import datetime

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
from recorder import Recorder

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
_seedingDone      = False


def _tagName(tag: str) -> str:
    return tag[len("$LEDGER-"):] if tag.startswith("$LEDGER-") else tag


_heartbeat = None
recorder   = Recorder(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                 "live-" + datetime.now().strftime("%Y%m%d-%H%M%S")),
    flushEachFill=True,
)


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
    global _seedingDone
    account.start()
    _seedingDone = True
    log.info("Account subscriptions done. Subscribing market data...")
    core.recorder = recorder
    core.start()
    reconciler.auditNow()
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
    if _seedingDone:
        return
    if _tagName(tag) != CASH_TAG:
        return
    if currency in ("", "BASE") or currency in _seededCurrencies:
        return
    if value == 0.0:
        return
    state.seed(currency, value)
    _seededCurrencies.add(currency)
    if state.fx.canConvert(currency):
        log.info("Book seeded: %s %.2f", currency, value)
    else:
        log.warning("Seeded %s %.2f but no traded pair can convert it to USD; "
                    "it is excluded from equity.", currency, value)

def onPositionUpdate(contractId, position):
    if contractId not in state.inventory and position != 0:
        state.reconcilePosition(contractId, int(position))
        log.info("Position seeded: %s %d", logSetup.name(contractId), int(position))

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
    try:
        recorder.finish({
            "name":         os.path.basename(recorder.outDir),
            "mode":         "live",
            "account":      getattr(account, "_account", ""),
            "signalSource": type(core._source).__name__,
            "instruments":  len(core.registry.getAll()),
            "marks":        core.marks(),
            "symbols":      core.symbols(),
            "endEquity":    state.equity(),
            "cashBy":       state.cashBy,
            "positions":    {k: v for k, v in state.inventory.items() if v},
        })
    except Exception as e:
        log.error("Could not save run record: %s", e)
    log.info("Shutdown complete.")


async def main():
    session.start()
    try:
        await broker.run()
    except asyncio.CancelledError:
        pass


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
        "Reconciler.auditNow":             hasattr(reconciler, "auditNow"),
        "StateManager.reconcileCash":      hasattr(state, "reconcileCash"),
        "StateManager.applyFill":          hasattr(state, "applyFill"),
        "StateManager.releasePending":     hasattr(state, "releasePending"),
        "TradingCore.summary":             hasattr(core, "summary"),
        "OrderManager.pending":            hasattr(type(core.orders), "pending"),
        "FxRates.usdRate":                 hasattr(state.fx, "usdRate"),
        "FxRates cross-pair mapping":      _fxMappingOk(),
        "AccountManager (sync start)":     not __import__("inspect").iscoroutinefunction(account.start),
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
