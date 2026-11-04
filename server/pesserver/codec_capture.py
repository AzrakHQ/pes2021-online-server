"""Capture codec: records every raw byte the real game sends (hex + .bin files) and yields no
messages. This is the slot where the game's native wire codec plugs in once it is known."""
import binascii, logging, os, time
from .protocol import Codec, Message

log = logging.getLogger("pesserver.capture")


class CaptureCodec(Codec):
    DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures")
    n = 0

    def __init__(self):
        os.makedirs(self.DIR, exist_ok=True)
        CaptureCodec.n += 1
        self.path = os.path.join(self.DIR, f"gate_{int(time.time())}_{CaptureCodec.n}.bin")

    async def read(self, reader):
        data = await reader.read(65536)
        if not data:
            return None
        with open(self.path, "ab") as f:
            f.write(data)
        log.info("raw %d bytes: %s%s", len(data), binascii.hexlify(data[:32]).decode(), "..." if len(data) > 32 else "")
        return await self.read(reader)       # keep draining; never produces a Message

    def encode(self, msg: Message) -> bytes:
        return b""
