"""The built-in TURN relay against a small RFC 5766 client over real TLS sockets."""
import asyncio, os, socket, ssl, struct, time

import pytest

from pesserver import turn, turnrelay as tr

SECRET, REALM, PORTS = "s3cret", "pes2021", (51200, 51219)


class Client:
    """Just enough of a TURN client: Allocate/Refresh/CreatePermission/ChannelBind/Send/ChannelData."""
    def __init__(self, reader, writer):
        self.r, self.w, self.nonce, self.key, self.user = reader, writer, b"", None, None

    @classmethod
    async def connect(cls, port):
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        return cls(*await asyncio.open_connection("127.0.0.1", port, ssl=ctx))

    async def recv(self):
        head = await asyncio.wait_for(self.r.readexactly(4), 3)
        if head[0] & 0xC0 == 0x40:
            ch, n = struct.unpack("!HH", head)
            return ("channel", ch, (await self.r.readexactly(n + (-n % 4)))[:n])
        rest = await self.r.readexactly(16 + struct.unpack("!H", head[2:4])[0])
        data = head + rest
        method, cls, tid, attrs = tr.parse(data)
        return ("stun", method, cls, {at: v for at, v, _ in attrs}, data, attrs)

    async def request(self, method, attrs, user=None, password=None):
        if user:
            self.user, self.key = user, tr.long_term_key(user, REALM, password)
        tid = os.urandom(12)
        auth = [(tr.A_USERNAME, self.user.encode()), (tr.A_REALM, REALM.encode()), (tr.A_NONCE, self.nonce)] if self.key else []
        self.w.write(tr.build(method, tr.REQUEST, tid, list(attrs) + auth, self.key))
        while True:
            m = await self.recv()
            if m[0] == "stun" and m[4][8:20] == tid:
                return m

    async def auth_request(self, method, attrs, user, password):
        m = await self.request(method, attrs)                 # first try: 401 + realm/nonce
        assert m[2] == tr.ERROR and m[3][tr.A_ERROR_CODE][2:4] == b"\x04\x01"
        self.nonce = m[3][tr.A_NONCE]
        return await self.request(method, attrs, user, password)

    def send(self, peer, data):
        self.w.write(tr.build(tr.SEND, tr.INDICATION, os.urandom(12),
                              [(tr.A_XOR_PEER, tr.xor_addr(*peer)), (tr.A_DATA, data)], fingerprint=False))

    def channel(self, ch, data):
        f = struct.pack("!HH", ch, len(data)) + data
        self.w.write(f + b"\0" * (-len(f) % 4))


def creds(pid, ttl=3600):
    u = f"{int(time.time()) + ttl}:{pid}"
    return u, tr.rest_password(SECRET, u)


def mi_ok(m, key):
    off = next(o for at, _, o in m[5] if at == tr.A_MESSAGE_INTEGRITY)
    return tr.integrity_ok(m[4], off, key)


