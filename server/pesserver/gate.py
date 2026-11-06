"""TCP gate server: accepts connections, decodes frames with a Codec, dispatches to Service.

Handlers may return {"_defer": True} for long-poll style requests; they then reply later through
sess.send (also used for server pushes such as CMD_WATCH_MATCHING)."""
import asyncio, logging, re
from .protocol import Codec, ProtocolError, Message, _read_or_none

log = logging.getLogger("pesserver.gate")

# The mod starts each gate connection with one line naming its install, before the game's first
# frame: b"PESID1 <32 hex>\n" (40 bytes, mod 0.3.0, hello version 2) or b"PESID2 <32 hex> <features>\n"
# (mod 0.4.0+, hello version 3; "math1" = the game gets the mod's same-math functions). The game's own
# first bytes are a frame header XORed with 5b9f2e64 (first byte 0x5b), so they can't be confused.
# Accounts are then stored by this id instead of the Steam id, which every copy that doesn't really
# run through Steam shares; matchmaking pairs only games with the same math (pesmatch.py).
ID_MAGIC, ID_LEN = b"PESID1 ", 40
ID2_MAGIC, ID2_MAX = b"PESID2 ", 128


class _Prefixed:
    """A StreamReader whose first bytes were already read (the start of the game's first frame)."""

    def __init__(self, reader, head):
        self._r, self._head = reader, head

    async def readexactly(self, n):
        if not self._head:
            return await self._r.readexactly(n)
        take, self._head = self._head[:n], self._head[n:]
        if len(take) == n:
            return take
        try:
            return take + await self._r.readexactly(n - len(take))
        except asyncio.IncompleteReadError as e:
            raise asyncio.IncompleteReadError(take + e.partial, n) from None

    async def read(self, n=-1):
        if self._head:
            take, self._head = (self._head, b"") if n < 0 else (self._head[:n], self._head[n:])
            return take
        return await self._r.read(n)


async def take_player_id(reader, sess):
    """Read the mod's id line if the connection starts with one (sets sess.mod_id). Returns the reader
    to use for the game's frames, or None if the client closed without sending anything."""
    head = await _read_or_none(reader, 24)
    if head is None:
        return None
    if head.startswith(ID_MAGIC):
        line = head + await reader.readexactly(ID_LEN - len(head))
        m = re.fullmatch(rb"PESID1 ([0-9a-f]{32})\n", line)
    elif head.startswith(ID2_MAGIC):
        try:
            line = head if b"\n" in head else head + await reader.readuntil(b"\n")
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
            raise ProtocolError("bad player id line") from None
        m = re.fullmatch(rb"PESID2 ([0-9a-f]{32})((?: [a-z0-9]{1,16}){0,8})\n", line) if len(line) <= ID2_MAX else None
    else:
        return _Prefixed(reader, head)
    if not m:
        raise ProtocolError("bad player id line")
    sess.mod_id = m.group(1).decode()
    sess.mod_features = frozenset(m.group(2).decode().split()) if m.lastindex == 2 else frozenset()
    log.info("mod install id %s… from %s%s", sess.mod_id[:8], sess.peer_ip,
             f" ({' '.join(sorted(sess.mod_features))})" if sess.mod_features else "")
    return reader


async def serve(service, codec_factory, host="0.0.0.0", port=10000, idle=300):
    from .service import Session

    async def on_conn(reader, writer):
        peer = writer.get_extra_info("peername")
        codec: Codec = codec_factory()
        lock = asyncio.Lock()

        async def send(msg: Message):
            async with lock:
                writer.write(codec.encode(msg))
                await writer.drain()

        sess = Session(peer[0], send)
        sess.local_ip = writer.get_extra_info("sockname")[0]   # the address this client reached us on
        sess.close = writer.close                               # admin "kick"
        sess.codec = codec
        log.info("connect %s", peer)
        try:
            reader = await asyncio.wait_for(take_player_id(reader, sess), timeout=idle)
            while reader is not None:
                msg = await asyncio.wait_for(codec.read(reader), timeout=idle)
                if msg is None:
                    break
                try:
                    reply = await service.handle(sess, msg)
                except Exception:  # a handler bug must not drop the client's session
                    log.exception("HANDLER CRASH on %s", msg.name)
                    reply = Message(msg.name, msg.seq, {"result": "ERR_DATABASE",
                                                       "_kind": msg.body.get("_kind", 0x60),
                                                       "_seq": msg.body.get("_seq", 1)})
                if reply.body.get("_defer"):
                    continue
                await send(reply)
        except asyncio.TimeoutError:
            log.info("idle timeout %s", peer)
        except (ProtocolError, ConnectionError) as e:
            log.warning("protocol/connection error from %s: %s", peer, e)
        finally:
            try:
                from . import events
                events.disconnected(service, sess)
            except Exception:  # noqa: BLE001
                log.exception("events.disconnected")
            try:
                from . import pesrooms
                pesrooms.disconnected(service, sess)
            except Exception:  # noqa: BLE001
                log.exception("pesrooms.disconnected")
            service.disconnected(sess)
            writer.close()
            log.info("disconnect %s", peer)

    return await asyncio.start_server(on_conn, host, port)
