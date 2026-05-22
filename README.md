# SYSTEM ARCHITECTURE & CORE VALUES
**Target Environment:** 4GB RAM Laptop + IB Gateway
**Tech Stack:** Python (asyncio, ib_async)

---

## 1. THE MISSION
To build an institutional-grade, low-latency algorithmic execution framework that operates flawlessly on highly constrained retail hardware. The system is designed to act as an unbreakable risk and execution firewall for any future mathematical signal generator.

---

## 2. OUR CORE VALUES

* **Strategy Agnosticism:** The infrastructure does not know, and does not care, what the trading strategy is. It handles long, short, or neutral strategies equally. It imposes risk limits (e.g., max position size) without dictating market direction.
* **Hardware Realism (The 4GB Constraint):** Every architectural decision is filtered through our severe RAM constraints. We reject heavy databases (Redis), OS-level network sockets (ZeroMQ), and Python multiprocessing. We rely exclusively on in-process memory efficiency.
* **The "Smart Bodyguard" Principle:** The Python framework is strictly the execution and risk gatekeeper. It assumes that whatever strategy eventually generates signals is prone to bugs. Through strict dependency injection, no order can reach the exchange without passing through the Risk Gate.
* **No Premature Complexity:** We build exactly what is needed for the system to survive the realities of live trading (e.g., Reconcilers for dropped packets, Contract Registries for routing), and nothing more.

---

## 3. ARCHITECTURE BLUEPRINT

### The Orchestrator (Risk & Execution)
* **`BrokerBoundary`:** Manages the heartbeat, connection lifecycle, and auto-reconnection to IB Gateway.
* **`ContractRegistry`:** The single source of truth. Qualifies every asset with the exchange at startup to prevent downstream routing rejections.
* **`DataFeeder`:** A high-speed, dictionary-free pipe that passes market ticks directly from the broker to the state using the asset's raw `conId`.
* **`RiskGate`:** The ultimate fail-safe. Injected with `SessionManager` and `StateManager`, it structurally blocks trades if the market is closed, the kill-switch is active, or cash/position limits are breached.
* **`OrderManager`:** Handles the mechanics of placing and tracking orders. *Coupled directly to the Risk Gate so it is physically impossible to place an unchecked order.*
* **`Reconciler`:** The reality check. Audits internal state against broker truth every 5 minutes to correct drift, partial fills, or dropped packets.

---

## 4. STRICT RULES FOR FUTURE DEVELOPMENT

1. **Keep Risk Centralized:** Never put risk logic (like session checks or cash balances) into the signal generation logic. The Risk Gate handles all rules.
2. **Never Append Infinite Lists:** Track history using `collections.deque` with strict maximum lengths to prevent Out-Of-Memory (OOM) crashes on constrained hardware.
3. **Never Log the `onTick` Event:** Logging every price update to an older hard drive will create fatal I/O bottlenecks. Only log executions, drift corrections, and errors.
4. **Never Trade Unverified Contracts:** If an asset is not qualified in the `ContractRegistry` at startup, the system cannot and will not trade it.
