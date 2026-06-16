import os
from ib_async import Forex
import config

_HERE = os.path.dirname(os.path.abspath(__file__))

startingCash = 100_000.0

speed = 1.0

forceSessionActive = False

universe = [
    (Forex("EURUSD", "IDEALPRO"), 12087792),
    #(Forex("GBPUSD", "IDEALPRO"), 12087797),
    #(Forex("USDJPY", "IDEALPRO"), 15016059),
    #(Forex("USDCHF", "IDEALPRO"), 12087820),
    #(Forex("AUDUSD", "IDEALPRO"), 14433401),
    #(Forex("USDCAD", "IDEALPRO"), 15016062),
    #(Forex("NZDUSD", "IDEALPRO"), 39453441),
]

halfSpread = {
    12087792: 0.0,   # EURUSD
    #12087797: 0.0,   # GBPUSD
    #15016059: 0.0,   # USDJPY
    #12087820: 0.0,   # USDCHF
    #14433401: 0.0,   # AUDUSD
    #15016062: 0.0,   # USDCAD
    #39453441: 0.0,   # NZDUSD
}

dataRoot = os.path.join(_HERE, "ticks")
tickPath = os.path.join(_HERE, "fx_ticks.csv")

fetchStart = "2026-01-01"
fetchEnd   = "2026-06-16"