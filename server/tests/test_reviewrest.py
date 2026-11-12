"""The rest of the 2026-11-12 review, game side: a lobby room isn't left waiting for someone who is gone, entry
numbers stay unique, replay uploads don't fill the log, garbage on the wire is a short line (not a traceback),
Divisions shows the real rating, a deleted competition leaves no "seen" marks, a recording cut by a restart is
closed, only a logged-in connection can search or use rooms, a reconnect must come from the same install, old match
rooms go, and table setup runs once."""
import asyncio, os, struct, time
from types import SimpleNamespace
import msgpack
import pytest
from pesserver import accounts, ncl, pescmds, pescoop, pesdivisions, pesmatch, pesreplay, pesrooms, pestourney  # noqa: F401
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
    assert sorted(nums) == [2, 3]


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
    assert rc() == "ERR_LOGIN_FAILED"                     # no id, and the player wasn't here a moment ago
    assert rc(mod_id=mine) == "NOERR"
    assert rc() == "NOERR"                                # no id, same address as the connection it replaces
    assert pescmds._dispatch(svc, SimpleNamespace(peer_ip="9.9.9.9"), "CMD_RECONNECT", {"pid": 1})["result"] == "ERR_LOGIN_FAILED"
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
