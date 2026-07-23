import os
from ib_async import Forex

_HERE = os.path.dirname(os.path.abspath(__file__))

# --- Broker Boundary ---
host           = "127.0.0.1"
port           = 7497
clientId       = 1
connectTimeout = 10
reconnectDelay = 5
heartbeatEvery = 10

# --- Engine Bridge (external engine, optional) ---
dspHost        = "127.0.0.1"
dspDataPort    = 5000
dspSignalPort  = 5001

# --- Signal Sampler ---
sampleInterval = 1.0
staleLimit     = 5.0
signalLookback = 600
maxSignalAge   = 5

# --- Session ---
tradingHoursUTC = None

# --- Risk Gate ---
killSwitchFile      = os.path.join(_HERE, "KILLSWITCH")
marginRate          = 0.05
maxOrderNotional    = 25_000.0
maxPositionNotional = 100_000.0
minFreeMargin       = 10_000.0
maxTickJump         = 0.05

# --- Reconciler ---
reconcileInterval = 300

# --- Universe ---
tradeUniverse = [
    Forex("EURUSD", "IDEALPRO"),
    Forex("EURGBP", "IDEALPRO"),
    Forex("EURAUD", "IDEALPRO"),
    Forex("EURNZD", "IDEALPRO"),
    Forex("EURCAD", "IDEALPRO"),
    Forex("EURCHF", "IDEALPRO"),
    Forex("EURJPY", "IDEALPRO"),

    Forex("GBPUSD", "IDEALPRO"),
    Forex("GBPAUD", "IDEALPRO"),
    Forex("GBPNZD", "IDEALPRO"),
    Forex("GBPCAD", "IDEALPRO"),
    Forex("GBPCHF", "IDEALPRO"),
    Forex("GBPJPY", "IDEALPRO"),

    Forex("AUDUSD", "IDEALPRO"),
    Forex("AUDNZD", "IDEALPRO"),
    Forex("AUDCAD", "IDEALPRO"),
    Forex("AUDCHF", "IDEALPRO"),
    Forex("AUDJPY", "IDEALPRO"),

    Forex("NZDUSD", "IDEALPRO"),
    Forex("NZDCAD", "IDEALPRO"),
    Forex("NZDCHF", "IDEALPRO"),
    Forex("NZDJPY", "IDEALPRO"),

    Forex("USDCAD", "IDEALPRO"),
    Forex("USDCHF", "IDEALPRO"),
    Forex("USDJPY", "IDEALPRO"),

    Forex("CADCHF", "IDEALPRO"),
    Forex("CADJPY", "IDEALPRO"),

    Forex("CHFJPY", "IDEALPRO"),
]