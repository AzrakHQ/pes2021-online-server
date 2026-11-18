"""The rest of the 2026-11-12 review, game side: a lobby room isn't left waiting for someone who is gone, entry
numbers stay unique, replay uploads don't fill the log, garbage on the wire is a short line (not a traceback),
Divisions shows the real rating, a deleted competition leaves no "seen" marks, a recording cut by a restart is
closed, only a logged-in connection can search or use rooms, a reconnect must come from the same install, old match
rooms go, and table setup runs once. Also the 2026-11-18 review (at the end): what the open web API may do, limits on
what strangers can send, a lobby match survives a blink, the router's ports and the relay's bookkeeping."""
import asyncio, hashlib, os, struct, threading, time, zlib
from types import SimpleNamespace
import msgpack
import pytest
from pesserver import accounts, announce, httpgate, ncl, netsetup, pescmds, pescoop, pesdivisions, pesmatch, pesmatchday, pesreplay, pesrooms, pestourney, turndtls, turnrelay  # noqa: F401
from pesserver.accounts import Accounts
from pesserver.protocol import ProtocolError
from pesserver.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    db = pescmds._db(s)
    for pid in (1, 2, 3):
        db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, f"u{pid}", f"P{pid}"))
    db.commit()
    return s


def _sess(svc, pid, **kw):
    s = SimpleNamespace(pes_pid=pid, pes_username=f"u{pid}", peer_ip="192.168.1.%d" % (10 + pid), **kw)
    svc.online[pid] = s
    return s


# ---- lobby rooms -------------------------------------------------------------------------------------
def test_room_is_free_again_when_the_one_still_in_the_match_screen_leaves(svc):
    a, b = _sess(svc, 1), _sess(svc, 2)
    d = lambda s, m, r=None: pescmds._dispatch(svc, s, m, r or {})
    rid = d(a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
    d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})
    for s in (a, b):
        d(s, "CMD_ENTRY_GAME", {"entry": "TRUE"})
    d(a, "CMD_SET_GAMEMEMBER")
    room = pesrooms._st(svc)["rooms"][rid]
    assert room["state"] == "SETENV"
    d(a, "CMD_LEAVE_GAME")
    assert room["state"] == "SETENV"                     # B is still in the match
    d(b, "CMD_LEAVE_ROOM")                               # B goes without leaving the match first
    assert room["state"] == "WAITING" and room["members"] == [1] and room["entries"] == {}
    c = _sess(svc, 3)
    assert d(c, "CMD_JOIN_ROOM_NTL", {"room_id": rid})["result"] == "NOERR"


def test_entry_numbers_are_not_given_twice(svc):
    a, b = _sess(svc, 1), _sess(svc, 2)
    d = lambda s, m, r=None: pescmds._dispatch(svc, s, m, r or {})
    rid = d(a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
    d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})
    d(a, "CMD_ENTRY_GAME", {"entry": "TRUE"}); d(b, "CMD_ENTRY_GAME", {"entry": "TRUE"})
    d(a, "CMD_ENTRY_GAME", {"entry": "FALSE"}); d(a, "CMD_ENTRY_GAME", {"entry": "TRUE"})
    nums = [e["entryNum"] for e in pesrooms._st(svc)["rooms"][rid]["entries"].values()]
    assert sorted(nums) == [1, 2]                          # the free number again, never above the room's size


# ---- the log and the wire ----------------------------------------------------------------------------
def test_a_replay_upload_is_logged_as_its_size():
    chunks = ["x" * 5000, b"y" * 7000]
    assert ncl._short(chunks) == "<2 items, 12000 bytes>"
    assert ncl._short([1, 2, 3]) == [1, 2, 3] and ncl._short(["a", "b"]) == ["a", "b"]
    assert ncl._short("z" * 601) == "<601 chars>" and ncl._short("ok") == "ok"


def test_garbage_in_a_frame_is_a_protocol_error_not_a_crash():
    async def read(plain):
        r = asyncio.StreamReader()
        r.feed_data(ncl.frame(ncl.KIND_EASY, 1, ncl.bf_encrypt(ncl._pad(plain))))
        r.feed_eof()
        return await ncl.NclCodec().read(r)
    bad_zlib = struct.pack(">IIB", 5, 5, 1) + b"nope!"                    # says "compressed", isn't
    bad_pack = struct.pack(">IIB", 3, 3, 0) + b"\xc1\xc1\xc1"             # not msgpack
    bad_rqid = msgpack.packb({"msgid": "CMD_X", "rqid": "seven"})
    for plain in (bad_zlib, bad_pack, struct.pack(">IIB", len(bad_rqid), len(bad_rqid), 0) + bad_rqid):
        with pytest.raises(ProtocolError):
            asyncio.run(read(plain))


