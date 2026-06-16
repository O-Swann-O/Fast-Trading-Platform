from datetime import time
from ib_async import Forex, Stock

# --- Broker Boundary ---
host           = "127.0.0.1"
port           = 7497
clientId       = 1
connectTimeout = 10
reconnectDelay = 5
heartbeatEvery = 10

# --- Engine Bridge ---
dspHost        = "127.0.0.1"
dspDataPort    = 5000
dspSignalPort  = 5001
maxSignalAge   = 5

# --- Session Manager ---
sessionStart   = time(9, 30) 
sessionEnd     = time(15, 55) 

# --- Risk Gate ---
killSwitchActive = False
maxOrderQty      = 10
maxPosition      = 50
minCash          = 1000.0
maxTickJump      = 0.05

# --- Reconciler ---
reconcileInterval = 300 # 5 minutes

# --- Universe ---
# List of tuples: (Symbol, Exchange, Currency)

tradeUniverse = [
    Forex("EURUSD", "IDEALPRO"),
    Forex("GBPUSD", "IDEALPRO"),
    Forex("USDJPY", "IDEALPRO"),
    Forex("USDCHF", "IDEALPRO"),
    Forex("AUDUSD", "IDEALPRO"),
    Forex("USDCAD", "IDEALPRO"),
    Forex("NZDUSD", "IDEALPRO"),
]