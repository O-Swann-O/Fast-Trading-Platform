import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = _HERE if os.path.exists(os.path.join(_HERE, "backtestConfig.py")) else os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import asyncio
import logging
import argparse

from ib_async import IB, Forex

import config
import logSetup
from fxRates import FxRates
from stateManager import StateManager
from reconciler import Reconciler

log = logging.getLogger(__name__)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD", help="FX pair you hold a manual position in")
    args = ap.parse_args()

    logSetup.setup()

    ib = IB()
    print(f"connecting to {config.host}:{config.port} ...")
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 50, timeout=config.connectTimeout)

    accounts = ib.managedAccounts()
    account  = accounts[0] if accounts else ""
    print(f"account: {account}\n")

    contract = (await ib.qualifyContractsAsync(Forex(args.symbol, "IDEALPRO")))[0]
    conId    = contract.conId
    print(f"{args.symbol} conId = {conId}\n")

    ib.client.reqAccountUpdates(True, account)
    try:
        await asyncio.wait_for(ib.reqPositionsAsync(), timeout=15)
    except asyncio.TimeoutError:
        print("reqPositionsAsync timed out")
    await asyncio.sleep(3)

    print("--- portfolio() ---")
    portfolio = ib.portfolio()
    if not portfolio:
        print("   (empty)")
    for item in portfolio:
        print(f"   {item.contract.localSymbol:<10} conId={item.contract.conId:<12} "
              f"position={item.position}")

    print("\n--- positions() ---")
    positions = ib.positions()
    if not positions:
        print("   (empty)")
    for p in positions:
        print(f"   {p.contract.localSymbol:<10} conId={p.contract.conId:<12} "
              f"position={p.position}")

    brokerQty = 0
    for p in positions:
        if p.contract.conId == conId:
            brokerQty = int(p.position)

    print(f"\n--- broker reports {args.symbol}: {brokerQty} ---")

    if not portfolio and not positions:
        print("\nNo positions reported at all.")
        print("If you have NOT placed a manual trade, place one and re-run.")
        print("If you HAVE, this is the dangerous case: FX positions are not visible to the API.")

    print("\n=== RECONCILER BEHAVIOUR TEST ===")
    print("Simulating a book that believes it holds 25,000, and running the real audit.\n")

    fx    = FxRates()
    state = StateManager(fx, config.marginRate)
    state.registerInstrument(conId, contract.symbol, contract.currency)
    state.seed("USD", 100_000.0)
    state.reconcilePosition(conId, 25_000)

    rec = Reconciler(ib, state, config.reconcileInterval)
    drift = []
    rec.onDriftCorrected = lambda *a: drift.append(a)
    rec._reconcile()

    after = state.inventory.get(conId, 0)
    print(f"\nbook before audit : 25000")
    print(f"book after audit  : {after}")
    print(f"drift events      : {len(drift)}")

    print("\n=== VERDICT ===")
    if brokerQty and after == brokerQty:
        print("SAFE. The broker reports the position and the reconciler agrees with it.")
        print("Your live reconciler will work as intended.")
    elif not positions and after == 25_000:
        print("GUARD ACTIVE. The broker reported no positions, and the reconciler correctly")
        print("refused to flatten the book (see the ERROR line above).")
        print("If you DO hold a position right now, FX positions are invisible to the API:")
        print("  enable Virtual FX Tracking in Client Portal > Settings > Account Settings > Trading,")
        print("  or reconcile from portfolio()/cash instead of positions().")
    elif after == 0:
        print("DANGER. The reconciler zeroed a book that believed it held 25,000.")
        print("Do not run live until this is resolved.")
    else:
        print(f"Broker said {brokerQty}, book ended at {after}. Inspect the output above.")

    ib.client.reqAccountUpdates(False, account)
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())