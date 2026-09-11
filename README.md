# SYSTEM ARCHITECTURE & CORE VALUES

**Target Environment:** 4GB RAM Laptop + IB Gateway
**Tech Stack:** Python (asyncio, ib_async, numpy) + Parquet/DuckDB tick store

---

## 1. THE MISSION

To build an institutional-grade, low-latency algorithmic execution framework that operates
flawlessly on highly constrained retail hardware. The system is designed to act as an
unbreakable risk and execution firewall for any future mathematical signal generator.

---

## 2. OUR CORE VALUES

* **Correctness First:** A number that is fast and wrong is worthless. Money state has exactly
  one owner, every invariant is enforced where the data lives, and results are verified by hand
  against real data before they are trusted.
* **Strategy Agnosticism:** The infrastructure does not know, and does not care, what the trading
  strategy is. It handles long, short, or neutral strategies equally. It imposes risk limits
  without dictating market direction.
* **Hardware Realism (The 4GB Constraint):** Every architectural decision is filtered through our
  severe RAM constraints. We reject heavy databases and in-memory datasets. Ticks stream from disk
  in batches; nothing is materialised whole.
* **The "Smart Bodyguard" Principle:** The framework is strictly the execution and risk gatekeeper.
  It assumes the signal generator is stateless, greedy, and prone to bugs. No order can reach the
  exchange without passing through the local Risk Gate.
* **Simulation Parity:** Live and backtest run the same code. Anything that differs between them is
  a seam that can be swapped, never a second implementation. A bug in one is a bug in both.
* **No Premature Complexity:** We build exactly what is needed for the system to survive live
  trading, and nothing more. Speculative generality is a defect.

---

## 3. ARCHITECTURE BLUEPRINT

### The Core (shared by live and backtest)

* **`TradingCore`:** The single wiring point. Owns the tick path, the signal-to-order translation,
  and every callback. Both entrypoints construct one and differ only in what they inject.
* **`StateManager`:** The sole owner of positions, per-currency cash, pending orders, and reserved
  margin. Nothing else mutates money state.
* **`FxRates`:** Currency conversion. Learns each pair's base/quote at registration and values every
  currency in USD using the same tick stream that drives trading. No external rate feed.
* **`RiskGate`:** The ultimate fail-safe. Blocks trades when the market is closed, the kill switch
  is present, notional limits are breached, or free margin is insufficient.
* **`OrderManager`:** Places and tracks orders through to a terminal state. *Coupled directly to the
  Risk Gate so it is physically impossible to place an unchecked order.* A cancellation the system
  did not request is a broker rejection, and suppresses the instrument for `rejectCooldown`.
* **`ContractRegistry`:** The single source of truth for tradable instruments. Qualifies every asset
  with the exchange at startup to prevent downstream routing rejections.
* **`SessionManager`:** One implementation, both paths, all UTC. Encodes the FX week as a market
  fact; strategy trading hours are an optional overlay.
* **`SignalSampler`:** Samples the market on a fixed interval, masks stale instruments, and asks the
  signal source for target positions.
* **`DataFeeder`:** A high-speed pipe passing market ticks from the broker to the core using the
  asset's raw `conId`.

### Live Only

* **`BrokerBoundary`:** Manages the heartbeat, connection lifecycle, and auto-reconnection.
* **`AccountManager`:** Subscribes to account values and positions.
* **`Reconciler`:** The reality check. Seeds the book at startup and audits it against broker truth
  every 5 minutes, skipping any instrument with an order in flight. IB reports the base leg of an
  FX position as currency cash, which the book holds as inventory; the Reconciler removes it before
  seeding or comparing, and leaves positions outside the traded universe out of the book.

### Backtest Only

* **`SimBroker`:** Fills market orders against real historical bid/ask on the following tick.
* **`barReplay` / `dataStore`:** Streams the tick store in batches via DuckDB, merged chronologically
  across all instruments.

---

## 4. STRICT RULES FOR FUTURE DEVELOPMENT

1. **Keep Risk Centralized:** Never put risk logic (session checks, margin, inventory) into the
   signal generator. The Risk Gate handles all rules.
2. **One Owner For Money:** Positions, cash, and margin are mutated through `StateManager` methods
   only. Never write to its fields from outside.
3. **Never Break Simulation Parity:** If live and backtest need to differ, introduce a seam and
   inject it. Never fork the logic.
4. **Never Log the `onTick` Event:** Logging every price update will create fatal I/O bottlenecks.
   Only log executions, drift corrections, and errors.
5. **Never Trade Unverified Contracts:** If an asset is not qualified in the `ContractRegistry` at
   startup, the system cannot and will not trade it.
6. **Time Comes From The Clock:** Never call `datetime.now()` or `time.time()` in trading logic. Use
   the injected clock, or the backtest silently diverges from live.
7. **Release Pending Exactly Once:** Reserved margin and pending inventory are released by
   `OrderManager.onReleased`, fired once when an order's lifecycle ends. Fill, cancel and reject
   callbacks must never release, or a broker that reports an order both rejected and filled will
   corrupt the book.
