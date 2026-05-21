import asyncio
import logging

from ib_async import IB
import config

log = logging.getLogger(__name__)


class BrokerBoundary:

    def __init__(self) -> None:
        self._ib      = IB()
        self._running = False
        self.onConnected:    callable = None
        self.onDisconnected: callable = None

    @property
    def ib(self) -> IB:
        return self._ib

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True

        while self._running:
            connected = await self._connect()

            if not connected:
                await asyncio.sleep(config.reconnectDelay)
                continue

            try:
                await self._keepAlive()
            except ConnectionError as e:
                log.warning(e)
            finally:
                self._disconnect()

            if self._running:
                await asyncio.sleep(config.reconnectDelay)

    async def _connect(self) -> bool:
        try:
            await self._ib.connectAsync(
                config.host,
                config.port,
                clientId = config.clientId,
                timeout  = config.connectTimeout,
            )
        except Exception as e:
            log.error("Connection failed: %s", e)
            return False

        if not self._ib.isConnected():
            return False

        self._ib.disconnectedEvent += self._onDisconnected

        if self.onConnected:
            await self.onConnected()

        return True

    def _disconnect(self) -> None:
        try:
            self._ib.disconnectedEvent -= self._onDisconnected
        except Exception:
            pass
        self._ib.disconnect()

    async def _keepAlive(self) -> None:
        while self._running and self._ib.isConnected():
            await asyncio.sleep(config.heartbeatEvery)
            try:
                await self._ib.reqCurrentTimeAsync()
            except Exception as e:
                raise ConnectionError(f"Heartbeat failed: {e}") from e
        raise ConnectionError("Connection dropped.")

    def _onDisconnected(self) -> None:
        if self.onDisconnected:
            asyncio.ensure_future(self.onDisconnected())