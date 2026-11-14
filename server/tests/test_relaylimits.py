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
            assert m[2] == tr.SUCCESS, m[3]                              # at the first ask: the game is not made to retry
            return c
        a, b = await allocate(1), await allocate(2)
        a.w.close()                                                      # 1 leaves: its mobile allocation lingers
        await asyncio.sleep(0.05)
        assert len(srv.allocations) == 2 and sum(x.conn is None for x in srv.allocations.values()) == 1
        c = await allocate(3)                                            # no free port: 1's goes to player 3
        assert sorted(tr.user_of(x.username) for x in srv.allocations.values()) == ["2", "3"]

        # one player can't hold every port: over the limit their oldest allocation goes, also one whose
        # connection is still open (after a bad line the game starts TURN over without closing the old one)
        monkeypatch.setattr(tr, "MAX_PER_USER", 1)
        first = next(x for x in srv.allocations.values() if tr.user_of(x.username) == "3")
        d = await allocate(3)
        mine = [x for x in srv.allocations.values() if tr.user_of(x.username) == "3"]
        assert len(mine) == 1 and mine[0] is not first and len(srv.allocations) == 2
        m = await c.request(tr.REFRESH, [(tr.A_LIFETIME, b"\0\0\0\x3c")])
        assert m[2] == tr.ERROR                                          # the old connection has no allocation now
        for x in (b, c, d):
            x.w.close()
        srv.close()
    asyncio.run(go())


def test_closing_the_relay_closes_its_clients(tmp_path):
    async def go():
        srv, port = await _server(tmp_path, (51350, 51351))
        c = await Client.connect(port)
        assert (await c.auth_request(tr.ALLOCATE, PLAIN, *creds(1)))[2] == tr.SUCCESS
        srv.close()
        assert await asyncio.wait_for(c.r.read(1), 2) == b"" and not srv.allocations
    asyncio.run(go())


def test_the_relay_restarts_for_a_new_address_only_when_nobody_plays_through_it(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pesserver import netsetup
    monkeypatch.setattr(turn, "STATE", {"server": None, "dir": None, "secret": None, "cert_der": None, "ips": ()})
    monkeypatch.setattr(turn, "TLS_PORT", 51360); monkeypatch.setattr(turn, "DTLS_PORT", 51361)
    monkeypatch.setattr(turn, "RELAY_PORTS", (51362, 51363))
    monkeypatch.delenv("TURN", raising=False)
    monkeypatch.setattr(netsetup, "STATE", {**netsetup.STATE, "lan": "127.0.0.1", "wan": None})
    svc = SimpleNamespace(online={})

    async def go():
        assert await turn.start(svc, str(tmp_path), "127.0.0.1", None)
        first = turn.STATE["server"]
        assert turn.STATE["ips"] == ("127.0.0.1",) and not await turn.readdress(svc, str(tmp_path))   # nothing new
        c = await Client.connect(51360)
        user = turn.credentials(1)
        c.nonce = (await c.request(tr.ALLOCATE, PLAIN))[3][tr.A_NONCE]
        c.user, c.key = user[0], tr.long_term_key(user[0], turn.REALM, user[1])
        assert (await c.request(tr.ALLOCATE, PLAIN))[2] == tr.SUCCESS
        netsetup.STATE["wan"] = "81.2.69.160"                            # the public address was found late
        task = asyncio.ensure_future(turn.readdress(svc, str(tmp_path), poll=0.05))
        await asyncio.sleep(0.3)
        assert turn.STATE["server"] is first and not task.done()         # a match is on it: not now
        assert not await turn.readdress(svc, str(tmp_path))              # (one wait at a time)
        c.w.close()
        assert await asyncio.wait_for(task, 5) is True
        assert turn.STATE["server"] is not first and turn.STATE["ips"] == ("127.0.0.1", "81.2.69.160")
        netsetup.STATE["wan"] = None                                     # the address goes away again: no restart
        second = turn.STATE["server"]
        assert not await turn.readdress(svc, str(tmp_path)) and turn.STATE["server"] is second
        turn.stop()
    asyncio.run(go())


def test_a_relay_restart_that_fails_is_tried_again(tmp_path, monkeypatch):
    """Before, a restart for a new address that hit a port not free yet left the relay off until the next
    address change: every game got an empty TURN list."""
    from types import SimpleNamespace
    from pesserver import netsetup
    monkeypatch.setattr(turn, "STATE", {"server": None, "dir": None, "secret": None, "cert_der": None, "ips": ()})
    monkeypatch.setattr(turn, "TLS_PORT", 51370); monkeypatch.setattr(turn, "DTLS_PORT", 51371)
    monkeypatch.setattr(turn, "RELAY_PORTS", (51372, 51373))
    monkeypatch.setattr(turn, "RESTART_RETRY", (0.05, 0.05))
    monkeypatch.delenv("TURN", raising=False)
    monkeypatch.setattr(netsetup, "STATE", {**netsetup.STATE, "lan": "127.0.0.1", "wan": None})
    svc = SimpleNamespace(online={})

    async def go():
        assert await turn.start(svc, str(tmp_path), "127.0.0.1", None)
        real_start, tries = turn.start, []

        async def busy_then_free(*a):                                    # the port isn't free yet, twice
            tries.append(1)
            return False if len(tries) <= 2 else await real_start(*a)
        monkeypatch.setattr(turn, "start", busy_then_free)
        netsetup.STATE["wan"] = "81.2.69.160"
        assert await asyncio.wait_for(turn.readdress(svc, str(tmp_path)), 5) is True
        assert len(tries) == 3 and turn.running() and "81.2.69.160" in turn.STATE["ips"]
        turn.stop()
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
