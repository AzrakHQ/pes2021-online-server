"""Player names (pesnames.py): kept as typed plus a lowercase _ handle, unique apart from case/spaces/_-.; claims, Move, Block."""
import asyncio, json
from types import SimpleNamespace

import pytest

from pesserver import events, httpgate, pescmds, pesnames
from pesserver.accounts import Accounts
from pesserver.service import Service

STEAM_USER = "410000004100000"


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def _copy(svc, n, ip="192.168.1.7"):
    s = SimpleNamespace(mod_id=f"{n:032x}", mod_invite=None, mod_features=frozenset(), peer_ip=ip)
    assert pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": STEAM_USER, "hash": "x", "platform": "PC"}) == {"result": "NOERR"}
    return s


def _name(svc, s, name):
    return pescmds._dispatch(svc, s, "CMD_CREATE_PLAYER", {"name": name})["result"]


def test_clean_and_key():
    assert pesnames.clean("Mohamed Rashad") == "mohamed_rashad"
    assert pesnames.clean("  Mo--Rashad.10 ") == "mo_rashad_10"
    assert pesnames.clean("محمد رشاد") == "محمد_رشاد"                 # letters of any language stay
    assert pesnames.clean("Sami ⚽!") == "sami"
    assert pesnames.key("Mo Rashad") == pesnames.key("mo_rashad") == pesnames.key("MoRashad") == "morashad"


def test_shown():
    assert pesnames.shown("  Mohamed   Rashad ") == "Mohamed Rashad"
    assert pesnames.shown("Mo\u200b\x07Ra\tshad") == "MoRa shad"      # control / invisible characters go
    assert pesnames.shown("محمد رشاد") == "محمد رشاد"
    assert len(pesnames.shown("x" * 50)) == pesnames.SHOWN_MAX


def test_names_are_kept_as_typed_with_a_unique_handle(svc):
    a, b = _copy(svc, 1), _copy(svc, 2)
    assert _name(svc, a, "Mohamed Rashad") == "NOERR"
    assert pescmds._player(svc, a.pes_username)["name"] == "Mohamed Rashad"          # the game shows it as typed
    assert pesnames.handle_of(svc, a.pes_pid) == "mohamed_rashad"
    p = next(p for p in events.snapshot(svc)["players"] if p["pid"] == a.pes_pid)
    assert (p["name"], p["handle"]) == ("Mohamed Rashad", "mohamed_rashad")
    assert events.player_detail(svc, a.pes_pid)["handle"] == "mohamed_rashad"
    for similar in ("mohamed_rashad", "MOHAMED RASHAD", "Mohamed-Rashad", "MohamedRashad"):
        assert _name(svc, b, similar) == "ERR_ALREADYEXISTS"       # "There is already a User with that name."
    assert _name(svc, b, "!!") == "ERR_INVALIDLETTER"
    assert _name(svc, b, "Sami") == "NOERR"
    assert _name(svc, a, "Mohamed Rashad") == "NOERR"                 # their own name again


def test_a_taken_name_from_a_new_copy_is_a_claim_the_host_can_move(svc):
    old = _copy(svc, 1)
    _name(svc, old, "Mohamed Rashad")
    pid = old.pes_pid
    new = _copy(svc, 9, ip="203.0.113.5")                              # the same friend on a new PC
    assert _name(svc, new, "mohamed rashad") == "ERR_ALREADYEXISTS"
    claims = pesnames.claims_for(svc, pid)
    assert len(claims) == 1 and claims[0]["copy"] == new.pes_username and claims[0]["ip"] == "203.0.113.5"
    assert any("asked for the name mohamed rashad, which Mohamed Rashad has" in f["text"] for f in events._st(svc)["feed"])
    assert [p["name"] for p in events.snapshot(svc)["players"]] == ["Mohamed Rashad"]   # the nameless copy isn't listed
    assert pesnames.move(svc, pid, new.pes_username) == {"pid": pid, "name": "Mohamed Rashad"}
    again = _copy(svc, 9)
    assert again.pes_pid == pid and pescmds._dispatch(svc, again, "CMD_GET_PLAYER_DATA", {})["has_data"] == "YES"
    assert pesnames.claims_for(svc, pid) == []
    with pytest.raises(LookupError):
        pesnames.move(svc, pid, new.pes_username)                     # done once