def test_relay_end_to_end(tmp_path):
    async def go():
        turn._cert(str(tmp_path), ["127.0.0.1"])
        srv = tr.TurnServer(SECRET, REALM, PORTS, lambda local, peer: "127.0.0.1", own_ips={"127.0.0.1"})
        server = await srv.start("127.0.0.1", 0, str(tmp_path / "turn.crt"), str(tmp_path / "turn.key"))
        port = server.sockets[0].getsockname()[1]
        alloc = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0"), (tr.A_MOBILITY_TICKET, b"")]

        # wrong password: refused
        bad = await Client.connect(port)
        m = await bad.auth_request(tr.ALLOCATE, alloc, creds(9)[0], "wrong")
        assert m[2] == tr.ERROR and m[3][tr.A_ERROR_CODE][2:4] == b"\x04\x01"
        # expired username: refused
        m = await bad.request(tr.ALLOCATE, alloc, *creds(9, ttl=-10))
        assert m[2] == tr.ERROR

        a, b = await Client.connect(port), await Client.connect(port)
        ma = await a.auth_request(tr.ALLOCATE, alloc, *creds(1))
        mb = await b.auth_request(tr.ALLOCATE, alloc, *creds(2))
        assert ma[2] == mb[2] == tr.SUCCESS and mi_ok(ma, a.key)
        ticket = ma[3][tr.A_MOBILITY_TICKET]
        assert len(ticket) == 16                               # the game asks for mobility
        relay_a, relay_b = tr.unxor_addr(ma[3][tr.A_XOR_RELAYED]), tr.unxor_addr(mb[3][tr.A_XOR_RELAYED])
        assert relay_a[0] == "127.0.0.1" and PORTS[0] <= relay_a[1] <= PORTS[1] and relay_a != relay_b
        # a second Allocate on the same connection is a mismatch
        assert (await a.request(tr.ALLOCATE, alloc))[2] == tr.ERROR

        # A permits B, B binds a channel to A
        assert (await a.request(tr.CREATE_PERMISSION, [(tr.A_XOR_PEER, tr.xor_addr(*relay_b))]))[2] == tr.SUCCESS
        assert (await b.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, b"\x40\x01\0\0"),
                                                  (tr.A_XOR_PEER, tr.xor_addr(*relay_a))]))[2] == tr.SUCCESS
        # A -> B (Send indication) arrives on B's channel; handed over inside the server
        a.send(relay_b, b"hello B")
        assert await b.recv() == ("channel", 0x4001, b"hello B")
        # B -> A (ChannelData) arrives at A as a Data indication from B's relayed address
        b.channel(0x4001, b"hi A")
        m = await a.recv()
        assert m[1:3] == (tr.DATA, tr.INDICATION) and m[3][tr.A_DATA] == b"hi A"
        assert tr.unxor_addr(m[3][tr.A_XOR_PEER]) == relay_b

        # a real UDP peer: needs a permission, then reaches A
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp.bind(("127.0.0.2", 0))
        udp.sendto(b"no permission yet", relay_a)
        assert (await a.request(tr.CREATE_PERMISSION, [(tr.A_XOR_PEER, tr.xor_addr("127.0.0.2", 1))]))[2] == tr.SUCCESS
        udp.sendto(b"from outside", relay_a)
        m = await a.recv()
        assert m[3][tr.A_DATA] == b"from outside" and tr.unxor_addr(m[3][tr.A_XOR_PEER]) == udp.getsockname()
        a.send(udp.getsockname(), b"out")
        udp.settimeout(2)                                      # (blocking read off the event loop)
        assert (await asyncio.get_running_loop().run_in_executor(None, udp.recvfrom, 100))[0] == b"out"

        # mobility: A's connection drops, a new connection takes the allocation over with the ticket
        a.w.close(); await asyncio.sleep(0.1)
        a2 = await Client.connect(port)
        m = await a2.auth_request(tr.REFRESH, [(tr.A_MOBILITY_TICKET, ticket), (tr.A_LIFETIME, b"\0\0\x02\x58")], *creds(1))
        assert m[2] == tr.SUCCESS and m[3][tr.A_MOBILITY_TICKET] != ticket
        b.channel(0x4001, b"after move")
        assert (await a2.recv())[3][tr.A_DATA] == b"after move"
        # someone else's ticket is refused
        c = await Client.connect(port)
        m = await c.auth_request(tr.REFRESH, [(tr.A_MOBILITY_TICKET, m[3][tr.A_MOBILITY_TICKET])], *creds(7))
        assert m[2] == tr.ERROR

        # Refresh lifetime 0 frees the allocation
        assert (await b.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\0")]))[2] == tr.SUCCESS
        assert relay_b[1] not in srv.allocations
        srv.close(); udp.close()
    asyncio.run(go())


async def _pair(tmp_path):
    """A server and two clients, B with a channel 0x4001 to A's relayed address (what the game sets up)."""
    turn._cert(str(tmp_path), ["127.0.0.1"])
    srv = tr.TurnServer(SECRET, REALM, PORTS, lambda local, peer: "127.0.0.1", own_ips={"127.0.0.1"})
    server = await srv.start("127.0.0.1", 0, str(tmp_path / "turn.crt"), str(tmp_path / "turn.key"))
    port = server.sockets[0].getsockname()[1]
    alloc = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0"), (tr.A_LIFETIME, b"\0\0\0\x3c")]
    a, b = await Client.connect(port), await Client.connect(port)
    ma = await a.auth_request(tr.ALLOCATE, alloc, *creds(1))
    mb = await b.auth_request(tr.ALLOCATE, alloc, *creds(2))
    relay_a, relay_b = tr.unxor_addr(ma[3][tr.A_XOR_RELAYED]), tr.unxor_addr(mb[3][tr.A_XOR_RELAYED])
    assert (await a.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, b"\x40\x01\0\0"),
                                              (tr.A_XOR_PEER, tr.xor_addr(*relay_b))]))[2] == tr.SUCCESS
    assert (await b.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, b"\x40\x01\0\0"),
                                              (tr.A_XOR_PEER, tr.xor_addr(*relay_a))]))[2] == tr.SUCCESS
    return srv, port, a, b, relay_a, relay_b