8. **Absence Is Not Truth:** A broker feed returning nothing is a failure, not a flat account. The
   Reconciler refuses to overwrite positions when the position feed is empty but the book is not.
9. **Convert Before Summing:** Prices and P&L live in an instrument's quote currency. Anything that
   aggregates across instruments must convert to USD first, or a JPY pair will outweigh a USD pair
   by two orders of magnitude.

---

## 5. THE SIGNAL CONTRACT

The strategy is a `SignalSource` — a single method, called on a fixed interval by the sampler.

```
compute(conIds, prices) -> (targets, confidences)
```

* **`conIds`** — instrument identifiers, fixed order, stable across calls.
* **`prices`** — current mid per instrument; `NaN` where the feed has gone stale.
* **`targets`** — the *absolute desired position* per instrument, in base-currency units.
  Negative is short, zero is flat.
* **`confidences`** — alpha score per instrument, currently informational.

An in-process signal source is called synchronously with fresh prices, so there is no transport
delay to guard against. A source that arrives over a network boundary must validate its own
message age before the framework acts on it.

The framework computes the delta against current inventory plus in-flight orders and acts only on
the difference. Restating the same target repeatedly is therefore free and safe.

Maintaining history is the signal source's own responsibility; the caller supplies only the current
snapshot.

---

## 6. THE THREE SEAMS

Live and backtest differ in exactly three injections. Everything downstream is shared.

| Seam | Live | Backtest |
| :--- | :--- | :--- |
| Broker | `ib_async.IB` | `SimBroker` |
| Clock | `WallClock` | `SimClock`, advanced by tick timestamps |
| Signal source | any `SignalSource` | any `SignalSource` |

Because the clock is injected, replay speed has no effect on results: the sampler fires on data
time, not wall time. The same range replayed at any speed produces identical output.

Ticks are applied in timestamp groups. A sample due at T fires before any tick stamped T is applied,
so it sees a synchronous snapshot of everything stamped before T, and the result does not depend on
the order in which the store returns ties.

---

## 7. SETUP & CONFIGURATION

### Prerequisites

* Python 3.10+
* `ib_async`, `numpy`, `duckdb`, `pyarrow`
* Interactive Brokers TWS or IB Gateway running locally.

### Configuration

* **`config.py`** — everything shared by live and backtest: connection, risk limits, sampler
  timing, session hours, margin rate.
* **`backtestConfig.py`** — backtest only: starting cash, universe with conIds, tick store roots,
  data-fetch range, default test range.

Risk limits are denominated in **USD notional**, not units, so they retain their meaning across
instruments and asset classes.

### Kill Switch

Creating the file named by `config.killSwitchFile` in the project root blocks all new orders
immediately. Deleting it resumes. No restart required.

---

## 8. RUNNING

```
pip install -r requirements.txt

python main.py                                            # live / paper, observe-only: no orders
python main.py --trade                                    # live / paper, armed
python backtestMain.py --from DATE --to DATE --name RUN   # backtest, recorded to results/RUN
python analyze.py                                         # statistics for the newest run
python analyze.py --index --sort sharpe                   # browsable index of every run
```

`--source` selects the tick store *and* the instrument universe together, so the two can never
disagree. It accepts any key in `backtestConfig.stores` or a path to a tick CSV. Each source also
carries a profile — trading hours, sampling interval and, where it must differ from live, a
staleness limit — because those differ by asset class.
A range with no data fails immediately rather than replaying to an empty result.

Every backtest is recorded to `results/<name>/`: `fills.csv` written as trades happen,
`equity.parquet`, and `meta.json` holding the config that produced the run. `analyze.py` reads
those files and never imports the trading system, so metrics can change without any risk to the
execution path.

### Data

```
python dukascopyFetch.py                    # FX; skips days already on disk, never writes a partial day
python stockFetch.py --qualify-only         # write stockUniverse.py
python stockFetch.py --limit 100            # equity bars into the ibkr store
```

Diagnostics live in `Diagnostics/` and run from either the project root or that folder: store
coverage, gap detection, forced-fill audit, IB account probe.

---

## 9. CURRENT SYSTEM CONSTRAINTS (BY DESIGN)

1. **Market Orders Only:** Target-position deltas are translated into market orders to guarantee
   fills.
2. **Tick Data Only:** The signal source receives mid prices. If it needs bars, it builds them
   internally.
3. **Sampling Floor:** The tick store is resampled to one second, so sub-second signal is not
   testable against it.
4. **Optimistic Fills:** `SimBroker` crosses the real historical spread and charges commission on
   IBKR's schedule, but does not model partial fills, rejections, or variable latency. Note that
   below roughly 100,000 USD notional the per-order minimum dominates, so `maxOrderNotional`
   directly determines the effective commission rate.
5. **Minimum Order Size:** IDEALPRO routes orders under roughly 20,000 base-currency units as odd
   lots at worse prices. `config.minOrderQty` enforces this floor on trades that increase a
   position; reductions are always allowed so a small position can still be closed.
6. **Margin Assumption:** Position sizing assumes the account carries the margin permissions implied
   by `config.marginRate`, which is a configured estimate rather than a broker-reported figure.