def test_move_refuses_a_copy_that_already_plays(svc):
    old, other = _copy(svc, 1), _copy(svc, 2)
    _name(svc, old, "Omar")
    _name(svc, other, "omar")                                         # claim, then they pick another name
    _name(svc, other, "Omar2")
    with pytest.raises(LookupError):
        pesnames.move(svc, old.pes_pid, other.pes_username)           # the claim is gone once they have a name
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        db.execute("INSERT INTO pes_name_claims (username, wanted, pid, t) VALUES (?,?,?,0)", (other.pes_username, "omar", old.pes_pid))
    with pytest.raises(ValueError):
        pesnames.move(svc, old.pes_pid, other.pes_username)


def test_block(svc):
    a = _copy(svc, 1)
    _name(svc, a, "Sami")
    pesnames.set_blocked(svc, a.pes_pid, True)
    s = SimpleNamespace(mod_id=f"{1:032x}", mod_invite=None, mod_features=frozenset(), peer_ip="x")
    assert pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": STEAM_USER, "hash": "x"}) == {"result": "ERR_INVALID_ACCOUNT"}
    assert [p["blocked"] for p in events.snapshot(svc)["players"]] == [True]
    pesnames.set_blocked(svc, a.pes_pid, False)
    assert pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": STEAM_USER, "hash": "x"}) == {"result": "NOERR"}


def test_an_invite_account_moves_to_the_install_id(svc):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        db.execute("CREATE TABLE pes_invites (id INTEGER PRIMARY KEY, code TEXT, handle TEXT, pid INTEGER, created REAL, revoked REAL, last_seen REAL)")
        db.execute("INSERT INTO pes_invites (id, code, handle, created) VALUES (3, ?, 'nutmeg_ninja', 0)", ("ab" * 16,))
        db.execute("INSERT INTO pes_players (username, name, created) VALUES ('i:3', 'sami10', 0)")
    s = SimpleNamespace(mod_id="c" * 32, mod_invite="ab" * 16, mod_features=frozenset(), peer_ip="x")
    pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": STEAM_USER, "hash": "x"})
    assert pescmds._dispatch(svc, s, "CMD_GET_PLAYER_DATA", {})["has_data"] == "YES"
    assert pescmds._player(svc, "m:" + "c" * 32)["name"] == "sami10"


def test_player_routes(svc):
    a = _copy(svc, 1); _name(svc, a, "Mohamed Rashad")
    b = _copy(svc, 2); _name(svc, b, "mohamed-rashad")
    svc.admin_password = "pw"
    tok = httpgate._sessions(svc).new(True)
    hdr = {"host": "127.0.0.1:8080", "authorization": "Bearer " + tok}
    call = lambda m, r, body=b"": asyncio.run(httpgate._admin(svc, m, r, hdr, body, "127.0.0.1"))
    st, out, _ = call("POST", "/admin/api/player", json.dumps({"pid": a.pes_pid}).encode())
    p = json.loads(out)
    assert st.startswith("200") and p["blocked"] is False and p["claims"][0]["copy"] == b.pes_username
    st, out, _ = call("POST", "/admin/api/players/block", json.dumps({"pid": a.pes_pid, "on": True}).encode())
    assert st.startswith("200") and json.loads(out)["blocked"] is True
    st, out, _ = call("POST", "/admin/api/players/move", json.dumps({"pid": a.pes_pid, "copy": b.pes_username}).encode())
    assert st.startswith("200") and json.loads(out)["name"] == "Mohamed Rashad"
    st, out, _ = call("POST", "/admin/api/players/move", json.dumps({"pid": a.pes_pid, "copy": b.pes_username}).encode())
    assert st.startswith("404")


def test_players_named_before_handles_get_one(tmp_path):
    """An older database: the name column already holds the cleaned form and there is no handle column."""
    import sqlite3
    path = str(tmp_path / "a.db")
    s = Service(Accounts(path))
    s.data_dir = str(tmp_path)
    db = s.accounts.db
    with db:
        db.execute("CREATE TABLE pes_players(pid INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT,"
                   " steamid TEXT, psid INTEGER, created REAL NOT NULL, last_login REAL)")
        db.execute("INSERT INTO pes_players(username, name, created) VALUES('old', 'omar_10', 0)")
    pescmds._db(s)
    assert [(p["name"], p["handle"]) for p in events.snapshot(s)["players"]] == [("omar_10", "omar_10")]
    b = _copy(s, 2)
    assert _name(s, b, "Omar 10") == "ERR_ALREADYEXISTS"
    assert sqlite3.connect(path).execute("SELECT handle FROM pes_players WHERE username='old'").fetchone()[0] == "omar_10"
