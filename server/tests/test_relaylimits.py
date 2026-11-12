"""The relay's limits (2026-11-12 review): unfinished DTLS handshakes can't crowd players out, TLS connections are
capped and time out, ports held for clients that left are handed to a waiting player, an allocation has a bounded
number of peers, and an Allocate whose client vanished leaves nothing behind."""
import asyncio, socket

import pytest

from pesserver import turn, turnrelay as tr, turndtls
from test_turnrelay import Client, creds, SECRET, REALM
from test_turndtls import KtcClient

ALLOC = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0"), (tr.A_MOBILITY_TICKET, b"")]
PLAIN = [(tr.A_REQUESTED_TRANSPORT, b"\x11\0\0\0")]


async def _server(tmp_path, ports, dtls=False):
    turn._cert(str(tmp_path), ["127.0.0.1"])
    srv = tr.TurnServer(SECRET, REALM, ports, lambda local, peer: "127.0.0.1", own_ips={"127.0.0.1"})
    server = await srv.start("127.0.0.1", 0, str(tmp_path / "turn.crt"), str(tmp_path / "turn.key"),
                             **({"dtls_port": 0} if dtls else {}))
    return srv, server.sockets[0].getsockname()[1]


@pytest.mark.skipif(not turndtls.available(), reason="pyOpenSSL with DTLS not installed")
def test_unfinished_handshakes_cannot_lock_players_out(tmp_path, monkeypatch):
    async def go():
        srv, tls_port = await _server(tmp_path, (51300, 51309), dtls=True)
        leg, loop = srv.dtls, asyncio.get_running_loop()

        def hello():                                     # a ClientHello, never finished
            k = KtcClient(srv.dtls_port)
            try:
                k.c.do_handshake()
            except k.SSL.WantReadError:
                k._pump()
            return k
        strangers = [await loop.run_in_executor(None, hello) for _ in range(turndtls.MAX_STRANGERS + 14)]
        await asyncio.sleep(0.2)
        assert len(leg.assocs) == turndtls.MAX_STRANGERS and not any(a.known or a.ready for a in leg.assocs.values())
        assert all(a.timer is None for a in leg.assocs.values())          # one answer per hello, no retransmits

        # a player (allocated on the TLS leg first, as the game does) gets in even when the table is full
        t = await Client.connect(tls_port)
        assert (await t.auth_request(tr.ALLOCATE, ALLOC, *creds(5)))[2] == tr.SUCCESS
        monkeypatch.setattr(turndtls, "MAX_SESSIONS", len(leg.assocs))

        def player():
            k = KtcClient(srv.dtls_port); k.handshake()
            return k.auth_request(tr.ALLOCATE, ALLOC, *creds(1))[0]
        assert await loop.run_in_executor(None, player) == tr.SUCCESS
        assert len(leg.assocs) == turndtls.MAX_SESSIONS and sum(a.ready for a in leg.assocs.values()) == 1

        # the unfinished ones are forgotten after 15 s; the player's association stays
        for a in leg.assocs.values():
            a.t0 -= turndtls.HANDSHAKE_TIMEOUT + 1
        leg._sweeper.cancel(); leg._sweep()
        assert [a.ready for a in leg.assocs.values()] == [True]
        for k in strangers:
            k.s.close()
        srv.close()
    asyncio.run(go())


def test_tls_connections_are_capped_and_idle_ones_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "MAX_CONNS_PER_IP", 2)
    monkeypatch.setattr(tr, "QUIET_NEW", 0.4)

    async def go():
        srv, port = await _server(tmp_path, (51310, 51311))
        a, b = await Client.connect(port), await Client.connect(port)
        await asyncio.sleep(0.05)
        assert srv.conns == {"127.0.0.1": 2}
        c = await Client.connect(port)
        assert await asyncio.wait_for(c.r.read(1), 2) == b""             # the third: closed at once
        assert (await a.auth_request(tr.ALLOCATE, PLAIN, *creds(1)))[2] == tr.SUCCESS
        assert await asyncio.wait_for(b.r.read(1), 2) == b""             # said nothing for QUIET_NEW: closed
        await asyncio.sleep(0.6)
        assert srv.conns == {"127.0.0.1": 1} and len(srv.allocations) == 1   # the one with an allocation stays
        a.w.close()
        await asyncio.sleep(0.1)
        assert srv.conns == {} and not srv.allocations
        srv.close()
    asyncio.run(go())