# ---- Divisions, tournaments, replays -----------------------------------------------------------------
def test_divisions_shows_the_players_rating(svc):
    svc.accounts.db.execute("UPDATE pes_players SET rating=1234 WHERE pid=1")
    rec = msgpack.unpackb(pesdivisions.division_info(svc, 1)["match_record"])
    assert rec["rating"] == 1234.0


def test_a_deleted_competition_or_player_leaves_no_seen_marks(svc):
    now = time.time()
    c = pestourney.competitions(svc)[0]
    db = svc.accounts.db
    db.execute("INSERT INTO pes_tourney_seen (pid, compe_id) VALUES (1, ?), (2, ?)", (c["compe_id"], c["compe_id"]))
    pestourney.forget(svc, 2)
    assert db.execute("SELECT pid FROM pes_tourney_seen").fetchall() == [(1,)]
    pestourney.delete(svc, c["compe_id"])
    assert db.execute("SELECT COUNT(*) FROM pes_tourney_seen").fetchone()[0] == 0
    assert now                                             # (the next competition reuses the number)


def test_a_recording_cut_by_a_restart_is_closed(svc, monkeypatch):
    a = _sess(svc, 1); _sess(svc, 2)
    pesmatch._state(svc)["rooms"][4] = {"owner": 1, "members": [1, 2], "t": time.time()}
    mid = pescmds.dispatch(svc, a, "CMD_GET_MATCHID", {})["match_id"]
    pescmds.dispatch(svc, a, "CMD_START_SEND_GAME_SEQUENCE_DATA", {"pid": 1})
    with pesreplay._lock:
        pesreplay._chunk(svc, mid, b"abc")
    monkeypatch.setattr(pesreplay, "_live", {})          # the server restarted: nothing is being recorded
    meta = dict(pesreplay.recordings(svc))[mid]
    assert meta["closed"] is True and meta["n"] == 2
    last = pesreplay._dec(open(os.path.join(pesreplay.folder(svc), mid, "2.enc"), "rb").read())
    assert last.endswith(b"END_OF_DATA")
    assert dict(pesreplay.recordings(svc))[mid]["n"] == 2  # once, not on every look


# ---- who may do what ---------------------------------------------------------------------------------
def test_only_a_logged_in_connection_can_search_or_use_rooms(svc):
    for who in (None, SimpleNamespace()):                 # the web API, and a connection before its login
        for m in ("CMD_START_MATCHING", "CMD_WATCH_MATCHING", "CMD_CREATEJOIN_ROOM", "CMD_JOIN_LOBBY",
                  "CMD_START_COOP_MATCHING", "CMD_REQWATCHL_ROOMLIST", "CMD_SEND_TEXTCHAT"):
            assert pescmds._dispatch(svc, who, m, {"pid": "1"}) == {"result": "ERR_LOGIN_FAILED"}, m
    st = pesmatch._state(svc)
    assert st["queue"] == [] and st["watch"] == {} and not pesrooms._st(svc)["rooms"]
    assert pescmds.NEEDS_PLAYER <= set(pescmds.HANDLERS)  # (every name is a real command)
    assert pescmds._dispatch(svc, None, "CMD_GET_LOBBYLIST", {})["result"] == "NOERR"


def test_a_reconnect_must_come_from_the_same_install(svc):
    mine, other = "a" * 32, "b" * 32
    svc.accounts.db.execute("UPDATE pes_players SET username=? WHERE pid=1", ("m:" + mine,))
    rc = lambda **kw: pescmds._dispatch(svc, SimpleNamespace(peer_ip="41.0.0.1", **kw), "CMD_RECONNECT", {"pid": 1})["result"]
    assert rc(mod_id=other) == "ERR_LOGIN_FAILED"         # another install naming this player
    assert rc() == "ERR_LOGIN_FAILED"                     # no id at all (the mod sends it on every connection)
    assert rc(mod_id=mine) == "NOERR"
    assert pescmds._dispatch(svc, SimpleNamespace(peer_ip="9.9.9.9"), "CMD_RECONNECT", {"pid": 2})["result"] == "NOERR"   # (no mod: as before)


