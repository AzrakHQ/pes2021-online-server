"""Transport abstraction: a Codec turns bytes into Message objects. The real PES wire format is
ncl.NclCodec."""
import abc, os
from dataclasses import dataclass, field


@dataclass
class Message:
    name: str                       # the command, e.g. "CMD_LOGIN"
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
