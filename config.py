from datetime import time

# --- Broker Boundary ---
host           = "127.0.0.1"
port           = 7497
clientId       = 1
connectTimeout = 10
reconnectDelay = 5
heartbeatEvery = 10

# --- Session Manager ---
sessionStart   = time(9, 30) 
sessionEnd     = time(15, 55) 

# --- Risk Gate ---
killSwitchActive = False
maxOrderQty      = 10
maxPosition      = 50
minCash          = 1000.0
     
# --- Reconciler ---
reconcileInterval = 300 # 5 minutes

# --- Universe ---
# List of tuples: (Symbol, Exchange, Currency)
tradeUniverse = [
    #('SPY', 'SMART', 'USD'),
    #('QQQ', 'SMART', 'USD')
]