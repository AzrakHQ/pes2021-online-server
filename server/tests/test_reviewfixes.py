"""Fixes from the 2026-11-12 review of the admin page and the server: the relay only sends to players, relay
credentials only go to the player's own PC, no password-less sign-in through a proxy, a searching player whose game
vanished leaves the queue, a reconnect isn't a forfeit and isn't a way round a kick or block."""
import asyncio, json
from types import SimpleNamespace
import pytest
from pesserver import events, httpgate, netsetup, pescmds, pesmanner, pesmatch, pesnames, pesresults, turn, turnrelay
from pesserver.accounts import Accounts
from pesserver.service import Service
from test_matchfixes import _searching


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("PESSERVER_PUBLIC_ADDR", raising=False)
    monkeypatch.setitem(netsetup.STATE, "wan", "81.2.69.160")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.100")
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


# ---- the relay ---------------------------------------------------------------------------------------
def test_relay_sends_only_to_players_never_to_this_pc_or_the_home_network():
    players = {"41.236.194.222", "192.168.1.12"}
    srv = turnrelay.TurnServer("s", "r", (49160, 49161), lambda a, b: "81.2.69.160",
                               own_ips={"192.168.1.100", "81.2.69.160", "127.0.0.1"}, peer_ok=players.__contains__)
    assert srv.may_send("41.236.194.222") and srv.may_send("192.168.1.12")          # friends, far and at home
    for ip in ("192.168.1.1", "127.0.0.1", "192.168.1.100", "81.2.69.160", "8.8.8.8", "224.0.0.1", "169.254.1.1", "x"):
        assert not srv.may_send(ip), ip                                                # router, this PC, strangers
    assert turnrelay.TurnServer("s", "r", (49160, 49161), lambda a, b: "x").may_send("192.168.1.1")   # (tests: no rule)


def test_relay_credentials_only_for_an_online_player_asking_from_their_own_pc(svc, monkeypatch):
    monkeypatch.setitem(turn.STATE, "server", object())
    monkeypatch.setitem(turn.STATE, "secret", "k")
    monkeypatch.setitem(turn.STATE, "cert_der", b"der")
    svc.online[2] = SimpleNamespace(pes_pid=2, peer_ip="41.236.194.222", local_ip="192.168.1.100")
    ask = lambda pid, ip: pescmds._dispatch(svc, None, "CMD_GET_TURN_SERVER_LIST",
                                            {"pid": str(pid), "_http": SimpleNamespace(peer_ip=ip, local_ip="192.168.1.100")})
    assert "pw" in ask(2, "41.236.194.222") and ask(2, "41.236.194.222")["turn_server_list"]
    assert ask(2, "203.0.113.9") == {"result": "NOERR"}        # a stranger naming an online player
    assert ask(7, "203.0.113.9") == {"result": "NOERR"}        # nobody online with that number


# ---- the admin sign-in -------------------------------------------------------------------------------
def test_no_passwordless_sign_in_through_a_proxy_on_the_server_pc(svc):
    svc.admin_password = "correct horse battery"
    httpgate._FAILED.clear()
    login = lambda hdr: asyncio.run(httpgate._admin(svc, "POST", "/admin/api/login", {"host": "127.0.0.1:8080", "x-pes-local": "1", **hdr},
                                                    b'{"local": true}', "127.0.0.1", False))
    assert login({})[0].startswith("200")
    for hdr in ({"x-forwarded-for": "203.0.113.9"}, {"forwarded": "for=203.0.113.9"}, {"tailscale-user-login": "a@b"},
                {"via": "1.1 proxy"}):
        assert login({**hdr, "host": "gamepc.tail1234.ts.net"})[0].startswith("401"), hdr
    assert httpgate.same_pc("127.0.0.1", {}) and not httpgate.same_pc("127.0.0.1", {"x-real-ip": "1.2.3.4"})


