import asyncio
import struct
import logging

log = logging.getLogger(__name__)

TICK_FMT = '<I f I'
SIG_FMT  = '<I i f I'

class EngineBridge:
    def __init__(self, host: str, dataPort: int, signalPort: int) -> None:
        self.host             = host
        self.dataPort         = dataPort
        self.signalPort       = signalPort
        self.onTargetPosition = None
        self._transportOut    = None
        
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        
        class SignalReceiver(asyncio.DatagramProtocol):
            def __init__(self, bridge: 'EngineBridge'):
                self.bridge = bridge

            def connection_made(self, transport):
                log.info("EngineBridge listening for signals on UDP %s:%d", self.bridge.host, self.bridge.signalPort)

            def datagram_received(self, data, addr):
                if len(data) == struct.calcsize(SIG_FMT):
                    conId, targetPos, confidence, timestamp = struct.unpack(SIG_FMT, data)
                    if self.bridge.onTargetPosition:
                        self.bridge.onTargetPosition(conId, targetPos, confidence)

        class DataBroadcaster(asyncio.DatagramProtocol):
            def __init__(self, bridge: 'EngineBridge'):
                self.bridge = bridge

            def connection_made(self, transport):
                self.bridge._transportOut = transport
                log.info("EngineBridge broadcasting ticks to UDP %s:%d", self.bridge.host, self.bridge.dataPort)

        await loop.create_datagram_endpoint(
            lambda: SignalReceiver(self),
            local_addr=(self.host, self.signalPort)
        )
        
        await loop.create_datagram_endpoint(
            lambda: DataBroadcaster(self),
            remote_addr=(self.host, self.dataPort)
        )

    def streamTick(self, conId: int, price: float, timestamp: int) -> None:
        if self._transportOut:
            payload = struct.pack(TICK_FMT, conId, price, timestamp)
            self._transportOut.sendto(payload) 