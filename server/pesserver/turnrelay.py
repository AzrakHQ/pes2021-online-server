"""A small TURN server (RFC 5766/8656 over TLS/TCP, RFC 8016 mobility) built into the PES server.

What the game's TURN client ("ktc", seen live against coturn) does: TLS over TCP to tls_port, Allocate ->
401 -> Allocate with long-term credentials and MOBILITY-TICKET -> then permissions/channels to the
opponent's relayed address. This module answers exactly that, relays UDP for each allocation, and hands
traffic between two of its own allocations over in memory (both players on the same server is the normal
case; going out to our own public address would need NAT loopback on the host's router).

Credentials follow coturn's TURN REST scheme: username "<expiry>:<pid>", password
base64(HMAC-SHA1(secret, username)); key = MD5(username:realm:password).
"""
import asyncio, base64, binascii, hashlib, hmac, ipaddress, logging, os, socket, ssl, struct, time

log = logging.getLogger("pesserver.turnrelay")

MAGIC = 0x2112A442
# methods
BINDING, ALLOCATE, REFRESH, SEND, DATA, CREATE_PERMISSION, CHANNEL_BIND = 0x001, 0x003, 0x004, 0x006, 0x007, 0x008, 0x009
# classes
REQUEST, INDICATION, SUCCESS, ERROR = 0, 1, 2, 3
# attributes
A_USERNAME, A_MESSAGE_INTEGRITY, A_ERROR_CODE, A_CHANNEL_NUMBER, A_LIFETIME = 0x0006, 0x0008, 0x0009, 0x000C, 0x000D
A_XOR_PEER, A_DATA, A_REALM, A_NONCE, A_XOR_RELAYED = 0x0012, 0x0013, 0x0014, 0x0015, 0x0016
A_REQUESTED_TRANSPORT, A_XOR_MAPPED, A_FINGERPRINT, A_MOBILITY_TICKET = 0x0019, 0x0020, 0x8028, 0x8030

DEFAULT_LIFETIME, MAX_LIFETIME = 600, 3600
PERMISSION_LIFETIME, CHANNEL_LIFETIME = 300, 600
MAX_ALLOCATIONS = 64


# ---- STUN encoding ----------------------------------------------------------------------------------
def msg_type(method, cls):
    return (method & 0xF) | ((method & 0x70) << 1) | ((method & 0xF80) << 2) | ((cls & 1) << 4) | ((cls & 2) << 7)


def split_type(t):
    method = (t & 0xF) | ((t >> 1) & 0x70) | ((t >> 2) & 0xF80)
    cls = ((t >> 4) & 1) | ((t >> 7) & 2)
    return method, cls


def parse(data):
    """(method, class, transaction id, [(attr type, value, offset)]) of one STUN message."""
    t, length, cookie = struct.unpack("!HHI", data[:8])
    if cookie != MAGIC or len(data) < 20 + length:
        raise ValueError("not a STUN message")
    attrs, i = [], 20
    while i + 4 <= 20 + length:
        at, al = struct.unpack("!HH", data[i:i + 4])
        attrs.append((at, data[i + 4:i + 4 + al], i))
        i += 4 + ((al + 3) & ~3)
    return (*split_type(t), data[8:20], attrs)


def xor_addr(ip, port):
    return struct.pack("!BBH", 0, 1, port ^ (MAGIC >> 16)) + bytes(a ^ b for a, b in zip(socket.inet_aton(ip), struct.pack("!I", MAGIC)))


def unxor_addr(v):
    if len(v) < 8 or v[1] != 1:
        raise ValueError("only IPv4 addresses")
    port = struct.unpack("!H", v[2:4])[0] ^ (MAGIC >> 16)
    return socket.inet_ntoa(bytes(a ^ b for a, b in zip(v[4:8], struct.pack("!I", MAGIC)))), port


def _attr(at, value):
    return struct.pack("!HH", at, len(value)) + value + b"\0" * (-len(value) % 4)


def build(method, cls, tid, attrs, key=None, fingerprint=True):
    """A STUN message; with `key`, adds MESSAGE-INTEGRITY (long-term key) before FINGERPRINT."""
    body = b"".join(_attr(at, v) for at, v in attrs)
    t = msg_type(method, cls)
    if key is not None:
        hdr = struct.pack("!HHI", t, len(body) + 24, MAGIC) + tid
        body += _attr(A_MESSAGE_INTEGRITY, hmac.new(key, hdr + body, hashlib.sha1).digest())
    if fingerprint:
        hdr = struct.pack("!HHI", t, len(body) + 8, MAGIC) + tid
        crc = (binascii.crc32(hdr + body) ^ 0x5354554E) & 0xFFFFFFFF
        body += _attr(A_FINGERPRINT, struct.pack("!I", crc))
    return struct.pack("!HHI", t, len(body), MAGIC) + tid + body


