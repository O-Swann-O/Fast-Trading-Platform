# SYSTEM ARCHITECTURE & CORE VALUES
**Target Environment:** 4GB RAM Laptop + IB Gateway
**Tech Stack:** Python (asyncio, ib_async) + Native UDP IPC + C++ (Math Engine)

---

## 1. THE MISSION
To build an institutional-grade, low-latency algorithmic execution framework that operates flawlessly on highly constrained retail hardware. The system is designed to act as an unbreakable risk and execution firewall for any future mathematical signal generator.

---

## 2. OUR CORE VALUES

* **Strategy Agnosticism:** The infrastructure does not know, and does not care, what the trading strategy is. It handles long, short, or neutral strategies equally. It imposes risk limits (e.g., max position size) without dictating market direction.
* **Hardware Realism (The 4GB Constraint):** Every architectural decision is filtered through our severe RAM constraints. We reject heavy databases (Redis), OS-level network sockets (ZeroMQ), and Python multiprocessing. We rely exclusively on native OS-level UDP loopback sockets and fixed-width binary structs.
* **The "Smart Bodyguard" Principle:** The Python framework is strictly the execution and risk gatekeeper. It assumes the external C++ math engine is stateless, greedy, and prone to bugs/crashes. Through strict dependency injection and process isolation, no order can reach the exchange without passing through the local Risk Gate.
* **No Premature Complexity:** We build exactly what is needed for the system to survive the realities of live trading (e.g., Reconcilers for dropped packets, Contract Registries for routing, lightweight IPC bridges), and nothing more.

---

## 3. ARCHITECTURE BLUEPRINT

### The Orchestrator (Risk & Execution)
* **`BrokerBoundary`:** Manages the heartbeat, connection lifecycle, and auto-reconnection to IB Gateway.
* **`ContractRegistry`:** The single source of truth. Qualifies every asset with the exchange at startup to prevent downstream routing rejections.
* **`EngineBridge`:** The strict IPC boundary. A zero-allocation, dual-UDP socket bridge that broadcasts 12-byte market ticks to the C++ engine (Port 5000) and catches 16-byte execution signals (Port 5001).
* **`DataFeeder`:** A high-speed, dictionary-free pipe that passes market ticks directly from the broker to the state and the Engine Bridge using the asset's raw `conId`.
* **`RiskGate`:** The ultimate fail-safe. Injected with `SessionManager` and `StateManager`, it structurally blocks trades if the market is closed, the kill-switch is active, or cash/position limits are breached.
* **`OrderManager`:** Handles the mechanics of placing and tracking orders. *Coupled directly to the Risk Gate so it is physically impossible to place an unchecked order.*
* **`Reconciler`:** The reality check. Audits internal state against broker truth every 5 minutes to correct drift, partial fills, or dropped packets.

---

## 4. STRICT RULES FOR FUTURE DEVELOPMENT

1. **Keep Risk Centralized:** Never put risk logic (like session checks, cash balances, or inventory tracking) into the C++ signal generation logic. The Python Risk Gate handles all rules.
2. **Never Break the Binary Contract:** Communication between Python and the math engine is strictly fixed-width binary. Never introduce JSON, strings, or variable-length arrays across the bridge.
3. **Never Log the `onTick` Event:** Logging every price update to an older hard drive will create fatal I/O bottlenecks. Only log executions, drift corrections, and errors.
4. **Never Trade Unverified Contracts:** If an asset is not qualified in the `ContractRegistry` at startup, the system cannot and will not trade it.

---

## 5. THE IPC PROTOCOL (THE C++ CONTRACT)

Communication between the Python Framework and the external C++ DSP Engine occurs strictly over Local UDP Sockets (`127.0.0.1`).

### A. Market Data Stream (Python -> C++)
* **Port:** `5000` (Defined in `config.py`)
* **Format:** 12 bytes (`<I f I`)
* **Behavior:** Python broadcasts every incoming price tick. If the C++ engine is offline, the packets are safely dropped into the void.

| Offset | Type (C / Python) | Size | Description |
| :--- | :--- | :--- | :--- |
| 0 | `uint32_t` / `I` | 4 bytes | `contractId` (The asset identifier) |
| 4 | `float` / `f` | 4 bytes | `price` (Current market price) |
| 8 | `uint32_t` / `I` | 4 bytes | `timestamp` (Unix epoch time) |

### B. Execution Signals (C++ -> Python)
* **Port:** `5001` (Defined in `config.py`)
* **Format:** 16 bytes (`<I i f I`)
* **Behavior:** C++ emits its *ideal absolute target position*. Python receives this, calculates the delta against current inventory, and acts accordingly.

| Offset | Type (C / Python) | Size | Description |
| :--- | :--- | :--- | :--- |
| 0 | `uint32_t` / `I` | 4 bytes | `contractId` (The asset identifier) |
| 4 | `int32_t` / `i` | 4 bytes | `targetPosition` (Negative for short, positive for long) |
| 8 | `float` / `f` | 4 bytes | `confidence` (Alpha score, e.g., 0.0 to 1.0) |
| 12 | `uint32_t` / `I` | 4 bytes | `timestamp` (Unix epoch time) |

---

## 6. SETUP & CONFIGURATION

### Prerequisites
* Python 3.8+
* `ib_async` package (`pip install ib_async`)
* Interactive Brokers TWS or IB Gateway running locally.

### Configuration (`config.py`)
All system parameters are centralized in `config.py`. You **must** configure this file before live deployment.
* **Network:** Set IBKR ports (usually `4001` live, `4002` paper, or `7496/7497` for TWS) and UDP IPC ports.
* **Risk Limits:** Hardcode your `maxPosition`, `maxOrderQty`, and `minCash` boundaries.
* **Universe:** Define the exact assets you intend to trade. The system will ignore anything not explicitly registered here.

---

## 7. CURRENT SYSTEM CONSTRAINTS (BY DESIGN)

To maintain absolute minimum latency and memory footprint, the following constraints are hardcoded into the current build:

1. **Market Orders Only:** The bridge currently translates all delta-signals into aggressive Market Orders to guarantee fills. Limit pricing is not supported in the 16-byte protocol.
2. **Tick Data Only:** The C++ engine receives raw price ticks. If the math engine requires synthetic OHLCV minute-bars, the math engine must construct them internally.
3. **No Shorting Un-Marginable Assets:** The `RiskGate` logic tracks position sizes but assumes your IBKR account has the appropriate margin permissions for negative inventory targets.