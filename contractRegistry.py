import logging
from ib_async import Contract

log = logging.getLogger(__name__)

class ContractRegistry:

    def __init__(self, ib) -> None:
        self._ib     = ib
        self._by_id  = {}
        self._by_sym = {}

    @staticmethod
    def _symKey(contract: Contract) -> str:
        if contract.secType == "CASH":
            return f"{contract.symbol}{contract.currency}"
        return contract.symbol

    async def register(self, contract: Contract) -> int:
        qualified = await self._ib.qualifyContractsAsync(contract)

        if not qualified:
            log.error("Registry failed to qualify contract: %s", contract)
            return None

        valid_contract = qualified[0]
        conId = valid_contract.conId

        self._by_id[conId] = valid_contract
        self._by_sym[self._symKey(valid_contract)] = conId
        
        log.info("Registered Contract: %s (ID: %s, Exchange: %s)", 
                 valid_contract.localSymbol, conId, valid_contract.exchange)
                 
        return conId

    def getById(self, conId: int) -> Contract:
        return self._by_id.get(conId)

    def getBySymbol(self, symbol: str) -> Contract:
        conId = self._by_sym.get(symbol)
        if conId:
            return self._by_id.get(conId)
        return None

    def getAll(self) -> list:
        return list(self._by_id.values())