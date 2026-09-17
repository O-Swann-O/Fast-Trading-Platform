"""
Is this run's result distinguishable from luck?

Works on a recorded run, so it costs nothing to re-run and does not touch the store.
It answers one question: given how many round trips this strategy actually made and
how variable they were, could the observed result have come from no edge at all?

  BOOTSTRAP        Resample the round trips with replacement a few thousand times.
                   The spread of the resulting means is the uncertainty on the
                   result. If the 5th percentile is below zero, the backtest does
                   not establish an edge, whatever the headline return says.

  PERMUTATION      Randomly flip the sign of each trade's P&L. Under no edge the
                   mean is zero, so the share of permutations beating the observed
                   mean is a p-value that assumes nothing about the distribution.

  SAMPLE SIZE      From the observed mean and spread, how many round trips would be
                   needed for t = 2. If that number is far beyond what the strategy
                   generates, the backtest cannot settle the question and running it
                   over more history is the only fix.

  COST SENSITIVITY What the result becomes at higher costs. The $2 commission floor
                   is known; slippage on market orders is not, and a result that
                   dies at 1.5x costs was never real.

This says nothing about lookahead. Statistics cannot detect it — a strategy reading
the future looks wonderful by every measure here. Use the delay test for that.

    python Diagnostics/validate.py                  # newest run
    python Diagnostics/validate.py results/myrun
"""
import os
import sys
import argparse

here = os.path.dirname(os.path.abspath(__file__))
root = here if os.path.exists(os.path.join(here, "backtestConfig.py")) else os.path.dirname(here)
sys.path.insert(0, root)

import numpy as np

import analyze

ap = argparse.ArgumentParser()
ap.add_argument("run", nargs="?", default=None, help="run directory (default: newest)")
ap.add_argument("--draws", type=int, default=5000, help="bootstrap / permutation draws")
ap.add_argument("--seed",  type=int, default=0)
args = ap.parse_args()

runDir = args.run
if runDir is None:
    results = os.path.join(root, "results")
    if not os.path.isdir(results):
        sys.exit("No results/ directory. Run a backtest first.")
    runs = [os.path.join(results, d) for d in os.listdir(results)
            if os.path.isdir(os.path.join(results, d))]
    if not runs:
        sys.exit("No runs found in results/.")
    runDir = max(runs, key=os.path.getmtime)
if not os.path.isdir(runDir):
    sys.exit(f"Not a run directory: {runDir}")

meta, times, values, fills = analyze.loadRun(runDir)
tr = analyze.tradeStats(fills, meta.get("marks", {}), meta.get("quoteCcy", {}),
                        meta.get("quoteRates", {}), float(meta.get("commission", 0.0)))


def roundTrips(fills):
    """Realised P&L of each closed exposure, in USD.

    analyze.tradeStats reports only the count, so the per-trade series is rebuilt
    here with the same average-cost rule: every fill that reduces or reverses a
    position closes some of it and realises P&L.
    """
    pos, cost, out = {}, {}, []
    for f in fills:
        cid  = f["conId"]
        q    = f["qty"] if f["action"] == "BUY" else -f["qty"]
        x    = f["price"]
        rate = f.get("quoteRate", 0.0) or 0.0
        p, c = pos.get(cid, 0), cost.get(cid, 0.0)
        if p == 0 or (p > 0) == (q > 0):
            total = p + q
            cost[cid] = ((p * c) + (q * x)) / total if total else 0.0
            pos[cid]  = total
        else:
            closed = min(abs(p), abs(q))
            out.append(closed * (x - c) * (1 if p > 0 else -1) * rate)
            rem = p + q
            if rem != 0 and (rem > 0) != (p > 0):
                cost[cid] = x
            elif rem == 0:
                cost[cid] = 0.0
            pos[cid] = rem
    return np.asarray(out, dtype=np.float64)


rt = roundTrips(fills)