def integrity_ok(data, mi_offset, key):
    """Check MESSAGE-INTEGRITY: HMAC over the message up to the attribute, length field covering it."""
    t = struct.unpack("!H", data[:2])[0]
    hdr = struct.pack("!HHI", t, mi_offset - 20 + 24, MAGIC) + data[8:20]
    want = hmac.new(key, hdr + data[20:mi_offset], hashlib.sha1).digest()
    return hmac.compare_digest(want, data[mi_offset + 4:mi_offset + 24])


def long_term_key(username, realm, password):
    return hashlib.md5(f"{username}:{realm}:{password}".encode()).digest()


def rest_password(secret, username):
    return base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()


# ---- server -----------------------------------------------------------------------------------------
class Allocation:
    def __init__(self, server, conn, relay_port, transport, advertised_ip, username, key):
        self.server, self.conn, self.port, self.transport = server, conn, relay_port, transport
        self.advertised = (advertised_ip, relay_port)       # what this client was told (XOR-RELAYED-ADDRESS)
        self.username, self.key = username, key
        self.expires = time.monotonic() + DEFAULT_LIFETIME
        self.permissions = {}                               # peer ip -> expiry
        self.channels = {}                                  # channel number -> ((ip, port), expiry)
        self.ticket = None

    def permitted(self, ip):
        return self.permissions.get(ip, 0) > time.monotonic()

    def channel_for(self, peer):
        now = time.monotonic()
        return next((ch for ch, (p, exp) in self.channels.items() if p == peer and exp > now), None)

    def from_peer(self, data, peer):
        """Datagram for this client from `peer` (via UDP or handed over by another allocation)."""
        if not self.permitted(peer[0]) or self.conn is None:
            return
        ch = self.channel_for(peer)
        if ch is not None:
            frame = struct.pack("!HH", ch, len(data)) + data
            self.conn.write(frame + b"\0" * (-len(frame) % 4))
        else:
            self.conn.write(build(DATA, INDICATION, os.urandom(12),
                                  [(A_XOR_PEER, xor_addr(*peer)), (A_DATA, data)], fingerprint=False))

    def to_peer(self, data, peer):
        if not self.permitted(peer[0]):
            return
        other = self.server.local_allocation(peer)
        if other is not None:                               # both ends on this server: no router hairpin
            other.from_peer(data, self.advertised)
        else:
            self.transport.sendto(data, peer)

    def close(self):
        self.transport.close()


class _RelayProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self.alloc = None

    def datagram_received(self, data, addr):
        if self.alloc is not None:
            self.alloc.from_peer(data, addr[:2])


class TurnServer:
    def __init__(self, secret, realm, relay_ports, advertise, own_ips=()):
        """advertise(local_ip, peer_ip) -> the relay IP to give that client (same rules as the gate's)."""
        self.secret, self.realm = secret, realm
        self.relay_ports = list(range(relay_ports[0], relay_ports[1] + 1))
        self.advertise, self.own_ips = advertise, set(own_ips)
        self.allocations = {}                               # relay port -> Allocation
        self.tickets = {}                                   # mobility ticket -> Allocation
        self.server = None

    # -- lifecycle
    async def start(self, host, port, certfile, keyfile):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        self.server = await asyncio.start_server(self._client, host, port, ssl=ctx)
        self._sweeper = asyncio.get_running_loop().create_task(self._sweep())
        return self.server

    def close(self):
        if self.server is not None:
            self.server.close()
            self._sweeper.cancel()
        for a in list(self.allocations.values()):
            self._free(a)

    async def _sweep(self):
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for a in [a for a in self.allocations.values() if a.expires < now]:
                log.info("TURN allocation %d expired (%s)", a.port, a.username)
                self._free(a)

    def _free(self, a):
        self.allocations.pop(a.port, None)
        if a.ticket:
            self.tickets.pop(a.ticket, None)
        a.close()

    def local_allocation(self, peer):
        a = self.allocations.get(peer[1])
        if a is not None and (peer[0] in self.own_ips or peer == a.advertised):
            return a
        return None

    # -- connections
    async def _client(self, reader, writer):
        conn = _Conn(self, writer)
        try:
            while True:
                head = await reader.readexactly(4)
                if head[0] & 0xC0 == 0x40:                 # ChannelData
                    ch, n = struct.unpack("!HH", head)
                    data = await reader.readexactly(n + (-n % 4))
                    conn.channel_data(ch, data[:n])
                    continue
                length = struct.unpack("!H", head[2:4])[0]
                if length > 0xFFFF - 20:
                    break
                rest = await reader.readexactly(16 + length)
                conn.stun(head + rest)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, OSError):
            pass
        finally:
            conn.closed()
            writer.close()


