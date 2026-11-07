"""The TURN relay's second leg: TURN over DTLS 1.2 on UDP (RFC 7350), the way the game's client ("ktc") speaks it.

The game allocates over TLS first, reads the server's SOFTWARE attribute as a "dual route" directive
"HOST:PORT,<text>,0", then opens DTLS 1.2 to HOST:PORT, allocates again there, closes the TLS connection and
uses only this leg (notes/re/functions/ktcdtls.md, turncycle.md, livetests.md). Its DTLS library is stock
OpenSSL wrapped by Konami's ktc layer, which changes the framing in three ways:

- it appends 2 bytes after the last record of every application-data datagram it sends and adds 2 to that
  record's length field, so a standard DTLS stack drops the record: we cut them off before OpenSSL sees it;
- every plaintext ends with a type byte (0x00 = a plain STUN message or ChannelData frame); we strip it on
  receive and add it on send (records we send are otherwise standard);
- while DSCP is on (the default) the game re-creates its UDP socket every 10 s, so the session keeps its keys
  but arrives from a new source port. A record from an unknown port of a known IP is offered to that IP's
  sessions; the one whose keys authenticate it moves to the new port.

Each DTLS association gets a turnrelay._Conn, so Allocate/Refresh/permissions/channels and relaying are the
same code as on the TLS leg. ChannelData is not padded here (UDP framing). Needs pyOpenSSL.
"""
import asyncio, logging, struct, time

log = logging.getLogger("pesserver.turndtls")

try:
    from OpenSSL import SSL
except ImportError:  # pragma: no cover - checked by available()
    SSL = None

MAX_SESSIONS = 256
IDLE_TIMEOUT = 300                      # s without a record from the client: forget the association
TYPE_PLAIN = b"\x00"


def available():
    return SSL is not None and hasattr(SSL, "DTLS_METHOD")


def untrail(d):
    """Undo ktc's 2-byte trailer on an application-data datagram (no-op for anything else)."""
    if len(d) < 15 or d[0] != 0x17:
        return d
    off = 0
    while off + 13 <= len(d):
        n = struct.unpack("!H", d[off + 11:off + 13])[0]
        end = off + 13 + n
        if end >= len(d):
            if end == len(d) and n > 2:                 # last record: length counts the 2 extra bytes
                return d[:off + 11] + struct.pack("!H", n - 2) + d[off + 13:-2]
            return d
        off = end
    return d


class _Writer:
    """What turnrelay._Conn expects from a stream writer; writes go out as DTLS records."""
    datagram = True

    def __init__(self, assoc):
        self.a = assoc

    def get_extra_info(self, name):
        ep = self.a.ep          # the socket listens on 0.0.0.0: use the address the TLS leg saw for this client
        local = (ep.server.dtls_local.get(self.a.addr[0], ep.local[0]), ep.local[1])
        return {"peername": self.a.addr, "sockname": local}.get(name)

    def is_closing(self):
        return self.a.dead

    def write(self, data):
        self.a.send(data)

    def close(self):
        self.a.close()


