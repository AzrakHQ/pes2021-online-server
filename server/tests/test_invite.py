"""Invites (pesinvite.py): one mod per friend with a handle; the server knows them by its code."""
import asyncio, json, random
from types import SimpleNamespace

import pytest

from pesserver import events, gate, httpgate, identity, pescmds, pesinvite
from pesserver.accounts import Accounts
from pesserver.service import Service

STEAM_USER = "410000004100000"


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def _sess(mod_id="0123456789abcdef0123456789abcdef", invite=None, ip="192.168.1.7"):
    return SimpleNamespace(mod_id=mod_id, mod_invite=invite, mod_features=frozenset(), peer_ip=ip)


def _login(svc, s):
    return pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": STEAM_USER, "hash": "x", "platform": "PC"})


def _api(svc, route, body=None, method="POST"):
    status, res = pesinvite.admin_api(svc, route, method, json.dumps(body or {}).encode(), "http://pes.example.org")
    return status, res


def test_id_line_carries_the_invite_code():
    async def read(line):
        r = asyncio.StreamReader(); r.feed_data(line); r.feed_eof()
        s = SimpleNamespace(peer_ip="1.2.3.4")
        await gate.take_player_id(r, s)
        return s
    code = "ab" * 16
    s = asyncio.run(read(b"PESID2 " + b"0" * 32 + b" math1 coop1 inv=" + code.encode() + b"\n"))
    assert s.mod_invite == code and s.mod_features == {"math1", "coop1"}
    s = asyncio.run(read(b"PESID2 " + b"0" * 32 + b" math1\n"))
    assert s.mod_invite is None and s.mod_features == {"math1"}
    s = asyncio.run(read(b"PESID1 " + b"1" * 32 + b"\n"))
    assert s.mod_invite is None and s.mod_features == frozenset()


def test_an_invited_friend_is_the_same_player_on_any_pc(svc):
    inv = pesinvite.create(svc, "Nutmeg Ninja")
    a = _sess(invite=inv["code"])
    assert _login(svc, a) == {"result": "NOERR"}
    pid = a.pes_pid
    pescmds._dispatch(svc, a, "CMD_CREATE_PLAYER", {"name": "Mo"})
    b = _sess(mod_id="f" * 32, invite=inv["code"], ip="203.0.113.9")        # another PC, same mod file
    assert _login(svc, b) == {"result": "NOERR"} and b.pes_pid == pid
    assert pescmds._dispatch(svc, b, "CMD_GET_PLAYER_DATA", {})["has_data"] == "YES"
    assert pesinvite.by_code(svc, inv["code"])["pid"] == pid and pesinvite.handle_of(svc, pid) == "Nutmeg Ninja"


def test_revoked_and_unknown_invites_cannot_sign_in(svc):
    inv = pesinvite.create(svc)
    pesinvite.revoke(svc, inv["id"])
    assert _login(svc, _sess(invite=inv["code"])) == {"result": "ERR_INVALID_ACCOUNT"}     # "account suspended"
    pesinvite.revoke(svc, inv["id"], back=True)
    assert _login(svc, _sess(invite=inv["code"])) == {"result": "NOERR"}
    assert _login(svc, _sess(invite="0" * 32)) == {"result": "ERR_LOGIN_FAILED"}


def test_invites_only_turns_newcomers_away_but_keeps_known_players(svc):
    old = _sess(mod_id="a" * 32)
    assert _login(svc, old) == {"result": "NOERR"}                   # known before invites-only
    pesinvite.set_invites_only(svc, True)
    assert _login(svc, _sess(mod_id="a" * 32)) == {"result": "NOERR"}
    assert _login(svc, _sess(mod_id="b" * 32, ip="198.51.100.4")) == {"result": "ERR_LOGIN_FAILED"}
    assert any("without an invite" in f["text"] and "198.51.100.4" in f["text"] for f in events._st(svc)["feed"])
    inv = pesinvite.create(svc)
    assert _login(svc, _sess(mod_id="b" * 32, invite=inv["code"])) == {"result": "NOERR"}


def test_an_invite_for_an_existing_player_moves_their_account(svc):
    old = _sess(mod_id="c" * 32)
    _login(svc, old)
    pescmds._dispatch(svc, old, "CMD_CREATE_PLAYER", {"name": "Old Timer"})
    inv = pesinvite.create(svc, pid=old.pes_pid)
    with pytest.raises(ValueError):
        pesinvite.create(svc, pid=old.pes_pid)                       # one live invite per player
    new = _sess(mod_id="d" * 32, invite=inv["code"])
    assert _login(svc, new) == {"result": "NOERR"} and new.pes_pid == old.pes_pid
    assert pescmds._player(svc, "i:%d" % inv["id"])["name"] == "Old Timer"


