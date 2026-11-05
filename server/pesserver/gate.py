"""TCP gate server: accepts connections, decodes frames with a Codec, dispatches to Service.

Handlers may return {"_defer": True} for long-poll style requests; they then reply later through
sess.send (also used for server pushes such as CMD_WATCH_MATCHING)."""
import asyncio, logging
from .protocol import Codec, ProtocolError, Message

log = logging.getLogger("pesserver.gate")


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
            while True:
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
            service.disconnected(sess)
            writer.close()
            log.info("disconnect %s", peer)

    return await asyncio.start_server(on_conn, host, port)