# ---- housekeeping ------------------------------------------------------------------------------------
def test_old_match_rooms_go_and_the_newest_is_still_found(svc):
    rooms = pesmatch._state(svc)["rooms"]
    old = pesmatch.new_room(svc, owner=1, members=[1, 2])
    rooms[old]["t"] -= pesmatch.ROOM_KEEP + 1
    recent = pesmatch.new_room(svc, owner=1, members=[1, 3])
    rooms[recent]["t"] -= 600
    new = pesmatch.new_room(svc, owner=2, members=[2, 3])
    assert set(rooms) == {recent, new} and new > recent > old
    assert pesmatch._room_of(svc, 1)[0] == recent and pesmatch._room_of(svc, 2)[0] == new


def test_table_setup_runs_once_per_database(svc):
    calls = []

    @accounts.once
    def _setup(db):
        calls.append(db)
        db.execute("CREATE TABLE IF NOT EXISTS t_once (x)")
    db = svc.accounts.db
    _setup(db); _setup(db)
    assert len(calls) == 1
    other = Accounts().db
    _setup(other)
    assert len(calls) == 2
    db.execute("INSERT INTO t_once VALUES (1)")           # inside a transaction: a rollback could undo the tables,
    db.ready.discard(_setup.__module__)
    _setup(db); _setup(db)                                # so it is not remembered
    assert len(calls) == 4
    db.commit()


# ---- 2026-11-18 ------------------------------------------------------------------------------------------
def _web(svc, msgid, **form):
    """A POST to the web API (port 8080, open to the internet): no connection, every value is text."""
    return pescmds.dispatch(svc, None, msgid, {k: str(v) for k, v in form.items()})


def test_the_web_api_cant_act_for_a_player(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, m, *a: sent.append(m))
    _sess(svc, 1)
    pesmatch._state(svc)["ntl"][1] = {"reflexive_addr": "home address"}
    for m in ("CMD_SEND_NOTICE", "CMD_SEND_SYNC_NOTICE", "CMD_SEND_SYSTEM_NOTICE"):
        assert _web(svc, m, pid_list="1", data="x")["result"] == "ERR_LOGIN_FAILED"
    assert sent == []
    assert "reflexive_addr" not in _web(svc, "CMD_GET_IPANDPORT_NTL", pid=1)
    db = svc.accounts.db
    assert _web(svc, "CMD_LOGIN", username="stranger")["result"] == "ERR_LOGIN_FAILED"
    assert not db.execute("SELECT 1 FROM pes_players WHERE username='stranger'").fetchone()
    assert _web(svc, "CMD_FINISH_SEASON")["result"] == "ERR_LOGIN_FAILED"
    assert _web(svc, "CMD_JOIN_MATCHDAY", team_id=101, sp_gameplan_data="x" * 1000)["result"] == "ERR_LOGIN_FAILED"
    assert pesdivisions._state(svc, 1) and pesmatchday._totals(svc) == {}
    assert db.execute("SELECT COUNT(*) FROM pes_division").fetchone()[0] == 1      # only player 1's, just above