async def _arrives(client, timeout=0.5):
    try:
        return await asyncio.wait_for(client.recv(), timeout)
    except asyncio.TimeoutError:
        return None


def test_refresh_keeps_the_channels_of_a_long_match(tmp_path, monkeypatch):
    """The game binds its channel once and then only Refreshes the allocation every 30 s. The channel and its
    permission must last as long as those Refreshes do (a match is ~15 minutes; the RFC permission is 5)."""
    monkeypatch.setattr(tr, "PERMISSION_LIFETIME", 1)
    monkeypatch.setattr(tr, "CHANNEL_LIFETIME", 1)

    async def go():
        srv, _, a, b, _, _ = await _pair(tmp_path)
        for _ in range(4):                                     # 2 s: twice the permission's lifetime
            await asyncio.sleep(0.5)
            for c in (a, b):
                assert (await c.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\x3c")]))[2] == tr.SUCCESS
        a.channel(0x4001, b"minute 80")
        assert await _arrives(b) == ("channel", 0x4001, b"minute 80")
        b.channel(0x4001, b"still here")
        assert await _arrives(a) == ("channel", 0x4001, b"still here")

        # without Refreshes they run out as before, and a late Refresh doesn't bring them back
        await asyncio.sleep(1.2)
        assert (await a.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\x3c")]))[2] == tr.SUCCESS
        a.channel(0x4001, b"too late")
        assert await _arrives(b) is None
        srv.close()
    asyncio.run(go())


def test_a_rematch_gets_new_relay_ports(tmp_path):
    """A relayed address freed by a dropped match isn't handed out again while other ports are free."""
    async def go():
        srv, port, a, b, relay_a, relay_b = await _pair(tmp_path)
        for c in (a, b):                                       # the match ends: both allocations go
            assert (await c.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\0")]))[2] == tr.SUCCESS
        assert not srv.allocations
        a2, b2 = await Client.connect(port), await Client.connect(port)
        alloc = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0")]
        new = {tr.unxor_addr((await c.auth_request(tr.ALLOCATE, alloc, *creds(pid)))[3][tr.A_XOR_RELAYED])[1]
               for c, pid in ((a2, 1), (b2, 2))}
        assert len(new) == 2 and not new & {relay_a[1], relay_b[1]}
        srv.close()
    asyncio.run(go())


def test_message_integrity_and_fingerprint_round_trip():
    key = tr.long_term_key("1:2", REALM, "pw")
    msg = tr.build(tr.ALLOCATE, tr.SUCCESS, b"x" * 12, [(tr.A_LIFETIME, b"\0\0\x02\x58")], key)
    method, cls, tid, attrs = tr.parse(msg)
    assert (method, cls, tid) == (tr.ALLOCATE, tr.SUCCESS, b"x" * 12)
    off = next(o for at, _, o in attrs if at == tr.A_MESSAGE_INTEGRITY)
    assert tr.integrity_ok(msg, off, key) and not tr.integrity_ok(msg, off, b"k" * 16)
    assert attrs[-1][0] == tr.A_FINGERPRINT
