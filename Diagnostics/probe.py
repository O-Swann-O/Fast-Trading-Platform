import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging

from ib_async import IB, Forex

import config

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s - %(message)s")


async def main():
    ib = IB()
    print(f"connecting to {config.host}:{config.port} ...")
    await ib.connectAsync(config.host, config.port,
                          clientId=config.clientId + 20, timeout=config.connectTimeout)
    print("connected\n")

    accounts = ib.managedAccounts()
    print(f"managed accounts: {accounts}")
    acct = accounts[0] if accounts else ""

    print("\n--- reqAccountUpdatesAsync ---")
    try:
        await asyncio.wait_for(ib.reqAccountUpdatesAsync(acct), timeout=15)
        print("returned OK")
    except asyncio.TimeoutError:
        print("TIMED OUT after 15s  <-- this would hang onConnected")
    except Exception as e:
        print(f"RAISED: {e}")

    print("\n--- reqPositionsAsync ---")
    try:
        pos = await asyncio.wait_for(ib.reqPositionsAsync(), timeout=15)
        print(f"returned OK ({len(pos) if pos else 0} positions)")
    except asyncio.TimeoutError:
        print("TIMED OUT after 15s  <-- this would hang onConnected")
    except Exception as e:
        print(f"RAISED: {e}")

    await asyncio.sleep(2)

    print("\n--- account values matching NetLiquidation / Funds / Cash ---")
    interesting = ("NetLiquidation", "AvailableFunds", "TotalCashValue",
                   "BuyingPower", "EquityWithLoanValue", "CashBalance")
    seen = [v for v in ib.accountValues()
            if any(k in v.tag for k in interesting)]
    if not seen:
        print("  none found")
    for v in sorted(seen, key=lambda x: (x.tag, x.currency)):
        print(f"  tag={v.tag:<28} currency={v.currency:<6} value={v.value}")

    currencies = sorted({v.currency for v in ib.accountValues()})
    print(f"\n  all currencies present in accountValues: {currencies}")
    print(f"  'BASE' present: {'BASE' in currencies}")

    print("\n--- positions() ---")
    positions = ib.positions()
    print(f"  {len(positions)} position(s)")
    for p in positions:
        print(f"    {p.contract.localSymbol} conId={p.contract.conId} qty={p.position}")

    print("\n--- market data test: EURUSD, 20s ---")
    contract = (await ib.qualifyContractsAsync(Forex("EURUSD", "IDEALPRO")))[0]
    print(f"  qualified: {contract.localSymbol} conId={contract.conId}")

    count = {"n": 0}
    def onTickers(tickers):
        count["n"] += len(tickers)
    ib.pendingTickersEvent += onTickers

    ib.reqMktData(contract, "", False, False)
    for i in range(4):
        await asyncio.sleep(5)
        t = ib.ticker(contract)
        mp = t.marketPrice() if t else None
        print(f"  t+{(i+1)*5:>2}s  ticker events={count['n']:<6} bid={getattr(t,'bid',None)} "
              f"ask={getattr(t,'ask',None)} marketPrice={mp}")

    ib.cancelMktData(contract)
    ib.pendingTickersEvent -= onTickers

    print("\n--- verdict ---")
    if count["n"] == 0:
        print("  NO market data arrived. Check IDEALPRO FX subscription on the paper account,")
        print("  or whether the account is configured for delayed data only.")
    else:
        print(f"  Market data OK ({count['n']} ticker events). Subscription path works.")

    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())