print(f"run          : {os.path.basename(os.path.normpath(runDir))}")
print(f"range        : {meta.get('from','?')} .. {meta.get('to','?')}")
print(f"signal       : {meta.get('signalSource','?')}")
print(f"fills        : {tr.get('fills', 0):,}   closed trades: {rt.size:,}")

if rt.size < 8:
    sys.exit(f"\nOnly {rt.size} round trips. There is nothing to test — any statistic on "
             f"this many trades is noise. Run a longer range or a faster strategy.")

perTrade = float(meta.get("commission", 0.0)) / max(tr.get("fills", 1), 1) * 2.0
net = rt - perTrade if perTrade > 0 else rt.copy()

mu, sd = net.mean(), net.std(ddof=1)
t = mu / (sd / np.sqrt(net.size)) if sd > 0 else 0.0
print(f"\nROUND TRIPS (net of the ${perTrade:.2f} commission already charged per trip)")
print(f"  mean       {mu:>12,.2f}")
print(f"  median     {np.median(net):>12,.2f}")
print(f"  sd         {sd:>12,.2f}")
print(f"  win rate   {np.mean(net > 0):>11.1%}")
print(f"  total      {net.sum():>12,.2f}")
print(f"  t-stat     {t:>12.2f}   (|t| > 2 is the usual bar)")

rng = np.random.default_rng(args.seed)

boot = rng.choice(net, size=(args.draws, net.size), replace=True).mean(axis=1)
lo, hi = np.percentile(boot, [5, 95])
print(f"\nBOOTSTRAP  ({args.draws:,} resamples of the {net.size} trips)")
print(f"  mean per trip, 90% interval   {lo:>10,.2f}  to {hi:>10,.2f}")
print(f"  share of resamples below zero {np.mean(boot <= 0):>10.1%}")

signs = rng.choice([-1.0, 1.0], size=(args.draws, net.size))
perm  = (signs * np.abs(net)).mean(axis=1)
p = float(np.mean(perm >= mu)) if mu >= 0 else float(np.mean(perm <= mu))
print(f"\nPERMUTATION  (sign of each trip randomised)")
print(f"  p-value for the observed mean {p:>10.3f}"
      + ("   <- not distinguishable from no edge" if p > 0.05 else ""))

if sd > 0 and mu != 0:
    need = (2.0 * sd / abs(mu)) ** 2
    print(f"\nSAMPLE SIZE")
    print(f"  round trips needed for |t| = 2 at this effect size: {need:,.0f}")
    print(f"  this run produced {net.size:,}"
          + (f"  -> about {need/net.size:.1f}x more history would be needed"
             if need > net.size else "  -> enough"))

print(f"\nCOST SENSITIVITY  (per round trip, on top of what is already charged)")
for extra in (0.0, perTrade * 0.5, perTrade, perTrade * 2):
    adj = net - extra
    tt = adj.mean() / (adj.std(ddof=1) / np.sqrt(adj.size)) if adj.std(ddof=1) > 0 else 0.0
    print(f"  +${extra:>6,.2f}   total {adj.sum():>12,.2f}   mean {adj.mean():>8,.2f}   t {tt:>6.2f}")

eq = np.asarray(values, dtype=np.float64)
if eq.size > 2:
    r = np.diff(eq) / eq[:-1]
    r = r[np.isfinite(r)]
    if r.size > 2 and r.std() > 0:
        bootS = []
        for _ in range(min(args.draws, 2000)):
            s = rng.choice(r, size=r.size, replace=True)
            bootS.append(s.mean() / s.std() * np.sqrt(len(r)))
        bootS = np.array(bootS)
        print(f"\nEQUITY-CURVE SHARPE (unannualised, from {r.size:,} equity samples)")
        print(f"  observed {r.mean()/r.std()*np.sqrt(r.size):>8.2f}"
              f"   90% interval {np.percentile(bootS,5):>7.2f} to {np.percentile(bootS,95):>7.2f}")

print("\nNone of the above can detect lookahead. Run the delay test for that.")