class _Assoc:
    """One DTLS association (one client)."""

    def __init__(self, ep, addr):
        self.ep, self.addr, self.dead, self.ready = ep, addr, False, False
        self.c = SSL.Connection(ep.ctx, None)
        self.c.set_accept_state()
        try:
            self.c.set_ciphertext_mtu(1200)
        except (AttributeError, SSL.Error):
            pass
        self.conn, self.timer = None, None
        self.t0 = self.seen = time.monotonic()

    # -- record plumbing
    def flush(self):
        while True:
            try:
                out = self.c.bio_read(65536)
            except SSL.WantReadError:
                return
            if not out:
                return
            self.ep.transport.sendto(out, self.addr)

    def arm(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if self.dead or self.ready:
            return
        try:
            t = self.c.DTLSv1_get_timeout()
        except (AttributeError, SSL.Error):
            t = None
        if t is not None:
            self.timer = asyncio.get_running_loop().call_later(max(t, 0.05), self._retransmit)

    def _retransmit(self):
        self.timer = None
        try:
            self.c.DTLSv1_handle_timeout()
        except SSL.Error:
            pass
        self.flush(); self.arm()

    def feed(self, data):
        """Hand a datagram to OpenSSL; returns the plaintexts it yielded (empty if none, e.g. wrong keys)."""
        self.seen = time.monotonic()
        out = []
        self.c.bio_write(data)
        try:
            if not self.ready:
                try:
                    self.c.do_handshake()
                except SSL.WantReadError:
                    return out
                self.ready = True
                from .turnrelay import _Conn
                self.conn = _Conn(self.ep.server, _Writer(self))
                log.info("TURN dtls %s:%d: %s %s in %.2fs", *self.addr, self.c.get_protocol_version_name(),
                         self.c.get_cipher_name(), time.monotonic() - self.t0)
            while True:
                try:
                    out.append(self.c.recv(65536))
                except SSL.WantReadError:
                    break
                except SSL.ZeroReturnError:
                    self.close()
                    break
        except SSL.Error as e:
            log.info("TURN dtls %s:%d: %s", *self.addr, e)
            self.close()
        finally:
            self.flush(); self.arm()
        return out

    def handle(self, msg):
        if self.conn is None or not msg:
            return
        if msg[-1:] != TYPE_PLAIN:                      # 0x01/0x02 = ktc key/token messages: not used by us
            return
        msg = msg[:-1]
        if msg[0] & 0xC0 == 0x40:                       # ChannelData
            if len(msg) >= 4:
                ch, n = struct.unpack("!HH", msg[:4])
                self.conn.channel_data(ch, msg[4:4 + n])
        else:
            self.conn.stun(msg)

    def send(self, data):
        if self.dead:
            return
        try:
            self.c.send(data + TYPE_PLAIN)
        except SSL.Error as e:
            log.info("TURN dtls %s:%d send: %s", *self.addr, e)
        self.flush()

    def close(self):
        if self.dead:
            return
        self.dead = True
        if self.timer is not None:
            self.timer.cancel()
        if self.conn is not None:
            self.conn.closed()
        if self.ep.assocs.get(self.addr) is self:
            self.ep.assocs.pop(self.addr, None)


class DtlsLeg(asyncio.DatagramProtocol):
    def __init__(self, server, certfile, keyfile):
        self.server, self.assocs, self.transport, self.local = server, {}, None, None
        ctx = SSL.Context(SSL.DTLS_METHOD)
        ctx.use_certificate_chain_file(certfile)
        ctx.use_privatekey_file(keyfile)
        ctx.set_options(getattr(SSL, "OP_NO_QUERY_MTU", 0x1000))
        self.ctx = ctx

    def connection_made(self, transport):
        self.transport = transport
        self.local = transport.get_extra_info("sockname")[:2]
        self._sweeper = asyncio.get_running_loop().call_later(60, self._sweep)

    def _sweep(self):
        now = time.monotonic()
        for a in [a for a in self.assocs.values() if now - a.seen > IDLE_TIMEOUT]:
            a.close()
        self._sweeper = asyncio.get_running_loop().call_later(60, self._sweep)

    def close(self):
        for a in list(self.assocs.values()):
            a.close()
        if self.transport is not None:
            self.transport.close()
            self._sweeper.cancel()

    def datagram_received(self, data, addr):
        addr = addr[:2]
        if not data:
            return
        data = untrail(data)
        a = self.assocs.get(addr)
        if a is not None and data[0] == 22 and len(data) > 13 and data[13] == 1 and a.ready:
            a.close(); a = None                         # a new ClientHello on a live association: start over
        if a is None:
            if data[0] == 0x17:
                a = self._roam(addr, data)
                if a is not None:
                    return
            if data[0] != 22 or len(self.assocs) >= MAX_SESSIONS:
                return                                  # only a handshake opens an association
            a = self.assocs[addr] = _Assoc(self, addr)
        for msg in a.feed(data):
            a.handle(msg)

    def _roam(self, addr, data):
        """The client's socket was re-created: find its association by the keys that authenticate the record."""
        for old, a in list(self.assocs.items()):
            if old[0] != addr[0] or a.dead or not a.ready:
                continue
            msgs = a.feed(data)                         # a wrong session drops it silently (bad GCM tag)
            if msgs:
                self.assocs.pop(old, None)
                a.addr = addr
                self.assocs[addr] = a
                if a.conn is not None:
                    a.conn.peer = addr
                log.info("TURN dtls %s:%d moved to port %d", old[0], old[1], addr[1])
                for m in msgs:
                    a.handle(m)
                return a
        return None


async def start(server, host, port, certfile, keyfile):
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(lambda: DtlsLeg(server, certfile, keyfile),
                                                           local_addr=(host, port))
    return proto
