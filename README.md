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
  Risk Gate so it is physically impossible to place an unchecked order.*
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
* **`AccountManager`:** Subscribes to account values and positions; seeds the book at startup.
* **`Reconciler`:** The reality check. Audits internal state against broker truth every 5 minutes,
  skipping any instrument with an order in flight.

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
python main.py                                  # live / paper
python backtestMain.py --from DATE --to DATE    # backtest
python storeAudit.py                            # tick store coverage report
python verifyFill.py                            # force one fill, audit the book
```

Backtest data source is selected with `--source`, which accepts any store named in
`backtestConfig.stores` or a path to a tick CSV.

---

## 9. CURRENT SYSTEM CONSTRAINTS (BY DESIGN)

1. **Market Orders Only:** Target-position deltas are translated into market orders to guarantee
   fills.
2. **Tick Data Only:** The signal source receives mid prices. If it needs bars, it builds them
   internally.
3. **Sampling Floor:** The tick store is resampled to one second, so sub-second signal is not
   testable against it.
4. **Optimistic Fills:** `SimBroker` crosses the real historical spread but does not model partial
   fills, rejections, or variable latency. Adequate for retail FX size; revisit if strategy
   behaviour becomes fill-sensitive.
5. **Margin Assumption:** Position sizing assumes the account carries the margin permissions implied
   by `config.marginRate`.