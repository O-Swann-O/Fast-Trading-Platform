import asyncio
import logging

from ib_async import IB
import config

log = logging.getLogger(__name__)


class BrokerBoundary:

    def __init__(self) -> None:
        self._ib      = IB()
        self._running = False
        self._task    = None
        self._attempt = 0
        self.onConnected:    callable = None
        self.onDisconnected: callable = None

    @property
    def ib(self) -> IB:
        return self._ib

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def run(self) -> None:
        self._running = True
        self._task = asyncio.current_task()

        while self._running:
            connected = await self._connect()

            if not connected:
                log.info("Retrying in %ds.", config.reconnectDelay)
                await asyncio.sleep(config.reconnectDelay)
                continue

            try:
                await self._keepAlive()
            except ConnectionError as e:
                log.warning("Connection lost: %s", e)
            finally:
                self._disconnect()
                if self._running and self.onDisconnected:
                    try:
                        await self.onDisconnected()
                    except Exception as e:
                        log.error("Disconnect cleanup failed: %s", e)

            if self._running:
                log.info("Reconnecting in %ds.", config.reconnectDelay)
                await asyncio.sleep(config.reconnectDelay)

    async def _connect(self) -> bool:
        self._attempt += 1
        log.info("Connecting to %s:%d (clientId %d, attempt %d)...",
                 config.host, config.port, config.clientId, self._attempt)
        try:
            await self._ib.connectAsync(
                config.host,
                config.port,
                clientId = config.clientId,
                timeout  = config.connectTimeout,
            )
        except Exception as e:
            log.error("Connection attempt %d failed: %s", self._attempt, e)
            return False

        if not self._ib.isConnected():
            log.error("Connection attempt %d: handshake did not complete.", self._attempt)
            return False

        log.info("Connected on attempt %d. Heartbeat every %ds.",
                 self._attempt, config.heartbeatEvery)
        self._attempt = 0

        if self.onConnected:
            await self.onConnected()

        return True

    def _disconnect(self) -> None:
        if self._ib.isConnected():
            log.info("Closing broker connection.")
        self._ib.disconnect()

    async def _keepAlive(self) -> None:
        while self._running and self._ib.isConnected():
            await asyncio.sleep(config.heartbeatEvery)
            try:
                await self._ib.reqCurrentTimeAsync()
            except Exception as e:
                raise ConnectionError(f"heartbeat failed ({e})") from e
        if self._running:
            raise ConnectionError("socket dropped between heartbeats")
        raise ConnectionError("shutdown requested")
