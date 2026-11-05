"""The mod's install id: one line before the game's first frame, accounts stored by it."""
import asyncio
from pesserver import gate, ncl, pescmds
from pesserver.accounts import Accounts
from pesserver.ncl import NclCodec
from pesserver.service import Service

ID_A, ID_B = "0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"
STEAM_USER = "410000004100000"          # what every copy with the same CPY.ini sends


def _svc(tmp_path):
    return Service(Accounts(str(tmp_path / "a.db")))


async def _session(port, mod_id, steps):
    """One gate connection: optional id line, then the given commands; returns the replies."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    if mod_id:
        w.write(b"PESID1 " + mod_id.encode() + b"\n")
    out = []
    for i, (msgid, body) in enumerate(steps, 1):
        w.write(ncl.encode_message({"msgid": msgid, "rqid": i, **body}))
        await w.drain()
        msg = await asyncio.wait_for(NclCodec().read(r), 5)
        out.append(msg.body)
    w.close()
    return out


def _login(name=None):
    steps = [("CMD_LOGIN", {"username": STEAM_USER, "hash": "x", "platform": "PC"}), ("CMD_GET_PLAYER_DATA", {})]
    return steps + ([("CMD_CREATE_PLAYER", {"name": name})] if name else [])


def test_two_installs_with_the_same_steam_id_are_two_players(tmp_path):
    svc = _svc(tmp_path)

    async def go():
        srv = await gate.serve(svc, NclCodec, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            a = await _session(port, ID_A, _login("Mohamed"))
            assert a[1]["has_data"] == "NO"                       # first visit: the game asks for a name
            b = await _session(port, ID_B, _login())
            assert b[1]["has_data"] == "NO"                       # same Steam id, other install: asked too
            b = await _session(port, ID_B, _login("Friend"))
            a2 = await _session(port, ID_A, _login())
            assert a2[1]["has_data"] == "YES" and a2[1]["pid"] != (await _session(port, ID_B, _login()))[1]["pid"]
            old = await _session(port, None, _login("Old mod"))   # no id line (older mod): Steam id account
            assert old[0]["result"] == "NOERR"
        finally:
            srv.close()
    asyncio.run(go())
    rows = svc.accounts.db.execute("SELECT username, name, steamid FROM pes_players ORDER BY pid").fetchall()
    assert rows == [("m:" + ID_A, "Mohamed", STEAM_USER), ("m:" + ID_B, "Friend", STEAM_USER), (STEAM_USER, "Old mod", None)]


def test_a_bad_id_line_drops_the_connection(tmp_path):
    svc = _svc(tmp_path)

    async def go():
        srv = await gate.serve(svc, NclCodec, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"PESID1 " + b"Z" * 32 + b"\n")
            assert await asyncio.wait_for(r.read(), 5) == b""
        finally:
            srv.close()
    asyncio.run(go())


def test_prefixed_reader_hands_back_the_first_bytes():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(b"world")
        r.feed_eof()
        p = gate._Prefixed(r, b"hello ")
        assert await p.readexactly(3) == b"hel"
        assert await p.readexactly(5) == b"lo wo"
        assert await p.read(10) == b"rld"
    asyncio.run(go())


def test_account_key():
    from types import SimpleNamespace
    assert pescmds.account_key(SimpleNamespace(mod_id=ID_A), STEAM_USER) == "m:" + ID_A
    assert pescmds.account_key(SimpleNamespace(), STEAM_USER) == STEAM_USER
