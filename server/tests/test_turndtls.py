"""The relay's DTLS leg against a client that frames like the game's ktc layer (2-byte datagram trailer, a type
byte at the end of every plaintext, socket re-created mid-session)."""
import asyncio, os, socket, struct

import pytest

from pesserver import turn, turnrelay as tr, turndtls
from test_turnrelay import Client, creds, SECRET, REALM

pytestmark = pytest.mark.skipif(not turndtls.available(), reason="pyOpenSSL with DTLS not installed")
PORTS = (51240, 51259)


class KtcClient:
    """TURN over DTLS 1.2 the way the game sends it."""
    def __init__(self, port):
        from OpenSSL import SSL
        self.SSL, self.port = SSL, port
        ctx = SSL.Context(SSL.DTLS_METHOD)
        ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
        self.c = SSL.Connection(ctx, None)
        self.c.set_connect_state()
        self.new_socket()
        self.nonce, self.key, self.user = b"", None, None

    def new_socket(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.settimeout(2)
        self.s.connect(("127.0.0.1", self.port))

    def _pump(self, trailer=False):
        while True:
            try:
                d = self.c.bio_read(65536)
            except self.SSL.WantReadError:
                return
            if trailer and d[:1] == b"\x17":            # ktc: +2 bytes counted in the last record's length
                n = struct.unpack("!H", d[11:13])[0]
                d = d[:11] + struct.pack("!H", n + 2) + d[13:] + b"\xab\xcd"
            self.s.send(d)

    def handshake(self):
        for _ in range(20):
            try:
                self.c.do_handshake()
                self._pump()
                return
            except self.SSL.WantReadError:
                self._pump()
                self.c.bio_write(self.s.recv(65536))
        raise AssertionError("no handshake")

    def send(self, data):
        self.c.send(data + b"\x00")
        self._pump(trailer=True)

    def recv(self):
        while True:
            self.c.bio_write(self.s.recv(65536))
            try:
                m = self.c.recv(65536)
            except self.SSL.WantReadError:
                continue
            assert m[-1:] == b"\x00", "server plaintexts must end with the ktc type byte"
            return m[:-1]

    def request(self, method, attrs, user=None, password=None):
        if user:
            self.user, self.key = user, tr.long_term_key(user, REALM, password)
        tid = os.urandom(12)
        auth = [(tr.A_USERNAME, self.user.encode()), (tr.A_REALM, REALM.encode()), (tr.A_NONCE, self.nonce)] if self.key else []
        self.send(tr.build(method, tr.REQUEST, tid, list(attrs) + auth, self.key))
        while True:
            m = self.recv()
            if m[0] & 0xC0 == 0 and m[8:20] == tid:
                method, cls, _, at = tr.parse(m)
                return cls, {a: v for a, v, _ in at}

    def auth_request(self, method, attrs, user, password):
        cls, got = self.request(method, attrs)
        assert cls == tr.ERROR and got[tr.A_ERROR_CODE][2:4] == b"\x04\x01"
        self.nonce = got[tr.A_NONCE]
        return self.request(method, attrs, user, password)


def test_dtls_leg(tmp_path):
    async def go():
        turn._cert(str(tmp_path), ["127.0.0.1"])
        srv = tr.TurnServer(SECRET, REALM, PORTS, lambda local, peer: "127.0.0.1", own_ips={"127.0.0.1"})
        server = await srv.start("127.0.0.1", 0, str(tmp_path / "turn.crt"), str(tmp_path / "turn.key"), dtls_port=0)
        tls_port, dport = server.sockets[0].getsockname()[1], srv.dtls_port
        alloc = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0"), (tr.A_MOBILITY_TICKET, b"")]
        loop = asyncio.get_running_loop()

        # TLS leg: the success carries the dual-route directive pointing at the DTLS port
        t = await Client.connect(tls_port)
        m = await t.auth_request(tr.ALLOCATE, alloc, *creds(5))
        assert m[2] == tr.SUCCESS and m[3][tr.A_SOFTWARE] == f"127.0.0.1:{dport},pes,0".encode()

        # DTLS leg (blocking client off the event loop)
        def leg(pid):
            k = KtcClient(dport); k.handshake()
            cls, got = k.auth_request(tr.ALLOCATE, alloc, *creds(pid))
            assert cls == tr.SUCCESS and tr.A_SOFTWARE not in got      # no directive on the DTLS leg
            return k, tr.unxor_addr(got[tr.A_XOR_RELAYED])
        (a, ra), (b, rb) = await loop.run_in_executor(None, leg, 1), await loop.run_in_executor(None, leg, 2)
        assert ra[0] == "127.0.0.1" and ra != rb

        def bind_and_talk():
            assert a.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, b"\x40\x10\0\0"), (tr.A_XOR_PEER, tr.xor_addr(*rb))])[0] == tr.SUCCESS
            assert b.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, b"\x40\x20\0\0"), (tr.A_XOR_PEER, tr.xor_addr(*ra))])[0] == tr.SUCCESS
            a.send(struct.pack("!HH", 0x4010, 5) + b"hello")
            got = b.recv()
            assert got == struct.pack("!HH", 0x4020, 5) + b"hello"     # unpadded on a datagram leg
            # the game re-creates its socket every 10 s: same session, new source port
            b.new_socket()
            assert b.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\x3c")])[0] == tr.SUCCESS
            a.send(struct.pack("!HH", 0x4010, 3) + b"new")
            assert b.recv() == struct.pack("!HH", 0x4020, 3) + b"new"
        await loop.run_in_executor(None, bind_and_talk)
        assert len(srv.dtls.assocs) == 2
        srv.close()
    asyncio.run(go())


def test_untrail():
    rec = b"\x17\xfe\xfd" + b"\0\x01" + b"\0" * 6 + struct.pack("!H", 12) + b"x" * 10 + b"TT"
    out = turndtls.untrail(rec)
    assert out[11:13] == struct.pack("!H", 10) and out.endswith(b"x" * 10)
    hs = b"\x16\xfe\xff" + b"\0" * 8 + struct.pack("!H", 3) + b"abc"
    assert turndtls.untrail(hs) == hs