def test_a_broken_request_gets_an_answer_not_a_dropped_connection(svc):
    tok = httpgate._sessions(svc).new(True)
    hdr = {"authorization": "Bearer " + tok, "host": "127.0.0.1:8080"}
    for route in ("/admin/api/games/install", "/admin/api/games/remove"):
        for body in (b"{bad", b"[1]", b'"x"'):
            st, out, _ = asyncio.run(httpgate._admin(svc, "POST", route, hdr, body, "127.0.0.1", False))
            assert st.startswith("400") and json.loads(out)["error"], (route, body)


# ---- matchmaking -------------------------------------------------------------------------------------
def test_a_searching_player_whose_game_vanished_leaves_the_queue(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    a = _searching(svc, 1)
    pesmatch.disconnected(svc, a)                # (no event loop in a test: the wait is skipped)
    svc.online.pop(1)
    assert pesmatch._state(svc)["queue"] == [] and 1 not in pesmatch._state(svc)["watch"]
    b = _searching(svc, 2)                       # the next friend isn't paired with a ghost
    assert [p for p, _ in pesmatch._state(svc)["queue"]] == [2] and not pesmatch._state(svc)["rooms"]


def test_a_player_whose_line_dropped_is_not_paired_until_back(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    a = _searching(svc, 1)
    a.pes_gone = True                            # the connection closed; the game has 20 s to reconnect
    _searching(svc, 2)
    assert not pesmatch._state(svc)["rooms"]
    new = SimpleNamespace(peer_ip="192.168.1.5")
    assert pescmds._dispatch(svc, new, "CMD_RECONNECT", {"pid": 1})["result"] == "NOERR"
    pesmatch._try_pair(svc)
    assert len(pesmatch._state(svc)["rooms"]) == 1 and pesmatch._state(svc)["queue"] == []


def test_reconnect_is_no_way_round_a_block_or_a_kick(svc):
    db = svc.accounts.db
    for pid in (1, 2):
        db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, f"u{pid}", f"P{pid}"))
    pesnames.set_blocked(svc, 1, True)
    assert pescmds._dispatch(svc, SimpleNamespace(), "CMD_RECONNECT", {"pid": 1})["result"] == "ERR_LOGIN_FAILED"
    pescmds.show_out(svc, 2)                     # what Kick does
    assert pescmds._dispatch(svc, SimpleNamespace(), "CMD_RECONNECT", {"pid": 2})["result"] == "ERR_LOGIN_FAILED"
    svc.pes_shown_out[2] -= pescmds.SHOWN_OUT + 1
    assert pescmds._dispatch(svc, SimpleNamespace(), "CMD_RECONNECT", {"pid": 2})["result"] == "NOERR"


def test_a_line_that_blinks_mid_match_is_not_a_forfeit(svc):
    db = svc.accounts.db
    for pid in (1, 2):
        db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, f"u{pid}", f"P{pid}"))
    a, b = SimpleNamespace(pes_pid=1, connected_at=1.0), SimpleNamespace(pes_pid=2, connected_at=1.0)
    svc.online.update({1: a, 2: b})
    pesmatch._state(svc)["rooms"][5] = {"owner": 1, "members": [1, 2], "t": 0}
    for s in (a, b):
        pescmds.dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST", "gtimer": 1})
    lv = events._st(svc)["live"][5]
    events.disconnected(svc, a); svc.online.pop(1)
    assert pesmanner.rating(svc, 1) != pesmanner.rating(svc, 2) or lv["manner_marked"] == {1}
    new = SimpleNamespace()
    assert pescmds.dispatch(svc, new, "CMD_RECONNECT", {"pid": 1})["result"] == "NOERR"
    assert new.connected_at and 1 in events._st(svc)["present"]      # a later disconnect is seen again
    lv["forfeit_decide"]()                                           # 15 s later
    assert "forfeit" not in pesmatch._state(svc)["rooms"][5] and lv["manner_marked"] == set()
    assert db.execute("SELECT COUNT(*) FROM pes_manner WHERE pid=1 AND left_early=1").fetchone()[0] == 0