def test_the_web_api_cant_name_a_player(svc):
    announce.tell(svc, 1, "Private", "for player 1")
    announce.set_news(svc, "News", "for everyone")
    assert "announce_info_list" not in _web(svc, "CMD_GET_PRIVATEINFOTITLELIST", pid=1)
    assert "is_announcement" not in _web(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=1)
    assert announce.snapshot(svc)["news"]["seen"] == 0
    cid = pestourney.save(svc, {"name": "Cup"})
    assert _web(svc, "CMD_JOIN_PESLEAGUE_COMPE", compe_id=cid, pid=1, team_id=8) == {"result": "NOERR"}
    assert pestourney.entry(svc, cid, 1) is None
    # a game on its own connection still gets its messages
    assert "announce_info_list" in pescmds._dispatch(svc, _sess(svc, 1), "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 1})


def test_a_tiny_message_cant_unpack_into_gigabytes():
    with pytest.raises(ProtocolError):
        ncl._inflate(zlib.compress(b"\0" * (ncl.MAX_INFLATED + 1)), 10, 1)
    assert ncl._inflate(zlib.compress(msgpack.packb({"a": 1})), 3, 1) == {"a": 1}


def test_a_request_that_never_sends_its_body_is_dropped(svc, monkeypatch):
    monkeypatch.setattr(httpgate, "BODY_WAIT", 0.05)

    async def go():
        srv = await asyncio.start_server(httpgate.make_handler(svc, "127.0.0.1", 0, 0), "127.0.0.1", 0)
        r, w = await asyncio.open_connection(*srv.sockets[0].getsockname()[:2])
        w.write(b"POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\n")
        assert await asyncio.wait_for(r.read(), 2) == b""             # closed, not kept open for ever
        w.close(); srv.close()
    asyncio.run(go())


def _lobby_match(svc):
    a, b = _sess(svc, 1), _sess(svc, 2)
    d = lambda s, m, r=None: pescmds._dispatch(svc, s, m, r or {})
    rid = d(a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
    d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})
    for s in (a, b):
        d(s, "CMD_ENTRY_GAME", {"entry": "TRUE"})
    d(a, "CMD_SET_GAMEMEMBER")
    return a, b, pesrooms._st(svc)["rooms"][rid]


def test_a_blink_during_a_lobby_match_keeps_the_room(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 0.02)

    async def go():
        a, b, room = _lobby_match(svc)
        pesrooms.disconnected(svc, b)
        del svc.online[2]
        assert room["members"] == [1, 2]
        _sess(svc, 2, pes_reconnected=True)              # the game's CMD_RECONNECT: a new connection
        await asyncio.sleep(0.05)
        assert room["members"] == [1, 2]                 # both come back to the room after the match
        pesrooms.disconnected(svc, a)                    # the owner's game is really gone
        del svc.online[1]
        await asyncio.sleep(0.05)
        assert room["members"] == [2] and room["owner"] == 2
    asyncio.run(go())


def test_a_kicked_player_leaves_the_room_at_once(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 60)

    async def go():
        _a, b, room = _lobby_match(svc)
        b.kicked = True
        pesrooms.disconnected(svc, b)
        assert room["members"] == [1]
    asyncio.run(go())


def test_a_delayed_push_doesnt_bring_a_closed_room_back(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, m, r, k, q, body: sent.append(body["list_type"]))
    a, b = _sess(svc, 1), _sess(svc, 2)
    pescmds._dispatch(svc, b, "CMD_REQWATCHL_ROOMLIST", {})
    room = {"id": 99, "members": []}
    pesrooms.roomlist_push(svc, room, "ADD")             # (it closed before its ADD went out)
    pesrooms.roomlist_push(svc, room, "UPDATE")
    assert sent == []
    pesrooms.roomlist_push(svc, room, "DEL")
    assert sent == ["DEL"]


def test_the_router_opens_the_ports_the_server_really_uses(monkeypatch):
    monkeypatch.setattr(netsetup, "PORTS", list(netsetup.PORTS))
    netsetup.use_ports(8090, 10001, 3479, https=0, join=8090, relay=7601)
    assert netsetup.PORTS == [(8090, "TCP"), (10001, "TCP"), (3479, "UDP"), (7601, "UDP")]


@pytest.mark.parametrize("how", ["none found", "no answer"])
def test_ports_arent_called_open_once_the_router_is_gone(monkeypatch, how):
    monkeypatch.setattr(netsetup, "STATE", {**netsetup.STATE, "lan": "192.168.1.5", "wan": None, "_gw": object(),
                                            "mapped": ["8080/tcp"], "upnp_error": None})
    monkeypatch.setattr(netsetup, "lan_ip", lambda: "192.168.1.5")
    if how == "none found":
        monkeypatch.setattr(netsetup, "discover_gateway", lambda: None)
        netsetup._find_router()
    else:
        def silent(gw):
            raise OSError("no route")
        monkeypatch.setattr(netsetup, "_open_ports", silent)
        netsetup._tick(True, {"renew": True, "wan": False, "find": False})
    assert netsetup.STATE["mapped"] == [] and netsetup.STATE["_gw"] is None


class _Tr:
    def close(self):
        self.closed = True


def _alloc(srv, port, conn=None):
    a = turnrelay.Allocation(srv, conn, port, _Tr(), "127.0.0.1", "9:1", b"k")
    srv.allocations[port] = a
    return a


