"""Transport abstraction: a Codec turns bytes into Message objects. The real PES wire format is
ncl.NclCodec; JsonCodec is a simple length-prefixed JSON codec for tests and the dev client."""
import abc, json, os, struct
from dataclasses import dataclass, field


@dataclass
class Message:
    name: str                       # logical command, e.g. "auth.login"
    seq: int = 0                    # request id (echoed in replies)
    body: dict = field(default_factory=dict)


class Codec(abc.ABC):
    @abc.abstractmethod
    async def read(self, reader) -> "Message | None":
        """Read one message; None on clean EOF. May raise ProtocolError."""

    @abc.abstractmethod
    def encode(self, msg: Message) -> bytes: ...


class ProtocolError(Exception):
    pass


class JsonCodec(Codec):
    """Development/test codec: u32 BE length + UTF-8 JSON {"name","seq","body"}."""
    MAX = 1 << 20

    async def read(self, reader):
        hdr = await _readexactly(reader, 4)
        if hdr is None:
            return None
        (n,) = struct.unpack(">I", hdr)
        if n > self.MAX:
            raise ProtocolError("frame too large")
        raw = await _readexactly(reader, n)
        if raw is None:
            raise ProtocolError("truncated frame")
        try:
            d = json.loads(raw)
            return Message(str(d["name"]), int(d.get("seq", 0)), dict(d.get("body", {})))
        except (ValueError, KeyError, TypeError) as e:
            raise ProtocolError(f"bad json message: {e}")

    def encode(self, msg):
        raw = json.dumps({"name": msg.name, "seq": msg.seq, "body": msg.body}).encode()
        return struct.pack(">I", len(raw)) + raw


_RAWLOG_PATH = None


def record(direction: str, msgid: str, body: dict) -> None:
    """Append one decoded command to PESSERVER_RXLOG (JSONL) for offline replay / status tooling."""
    path = os.environ.get("PESSERVER_RXLOG", "")
    if not path:
        return
    import json, time

    def enc(v):
        if isinstance(v, (bytes, bytearray)):
            return {"__bin__": bytes(v).hex()}
        if isinstance(v, dict):
            return {k: enc(x) for k, x in v.items()}
        if isinstance(v, list):
            return [enc(x) for x in v]
        return v
    try:
        with open(path, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "dir": direction, "msgid": msgid,
                                "body": enc({k: v for k, v in body.items() if not k.startswith("_")})}) + "\n")
    except OSError:
        pass


def _rawlog(data: bytes) -> None:
    """Append raw received bytes to PESSERVER_RAWLOG (a path) when set. Off by default."""
    global _RAWLOG_PATH
    if _RAWLOG_PATH is None:
        _RAWLOG_PATH = os.environ.get("PESSERVER_RAWLOG", "")
    if _RAWLOG_PATH:
        try:
            with open(_RAWLOG_PATH, "ab") as f:
                f.write(data)
        except OSError:
            pass


async def _read_or_none(reader, n):
    import asyncio
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None
        raise ProtocolError("truncated packet") from e


async def _readexactly(reader, n):
    import asyncio
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        if not e.partial and n:
            return None
        return None