def test_a_player_who_really_left_still_forfeits(svc):
    db = svc.accounts.db
    for pid in (1, 2):
        db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, f"u{pid}", f"P{pid}"))
    a, b = SimpleNamespace(pes_pid=1, connected_at=1.0), SimpleNamespace(pes_pid=2, connected_at=1.0)
    svc.online.update({1: a, 2: b})
    pesmatch._state(svc)["rooms"][5] = {"owner": 1, "members": [1, 2], "t": 0, "mode": "OC_RANK", "teams": {1: 174, 2: 267}}
    for s in (a, b):
        pescmds.dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST", "gtimer": 1})
    lv = events._st(svc)["live"][5]
    events.disconnected(svc, a); svc.online.pop(1)
    lv["forfeit_decide"]()
    assert pesmatch._state(svc)["rooms"][5].get("forfeit") == 1


def test_divisions_history_is_not_hidden_by_other_modes(svc):
    db = svc.accounts.db
    events._st(svc)
    for pid in (1, 2):
        db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, f"u{pid}", f"P{pid}"))
    row = "INSERT INTO pes_matches(started, ended, home_pid, away_pid, home_name, away_name, home_score, away_score, reason, mode) VALUES(?,?,1,2,'P1','P2',1,0,'',?)"
    for i in range(3):
        db.execute(row, (i, i + 1, "Online Divisions"))
    for i in range(pesresults.HISTORY + 5):
        db.execute(row, (100 + i, 101 + i, "Quick Match"))
    assert len(pesresults.history(svc, 1, "RANK")) == 3


# ---- the background service keeps the settings it was started with -----------------------------------
def test_settings_survive_the_move_to_the_background(tmp_path, monkeypatch):
    from pesserver import hostctl
    data = str(tmp_path / "data")
    monkeypatch.setenv("UPNP", "0"); monkeypatch.setenv("TURN", "0"); monkeypatch.setenv("ADMIN_PASSWORD", "a long enough one")
    monkeypatch.setenv("UPDATE_CHECK", "0; rm -rf /")                    # not a plain value: never written
    assert hostctl.keep_settings(data) == {"UPNP": "0", "TURN": "0"}
    assert open(hostctl.env_path(data)).read() == "TURN=0\nUPNP=0\n"
    assert open(data + "/admin_password").read().strip() == "a long enough one"
    assert f"EnvironmentFile=-{data}/server.env" in hostctl.unit_text("/x/pes2021-server", data)
    monkeypatch.delenv("UPNP"); monkeypatch.setenv("TURN", "1")          # run again without UPNP: it stays
    assert hostctl.keep_settings(data) == {"UPNP": "0", "TURN": "1"}
    monkeypatch.setenv("ADMIN_PASSWORD", "short")                        # refused, like at start: the file stays
    hostctl.keep_settings(data)
    assert open(data + "/admin_password").read().strip() == "a long enough one"


# ---- Live Update: sent in pieces ---------------------------------------------------------------------
def test_live_update_download_is_streamed_whole(tmp_path, monkeypatch):
    import os
    from pesserver import liveupdate
    from test_http_speed import _req, _serve
    svc = Service(Accounts(str(tmp_path / "a.db"))); svc.data_dir = str(tmp_path); pescmds._db(svc)
    blob = os.urandom(700_000)                                  # several pieces
    path = str(tmp_path / "Roster.cpk")
    open(path, "wb").write(blob)
    monkeypatch.setattr(liveupdate, "file_for", lambda s, name: path if name == "roster.cpk" else None)

    async def test(port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        code, hdr, out = await _req(r, w, "GET", "/liveupdate/Roster.cpk")
        assert code == 200 and int(hdr["content-length"]) == len(blob) and out == blob
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"HEAD /liveupdate/Roster.cpk HTTP/1.1\r\nHost: x\r\n\r\n"); await w.drain()
        head = await r.read()                                    # HEAD: the size, no body
        assert b"Content-Length: 700000" in head and head.endswith(b"\r\n\r\n")
        r, w = await asyncio.open_connection("127.0.0.1", port)
        assert (await _req(r, w, "GET", "/liveupdate/other.cpk"))[0] == 404
    _serve(svc, test)
