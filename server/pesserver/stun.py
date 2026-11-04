"""Minimal RFC 5389 STUN server (Binding requests only) over UDP.

Replies with XOR-MAPPED-ADDRESS (and MAPPED-ADDRESS for old clients) so peers can learn
their public ip:port. Also usable as the endpoint-observation source for matchmaking.
"""
import asyncio, logging, socket, struct

log = logging.getLogger("pesserver.stun")

MAGIC = 0x2112A442
BINDING_REQUEST, BINDING_SUCCESS, BINDING_ERROR = 0x0001, 0x0101, 0x0111
ATTR_MAPPED, ATTR_XOR_MAPPED = 0x0001, 0x0020


def build_binding_response(request: bytes, addr):
    if len(request) < 20:
        return None
    mtype, mlen, cookie = struct.unpack("!HHI", request[:8])
    if mtype != BINDING_REQUEST or len(request) < 20 + mlen:
        return None
    tid = request[8:20]
    ip, port = addr[0], addr[1]
    packed = socket.inet_aton(ip)
    xport = port ^ (MAGIC >> 16)
    xip = bytes(a ^ b for a, b in zip(packed, struct.pack("!I", MAGIC)))
    xor_attr = struct.pack("!HHBBH", ATTR_XOR_MAPPED, 8, 0, 1, xport) + xip
    map_attr = struct.pack("!HHBBH", ATTR_MAPPED, 8, 0, 1, port) + packed
    body = xor_attr + map_attr
    return struct.pack("!HHI", BINDING_SUCCESS, len(body), MAGIC) + tid + body


def parse_xor_mapped(resp: bytes):
    """Client-side helper (used by tests / devclient)."""
    mtype, mlen, cookie = struct.unpack("!HHI", resp[:8])
    i = 20
    while i + 4 <= 20 + mlen:
        at, al = struct.unpack("!HH", resp[i:i + 4])
        if at == ATTR_XOR_MAPPED:
            port = struct.unpack("!H", resp[i + 6:i + 8])[0] ^ (MAGIC >> 16)
            ip = bytes(a ^ b for a, b in zip(resp[i + 8:i + 12], struct.pack("!I", MAGIC)))
            return socket.inet_ntoa(ip), port
        i += 4 + ((al + 3) & ~3)
    return None


class StunProtocol(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        resp = build_binding_response(data, addr)
        log.info("UDP %s:%d %dB %s -> %s", addr[0], addr[1], len(data), data.hex(),
                 f"binding-success {len(resp)}B" if resp else "NO REPLY (not a plain Binding Request)")
        if resp:
            self.t.sendto(resp, addr)


class RelayLog(asyncio.DatagramProtocol):
    """Placeholder game-relay endpoint: logs whatever the clients send to the address in CMD_GET_GAMERELAY."""
    def datagram_received(self, data, addr):
        log.info("RELAY UDP %s:%d %dB %s", addr[0], addr[1], len(data), data[:64].hex())


async def serve_relay(host="0.0.0.0", port=7600):
    loop = asyncio.get_running_loop()
    t, _ = await loop.create_datagram_endpoint(RelayLog, local_addr=(host, port))
    return t


async def serve(host="0.0.0.0", port=3478):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(StunProtocol, local_addr=(host, port))
    return transport
