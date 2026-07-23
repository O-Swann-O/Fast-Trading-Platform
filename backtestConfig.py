import os
from ib_async import Forex

_HERE = os.path.dirname(os.path.abspath(__file__))

startingCash = 100_000.0

forceSessionActive = False

universe = [
    (Forex("EURUSD", "IDEALPRO"), 12087792),
    (Forex("EURGBP", "IDEALPRO"), 12087807),
    (Forex("EURAUD", "IDEALPRO"), 15016065),
    (Forex("EURNZD", "IDEALPRO"), 47101302),
    (Forex("EURCAD", "IDEALPRO"), 15016068),
    (Forex("EURCHF", "IDEALPRO"), 12087817),
    (Forex("EURJPY", "IDEALPRO"), 14321016),

    (Forex("GBPUSD", "IDEALPRO"), 12087797),
    (Forex("GBPAUD", "IDEALPRO"), 15016075),
    (Forex("GBPNZD", "IDEALPRO"), 47101305),
    (Forex("GBPCAD", "IDEALPRO"), 15016078),
    (Forex("GBPCHF", "IDEALPRO"), 12087826),
    (Forex("GBPJPY", "IDEALPRO"), 14321015),

    (Forex("AUDUSD", "IDEALPRO"), 14433401),
    (Forex("AUDNZD", "IDEALPRO"), 39453424),
    (Forex("AUDCAD", "IDEALPRO"), 15016138),
    (Forex("AUDCHF", "IDEALPRO"), 15016125),
    (Forex("AUDJPY", "IDEALPRO"), 15016133),

    (Forex("NZDUSD", "IDEALPRO"), 39453441),
    (Forex("NZDCAD", "IDEALPRO"), 46189223),
    (Forex("NZDCHF", "IDEALPRO"), 46189224),
    (Forex("NZDJPY", "IDEALPRO"), 39453444),

    (Forex("USDCAD", "IDEALPRO"), 15016062),
    (Forex("USDCHF", "IDEALPRO"), 12087820),
    (Forex("USDJPY", "IDEALPRO"), 15016059),

    (Forex("CADCHF", "IDEALPRO"), 15016234),
    (Forex("CADJPY", "IDEALPRO"), 15016241),

    (Forex("CHFJPY", "IDEALPRO"), 14321010),
]

halfSpread = {cid: 0.0 for _, cid in universe}

dataRoot     = os.path.join(_HERE, "ticks")
ibkrDataRoot = os.path.join(_HERE, "ticks_ibkr")

stores = {
    "dukascopy": dataRoot,
    "ibkr":      ibkrDataRoot,
}

fetchStart = "2024-01-01"
fetchEnd   = "2026-06-16"

testStart  = "2025-01-01"
testEnd    = "2025-02-01"