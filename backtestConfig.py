from ib_async import Forex
import config

startingCash = 100_000.0

speed = 1.0

forceSessionActive = False

universe = [
    (Forex("EURUSD", "IDEALPRO"), 12087792),
]

halfSpread = {
    12087792: 0.0,
}

tickPath = "fx_ticks.csv"