def test_a_port_held_for_a_client_that_left_goes_to_a_waiting_player(tmp_path, monkeypatch):
    async def go():
        srv, port = await _server(tmp_path, (51320, 51321))              # two ports

        async def allocate(pid, attrs=ALLOC):
            c = await Client.connect(port)
            m = await c.auth_request(tr.ALLOCATE, attrs, *creds(pid))
            for _ in range(3):                                           # (the game asks again, about once a second)
                m = m or await c.request(tr.ALLOCATE, attrs)
                if m[2] == tr.SUCCESS:
                    return c
                assert m[3][tr.A_ERROR_CODE][2:4] == b"\x05\x08"         # 508: the freed port isn't ours yet
                m = None
                await asyncio.sleep(0.05)
            raise AssertionError("no port")
        a, b = await allocate(1), await allocate(2)
        a.w.close()                                                      # 1 leaves: its mobile allocation lingers
        await asyncio.sleep(0.05)
        assert len(srv.allocations) == 2 and sum(x.conn is None for x in srv.allocations.values()) == 1
        c = await allocate(3)                                            # no free port: 1's goes to player 3
        assert sorted(tr.user_of(x.username) for x in srv.allocations.values()) == ["2", "3"]

        # one player can't hold every port: over the limit, their own left-behind allocation is reused
        monkeypatch.setattr(tr, "MAX_PER_USER", 1)
        c2 = await Client.connect(port)
        m = await c2.auth_request(tr.ALLOCATE, ALLOC, *creds(3))
        assert m[2] == tr.ERROR and m[3][tr.A_ERROR_CODE][2:4] == b"\x04\x56"     # 486 while the first is in use
        c.w.close()
        await asyncio.sleep(0.05)
        d = await allocate(3)
        assert sorted(tr.user_of(x.username) for x in srv.allocations.values()) == ["2", "3"]
        for x in (b, c2, d):
            x.w.close()
        srv.close()
    asyncio.run(go())


def test_an_allocation_has_a_bounded_number_of_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "MAX_PEERS", 2)

    async def go():
        srv, port = await _server(tmp_path, (51330, 51331))
        c = await Client.connect(port)
        assert (await c.auth_request(tr.ALLOCATE, PLAIN, *creds(1)))[2] == tr.SUCCESS
        perm = lambda ip: c.request(tr.CREATE_PERMISSION, [(tr.A_XOR_PEER, tr.xor_addr(ip, 0))])
        assert (await perm("10.0.0.1"))[2] == tr.SUCCESS and (await perm("10.0.0.2"))[2] == tr.SUCCESS
        assert (await perm("10.0.0.1"))[2] == tr.SUCCESS                 # a refresh is not a new peer
        m = await perm("10.0.0.3")
        assert m[2] == tr.ERROR and m[3][tr.A_ERROR_CODE][2:4] == b"\x05\x08"
        bind = lambda ch, ip: c.request(tr.CHANNEL_BIND, [(tr.A_CHANNEL_NUMBER, bytes([0x40, ch, 0, 0])),
                                                          (tr.A_XOR_PEER, tr.xor_addr(ip, 5000))])
        assert (await bind(1, "10.0.0.1"))[2] == tr.SUCCESS and (await bind(2, "10.0.0.2"))[2] == tr.SUCCESS
        assert (await bind(1, "10.0.0.1"))[2] == tr.SUCCESS
        assert (await bind(3, "10.0.0.3"))[2] == tr.ERROR
        c.w.close()
        srv.close()
    asyncio.run(go())


def test_an_allocate_whose_client_left_keeps_no_port():
    class Gone:
        def get_extra_info(self, name):
            return ("127.0.0.1", 5)

        def is_closing(self):
            return True

        def write(self, data):
            raise AssertionError("nothing to say to a closed connection")

    async def go():
        srv = tr.TurnServer(SECRET, REALM, (51340, 51341), lambda local, peer: "127.0.0.1")
        conn = tr._Conn(srv, Gone())
        conn.pending = True
        conn.closed()                                    # the connection ended while the Allocate was being set up
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 51340))
        await conn._finish_allocate(b"t" * 12, {}, b"k", "9:1", sock)
        await asyncio.sleep(0)
        assert not srv.allocations and conn.alloc is None and not conn.pending and sock.fileno() == -1
    asyncio.run(go())
