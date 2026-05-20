class StateManager:
    __slots__ = ['cash', 'inventory', 'ticks']

    def __init__(self):
        self.cash      = 0.0
        self.inventory = {}
        self.ticks     = {}