def test_an_expired_allocation_lets_go_of_its_connection_and_port():
    srv = turnrelay.TurnServer("s", "r", (51400, 51401), lambda *_: "127.0.0.1")
    conn = SimpleNamespace(alloc=None)
    old = conn.alloc = _alloc(srv, 51400, conn)
    srv._free(old)                                       # what the sweep does when its time is up
    assert conn.alloc is None                            # so Refresh says 437 and the game allocates again
    new = _alloc(srv, 51400)                             # the port goes to someone else
    srv._free(old)                                       # the old connection ends only now
    assert srv.allocations[51400] is new


def test_an_empty_dtls_message_is_ignored():
    got = []
    turndtls._Assoc.handle(SimpleNamespace(conn=SimpleNamespace(stun=got.append)), turndtls.TYPE_PLAIN)
    assert got == []


# ---- round 3 of the 2026-11-18 review ----------------------------------------------------------------------
def test_the_web_api_does_not_hand_out_a_rooms_addresses(svc):
    a = _sess(svc, 1)
    pescmds._dispatch(svc, a, "CMD_SEND_IPANDPORT_NTL", {"reflexive_addr": "HIS-ADDRESS"})
    rid = pescmds._dispatch(svc, a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
    r = pescmds._dispatch(svc, None, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", {"room_id": str(rid)})
    assert r == {"result": "ERR_LOGIN_FAILED"}
    assert pescmds._dispatch(svc, a, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", {"room_id": rid})["list_num"] == 1


def test_a_new_sign_in_during_a_lobby_match_does_not_leave_a_ghost_in_the_room(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 0.02)

    async def go():
        a, b, room = _lobby_match(svc)
        pesrooms.disconnected(svc, b)
        del svc.online[2]
        _sess(svc, 2)                                    # a full sign-in, not CMD_RECONNECT
        await asyncio.sleep(0.05)
        assert room["members"] == [1]
        pescmds._dispatch(svc, a, "CMD_LEAVE_GAME", {})
        assert room["state"] == "WAITING"                # the friend isn't left waiting for the one who went
    asyncio.run(go())


def test_a_new_sign_in_keeps_the_room_it_made_meanwhile(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 0.02)

    async def go():
        _a, b, room = _lobby_match(svc)
        pesrooms.disconnected(svc, b)
        del svc.online[2]
        b2 = _sess(svc, 2)
        rid = pescmds._dispatch(svc, b2, "CMD_CREATEJOIN_ROOM", {"name": "New"})["room_id"]
        await asyncio.sleep(0.05)
        assert pesrooms.room_of(svc, 2)["id"] == rid and room["members"] == [1]
    asyncio.run(go())


def test_a_recording_survives_a_blink_and_ends_when_the_player_is_gone(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 0.02)
    closed = []
    monkeypatch.setattr(pesreplay, "close", lambda _s, mid, why: closed.append(mid))
    monkeypatch.setattr(pesreplay, "_live", {"m1": {"uploader": 1, "home": 1, "away": 2}})

    async def go():
        pesreplay.disconnected(svc, 1)
        _sess(svc, 1, pes_reconnected=True)
        await asyncio.sleep(0.05)
        assert closed == []
        del svc.online[1]
        pesreplay.disconnected(svc, 1)
        await asyncio.sleep(0.05)
        assert closed == ["m1"]
    asyncio.run(go())


# ---- round 4 of the 2026-11-18 review ----------------------------------------------------------------------
def test_a_second_blink_gets_its_own_wait_for_the_room_and_the_recording(svc, monkeypatch):
    closed = []
    monkeypatch.setattr(pesreplay, "close", lambda _s, mid, why: closed.append(mid))
    monkeypatch.setattr(pesreplay, "_live", {"m1": {"uploader": 2, "home": 1, "away": 2}})
    monkeypatch.setattr(pesreplay, "_away", {})
    monkeypatch.setattr(pesmatch, "GONE_AFTER", 0.4)

    def drop(s):
        pesrooms.disconnected(svc, s)
        pesreplay.disconnected(svc, 2)
        del svc.online[2]

    async def go():
        _a, b, room = _lobby_match(svc)
        drop(b)
        await asyncio.sleep(0.2)
        drop(_sess(svc, 2, pes_reconnected=True))        # back, and gone again halfway through the first wait
        await asyncio.sleep(0.25)                        # the first wait is over, the second isn't
        assert room["members"] == [1, 2] and closed == []
        _sess(svc, 2, pes_reconnected=True)
        await asyncio.sleep(0.25)
        assert room["members"] == [1, 2] and closed == []
    asyncio.run(go())


def test_a_new_live_update_file_is_never_read_on_the_servers_loop(svc, monkeypatch):
    from pesserver import liveupdate
    monkeypatch.setattr(liveupdate, "_CACHE", {})
    monkeypatch.setattr(liveupdate, "_LAST", {})
    monkeypatch.setattr(liveupdate, "_WATCHED", {liveupdate.folder(svc)})
    read = []
    monkeypatch.setattr(liveupdate, "_version", lambda path: read.append(path) or "102")
    os.makedirs(liveupdate.folder(svc))
    path = os.path.join(liveupdate.folder(svc), "new.cpk")
    open(path, "wb").write(b"x" * 10)
    assert liveupdate.current(svc) is None and read == []          # (what a game or the admin page asks)
    assert liveupdate.current(svc, wait=True)[0] == path           # the background thread's turn
    read.clear()
    assert liveupdate.current(svc)[0] == path and read == []
    open(path, "ab").write(b"y")                                   # still being copied in: not read here either
    assert liveupdate.current(svc)[0] == path and read == []


def test_a_replaced_live_update_file_gets_its_new_checksum_at_once(svc, monkeypatch):
    from pesserver import liveupdate
    monkeypatch.setattr(liveupdate, "_CACHE", {})
    monkeypatch.setattr(liveupdate, "_LAST", {})
    monkeypatch.setattr(liveupdate, "_WATCHED", set())
    monkeypatch.setattr(liveupdate, "_WAKE", threading.Event())
    monkeypatch.setattr(liveupdate, "CHECK_EVERY", 60)             # (the thread's own round can't be what finds it)
    monkeypatch.setattr(liveupdate, "_version", lambda path: "102")
    os.makedirs(liveupdate.folder(svc))
    path = os.path.join(liveupdate.folder(svc), "roster.cpk")

    def offered(data):
        want, end = hashlib.sha256(data).hexdigest(), time.monotonic() + 5
        while time.monotonic() < end:
            cur = liveupdate.current(svc)
            if cur and cur[1]["hash"] == want:
                return True
            time.sleep(0.01)
        return False
    open(path, "wb").write(b"old")
    liveupdate.start(svc)
    assert offered(b"old")
    open(path, "wb").write(b"the new roster")
    assert offered(b"the new roster")


def test_backups_work_in_a_folder_with_a_hash_in_its_name(tmp_path):
    from pesserver import pesbackup
    d = tmp_path / "server #1 100%"
    d.mkdir()
    s = Service(Accounts(str(d / "a.db")))
    s.data_dir = str(d)
    pescmds._db(s)
    assert pesbackup.restore(s, pesbackup.make(s)["name"])["players"] == 0


def test_results_search_ignores_case_in_every_alphabet(svc):
    from pesserver import events
    events._st(svc)
    db = svc.accounts.db
    with svc.accounts.lock, db:
        db.execute("INSERT INTO pes_matches(room, started, ended, home_name, away_name) VALUES(1, 1, 2, 'Émile', 'ÖZIL')")
    assert events.results_page(svc, "émile")[1] == 1 and events.results_page(svc, "ÉMILE")[1] == 1
    assert events.results_page(svc, "özil")[1] == 1 and events.results_page(svc, "100%")[1] == 0


def test_the_coop_block_list_needs_a_logged_in_player(svc):
    r = pescmds._dispatch(svc, SimpleNamespace(), "CMD_SET_COOP_REJECT", {"set_reject_pid_list": [1, 2]})
    assert r["reject_list_num"] == 0
    pescoop.rejects(svc, 1)                              # (makes the table)
    assert svc.accounts.db.execute("SELECT COUNT(*) FROM pes_coop_reject").fetchone()[0] == 0


def test_a_relative_data_folder_becomes_absolute(monkeypatch, tmp_path):
    from pesserver import __main__ as m, hostctl
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["pesserver", "open", "--data", "rel"])
    seen = []
    monkeypatch.setattr(hostctl, "cmd_open", seen.append)
    m.main()
    assert seen[0].data == str(tmp_path / "rel")