class _Conn:
    def __init__(self, server, writer):
        self.s, self.writer = server, writer
        self.peer = writer.get_extra_info("peername")[:2]
        self.local = writer.get_extra_info("sockname")[:2]
        self.nonce = base64.b16encode(os.urandom(12)).decode().lower()
        self.alloc = None
        self.pending = False                                # an Allocate is being set up
        self.last = {}                                      # transaction id -> response (retransmits)

    def write(self, data):
        if not self.writer.is_closing():
            self.writer.write(data)

    def closed(self):
        # Keep a mobile allocation for a while: the client may come back with its ticket.
        a, self.alloc = self.alloc, None
        if a is not None:
            a.conn = None
            if a.ticket is None:
                self.s._free(a)
            else:
                a.expires = min(a.expires, time.monotonic() + 60)

    def channel_data(self, ch, data):
        a = self.alloc
        if a is None:
            return
        entry = a.channels.get(ch)
        if entry and entry[1] > time.monotonic():
            a.to_peer(data, entry[0])

    # -- STUN
    def stun(self, data):
        try:
            method, cls, tid, attrs = parse(data)
        except (ValueError, struct.error):
            return
        get = {at: v for at, v, _ in attrs}
        if cls == INDICATION:
            if method == SEND and self.alloc is not None and A_XOR_PEER in get and A_DATA in get:
                try:
                    self.alloc.to_peer(get[A_DATA], unxor_addr(get[A_XOR_PEER]))
                except ValueError:
                    pass
            return
        if cls != REQUEST:
            return
        if tid in self.last:
            return self.write(self.last[tid])
        if self.pending:
            return
        if method == BINDING:
            return self._reply(tid, build(BINDING, SUCCESS, tid, [(A_XOR_MAPPED, xor_addr(*self.peer))]))
        key, username = self._authenticate(data, method, tid, attrs, get)
        if key is None:
            return
        handler = {ALLOCATE: self._allocate, REFRESH: self._refresh,
                   CREATE_PERMISSION: self._permission, CHANNEL_BIND: self._channel_bind}.get(method)
        if handler is None:
            return self._error(method, tid, 400, "Bad Request", key)
        handler(tid, get, attrs, key, username)

    def _reply(self, tid, msg):
        self.last = {tid: msg}
        self.write(msg)

    def _error(self, method, tid, code, reason, key=None, extra=()):
        err = struct.pack("!HBB", 0, code // 100, code % 100) + reason.encode()
        self._reply(tid, build(method, ERROR, tid, [(A_ERROR_CODE, err), *extra], key))

    def _authenticate(self, data, method, tid, attrs, get):
        challenge = [(A_REALM, self.s.realm.encode()), (A_NONCE, self.nonce.encode())]
        mi = next((off for at, _, off in attrs if at == A_MESSAGE_INTEGRITY), None)
        if mi is None or A_USERNAME not in get:
            self._error(method, tid, 401, "Unauthorized", extra=challenge)
            return None, None
        username = get[A_USERNAME].decode("utf-8", "replace")
        if get.get(A_NONCE, b"").decode("utf-8", "replace") != self.nonce:
            self._error(method, tid, 438, "Stale Nonce", extra=challenge)
            return None, None
        try:
            expiry = int(username.split(":", 1)[0])
        except ValueError:
            expiry = 0
        key = long_term_key(username, self.s.realm, rest_password(self.s.secret, username))
        if expiry < time.time() or not integrity_ok(data, mi, key):
            log.info("TURN auth failed for %r from %s", username, self.peer)
            self._error(method, tid, 401, "Unauthorized", extra=challenge)
            return None, None
        return key, username

    def _allocate(self, tid, get, attrs, key, username):
        if self.alloc is not None or self.pending:
            return self._error(ALLOCATE, tid, 437, "Allocation Mismatch", key)
        transport = get.get(A_REQUESTED_TRANSPORT, b"")
        if len(transport) < 1 or transport[0] != 17:
            return self._error(ALLOCATE, tid, 442, "Unsupported Transport Protocol", key)
        if len(self.s.allocations) >= MAX_ALLOCATIONS:
            return self._error(ALLOCATE, tid, 486, "Allocation Quota Reached", key)
        free = [p for p in self.s.relay_ports if p not in self.s.allocations]
        sock = None
        for port in free:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("0.0.0.0", port))
                break
            except OSError:
                sock.close(); sock = None
        if sock is None:
            return self._error(ALLOCATE, tid, 508, "Insufficient Capacity", key)
        # create_datagram_endpoint(sock=...) is a coroutine; finish the allocation when it is ready
        self.pending = True
        asyncio.get_running_loop().create_task(self._finish_allocate(tid, get, key, username, sock))

    async def _finish_allocate(self, tid, get, key, username, sock):
        proto = _RelayProtocol()
        try:
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: proto, sock=sock)
        finally:
            self.pending = False
        port = sock.getsockname()[1]
        ip = self.s.advertise(self.local[0], self.peer[0])
        a = Allocation(self.s, self, port, transport, ip, username, key)
        proto.alloc = a
        self.s.allocations[port] = a
        self.s.own_ips.add(ip)
        self.alloc = a
        lifetime = self._lifetime(get)
        a.expires = time.monotonic() + lifetime
        out = [(A_XOR_RELAYED, xor_addr(ip, port)), (A_XOR_MAPPED, xor_addr(*self.peer)),
               (A_LIFETIME, struct.pack("!I", lifetime))]
        if A_MOBILITY_TICKET in get:
            a.ticket = os.urandom(16)
            self.s.tickets[a.ticket] = a
            out.append((A_MOBILITY_TICKET, a.ticket))
        log.info("TURN allocation %s:%d for %s (%s)%s", ip, port, username, self.peer[0],
                 " mobile" if a.ticket else "")
        self._reply(tid, build(ALLOCATE, SUCCESS, tid, out, key))

    def _lifetime(self, get):
        v = get.get(A_LIFETIME)
        want = struct.unpack("!I", v[:4])[0] if v and len(v) >= 4 else DEFAULT_LIFETIME
        return max(0, min(want, MAX_LIFETIME))

    def _refresh(self, tid, get, attrs, key, username):
        a = self.alloc
        ticket = get.get(A_MOBILITY_TICKET)
        if a is None and ticket:                            # RFC 8016: the client moved to a new connection
            a = self.s.tickets.get(ticket)
            if a is None or a.username.split(":", 1)[-1] != username.split(":", 1)[-1]:
                return self._error(REFRESH, tid, 405, "Mobility Forbidden", key)
            if a.conn is not None and a.conn is not self:
                a.conn.alloc = None
            a.conn, self.alloc = self, a
            self.s.tickets.pop(a.ticket, None)
            a.ticket = os.urandom(16)
            self.s.tickets[a.ticket] = a
            log.info("TURN allocation %d moved to %s", a.port, self.peer)
        if a is None:
            return self._error(REFRESH, tid, 437, "Allocation Mismatch", key)
        lifetime = self._lifetime(get)
        out = [(A_LIFETIME, struct.pack("!I", lifetime))]
        if a.ticket and ticket is not None:
            out.append((A_MOBILITY_TICKET, a.ticket))
        if lifetime == 0:
            self.alloc = None
            self.s._free(a)
        else:
            a.expires = time.monotonic() + lifetime
        self._reply(tid, build(REFRESH, SUCCESS, tid, out, key))

    def _peers(self, attrs):
        return [unxor_addr(v) for at, v, _ in attrs if at == A_XOR_PEER]

    def _permission(self, tid, get, attrs, key, username):
        a = self.alloc
        if a is None:
            return self._error(CREATE_PERMISSION, tid, 437, "Allocation Mismatch", key)
        try:
            peers = self._peers(attrs)
        except ValueError:
            return self._error(CREATE_PERMISSION, tid, 443, "Peer Address Family Mismatch", key)
        if not peers:
            return self._error(CREATE_PERMISSION, tid, 400, "Bad Request", key)
        for ip, _ in peers:
            a.permissions[ip] = time.monotonic() + PERMISSION_LIFETIME
        log.info("TURN permission %d -> %s", a.port, ", ".join(ip for ip, _ in peers))
        self._reply(tid, build(CREATE_PERMISSION, SUCCESS, tid, [], key))

    def _channel_bind(self, tid, get, attrs, key, username):
        a = self.alloc
        if a is None:
            return self._error(CHANNEL_BIND, tid, 437, "Allocation Mismatch", key)
        try:
            (peer,) = self._peers(attrs)
            ch = struct.unpack("!H", get[A_CHANNEL_NUMBER][:2])[0]
        except (ValueError, KeyError, struct.error):
            return self._error(CHANNEL_BIND, tid, 400, "Bad Request", key)
        if not 0x4000 <= ch <= 0x7FFF:
            return self._error(CHANNEL_BIND, tid, 400, "Bad Request", key)
        now = time.monotonic()
        for other, (p, exp) in a.channels.items():
            if exp > now and ((other == ch) != (p == peer)):
                return self._error(CHANNEL_BIND, tid, 400, "Bad Request", key)
        a.channels[ch] = (peer, now + CHANNEL_LIFETIME)
        a.permissions[peer[0]] = now + PERMISSION_LIFETIME
        log.info("TURN channel %d: %#x -> %s:%d", a.port, ch, *peer)
        self._reply(tid, build(CHANNEL_BIND, SUCCESS, tid, [], key))
