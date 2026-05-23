class StateManager:
    __slots__ = ['cash', 'inventory', 'ticks', 'pending_inventory', 'reserved_cash']

    def __init__(self):
        self.cash              = 0.0
        self.inventory         = {}
        self.ticks             = {}
        self.pending_inventory = {}
        self.reserved_cash     = 0.0