def test_two_players_cannot_take_the_same_name(svc):
    a, b = _sess(mod_id="1" * 32), _sess(mod_id="2" * 32)
    _login(svc, a); _login(svc, b)
    assert pescmds._dispatch(svc, a, "CMD_CREATE_PLAYER", {"name": "Mohamed Rashad"}) == {"result": "NOERR"}
    assert pescmds._dispatch(svc, b, "CMD_CREATE_PLAYER", {"name": "mohamed rashad"}) == {"result": "ERR_ALREADYEXISTS"}
    assert pescmds._dispatch(svc, a, "CMD_CREATE_PLAYER", {"name": "Mohamed Rashad"}) == {"result": "NOERR"}   # own name again


def test_admin_api(svc):
    st, inv = _api(svc, "/admin/api/invites/new", {})
    assert st.startswith("200") and " " in inv["handle"] and inv["link"].startswith("http://pes.example.org/mod/dinput8.dll?invite=")
    assert _api(svc, "/admin/api/invites/new", {"handle": inv["handle"].upper()})[0].startswith("400")   # handles are unique
    assert _api(svc, "/admin/api/invites/new", {"handle": "x"})[0].startswith("400")
    st, r = _api(svc, "/admin/api/invites/rename", {"id": inv["id"], "handle": "Panenka Panda"})
    assert st.startswith("200") and r["handle"] == "Panenka Panda"
    assert _api(svc, "/admin/api/invites/revoke", {"id": inv["id"]})[1]["revoked"]
    assert _api(svc, "/admin/api/invites/revoke", {"id": inv["id"], "back": True})[1]["revoked"] is None
    st, snap = _api(svc, "/admin/api/invites", method="GET")
    assert snap["invites_only"] is False and [i["handle"] for i in snap["invites"]] == ["Panenka Panda"]
    assert _api(svc, "/admin/api/invites/settings", {"invites_only": True})[1]["invites_only"] is True
    assert _api(svc, "/admin/api/invites/delete", {"id": inv["id"]})[0].startswith("200")
    assert _api(svc, "/admin/api/invites/delete", {"id": inv["id"]})[0].startswith("404")
    assert _api(svc, "/admin/api/invites/new", {"pid": 999})[0].startswith("404")


def test_funny_handles_are_unique(svc):
    rng = random.Random(1)
    seen = {pesinvite.create(svc, pesinvite.funny_handle(svc, rng))["handle"].lower() for _ in range(60)}
    assert len(seen) == 60


def test_invite_download_has_the_code_written_in(svc, tmp_path, monkeypatch):
    dll = tmp_path / "dinput8.dll"
    dll.write_bytes(b"head" + identity.EMBED_MARKER + b"\0" * (identity.EMBED_SIZE - len(identity.EMBED_MARKER)) + b"tail")
    monkeypatch.setattr(httpgate, "MOD_DLL", str(dll))
    monkeypatch.setattr(identity, "DATA_DIR", str(tmp_path), raising=False)
    inv = pesinvite.create(svc)
    st, body, _, _ = httpgate._mod(svc, "/mod/dinput8.dll", {"host": "192.168.1.50"}, None,
                                   f"/mod/dinput8.dll?invite={inv['code']}")
    assert st.startswith("200") and f"invite={inv['code']}\n".encode() in body
    st, body, _, _ = httpgate._mod(svc, "/mod/dinput8.dll", {"host": "192.168.1.50"}, None, "/mod/dinput8.dll")
    assert st.startswith("200") and b"invite=" not in body
    pesinvite.revoke(svc, inv["id"])
    st, body, _, _ = httpgate._mod(svc, "/mod/dinput8.dll", {"host": "192.168.1.50"}, None,
                                   f"/mod/dinput8.dll?invite={inv['code']}")
    assert st.startswith("404") and b"new one" in body


def test_deleting_a_player_keeps_their_invite(svc):
    inv = pesinvite.create(svc)
    s = _sess(invite=inv["code"])
    _login(svc, s)
    pescmds.delete_player(svc, s.pes_pid)
    pesinvite.forget(svc, s.pes_pid)
    assert pesinvite.by_code(svc, inv["code"])["pid"] is None
    s2 = _sess(invite=inv["code"])
    assert _login(svc, s2) == {"result": "NOERR"}
    assert pescmds._dispatch(svc, s2, "CMD_GET_PLAYER_DATA", {})["has_data"] == "NO"           # asked for a name again


def test_admin_routes_through_the_web_handler(svc):
    svc.admin_password = "pw"
    tok = httpgate._sessions(svc).new(True)
    hdr = {"host": "127.0.0.1:8080", "authorization": "Bearer " + tok}
    call = lambda m, r, b=b"": asyncio.run(httpgate._admin(svc, m, r, hdr, b, "127.0.0.1"))
    st, out, _ = call("POST", "/admin/api/invites/new", b'{"handle": "Golden Goblin"}')
    assert st.startswith("200") and json.loads(out)["handle"] == "Golden Goblin"
    st, out, _ = call("GET", "/admin/api/invites")
    snap = json.loads(out)
    assert st.startswith("200") and snap["invites"][0]["code"] and snap["suggest"]
    st, out, _ = call("POST", "/admin/api/invites/suggest", b"{}")
    assert st.startswith("200") and json.loads(out)["handle"]
    assert any("made an invite for Golden Goblin" in f["text"] for f in events._st(svc)["feed"])
