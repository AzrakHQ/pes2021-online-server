"""Fixes found by mapping the game's online code, checked against recorded traffic (2026-11-05)."""
import base64, ipaddress
from types import SimpleNamespace
import msgpack
import pytest
from pesserver import announce, events, netsetup, pescmds, pesmatch
from pesserver.accounts import Accounts
from pesserver.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("PESSERVER_PUBLIC_ADDR", raising=False)
    monkeypatch.setitem(netsetup.STATE, "wan", "81.2.69.160")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.100")
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def addr(ip, port, as_str=True):
    """The game's address blob, exactly as recorded: {address_family 1, sa_data base64(port, 0, ip, 0...)}."""
    sa = port.to_bytes(2, "big") + b"\0\0" + ipaddress.IPv4Address(ip).packed + b"\0" * 6
    raw = msgpack.packb({"address_family": 1, "sa_data": base64.b64encode(sa).decode()})
    return raw.decode("utf-8", "surrogateescape") if as_str else raw


def ip_of(blob):
    raw = blob.encode("utf-8", "surrogateescape") if isinstance(blob, str) else blob
    sa = base64.b64decode(msgpack.unpackb(raw)["sa_data"])
    return str(ipaddress.IPv4Address(sa[4:8])), int.from_bytes(sa[:2], "big")


def test_recorded_blob_decodes():
    rec = "\udc82\udcaeaddress_family\x01\udca7sa_data\udcb4YfMAAMCoAWQAAAAAAAA="   # host's game, as logged
    assert ip_of(rec) == ("192.168.1.100", 25075)


def test_host_lan_address_is_rewritten_only_for_a_remote_friend(svc):
    """The remote friend gets the host's public address as host_addr; reflexive_addr is never rewritten,
    because the game compares reflexive labels to pick which side opens the link (gncx_labels.md)."""
    host = SimpleNamespace(pes_pid=1, peer_ip="192.168.1.100", local_ip="192.168.1.100")
    friend = SimpleNamespace(pes_pid=2, peer_ip="41.236.194.222", local_ip="192.168.1.100")
    neighbour = SimpleNamespace(pes_pid=3, peer_ip="192.168.1.12", local_ip="192.168.1.100")
    st = pesmatch._state(svc)
    st["ntl"][1] = {"host_addr": addr("192.168.1.100", 25075), "reflexive_addr": addr("192.168.1.100", 25075)}
    st["ntl"][2] = {"host_addr": addr("10.0.0.5", 2945), "reflexive_addr": addr("41.236.194.222", 2945)}
    for viewer, want_host in ((friend, "81.2.69.160"), (neighbour, "192.168.1.100"), (host, "192.168.1.100")):
        m = msgpack.unpackb(pesmatch._member(svc, 1, True, 0, viewer), raw=False, unicode_errors="surrogateescape")
        assert ip_of(m["reflexive_addr"]) == ("192.168.1.100", 25075)   # as reported: both games agree on the order
        assert ip_of(m["host_addr"]) == (want_host, 25075)               # what a remote friend can reach
    # the friend's own addresses are never touched
    m = msgpack.unpackb(pesmatch._member(svc, 2, False, 1, host), raw=False, unicode_errors="surrogateescape")
    assert ip_of(m["reflexive_addr"]) == ("41.236.194.222", 2945) and ip_of(m["host_addr"]) == ("10.0.0.5", 2945)
    r = pescmds._dispatch(svc, friend, "CMD_GET_IPANDPORT_NTL", {"pid": 1})
    assert ip_of(r["reflexive_addr"])[0] == "192.168.1.100" and ip_of(r["host_addr"])[0] == "81.2.69.160"
    # loopback (the game on the server PC itself) and bytes blobs too; junk passes through
    assert ip_of(pesmatch._visible_addr(svc, addr("127.0.0.1", 99, as_str=False), friend)) == ("81.2.69.160", 99)
    assert pesmatch._visible_addr(svc, "junk", friend) == "junk"


def test_tailscale_friend_gets_the_servers_tailscale_address(svc, monkeypatch):
    monkeypatch.setenv("PESSERVER_PUBLIC_ADDR", "100.101.102.103")
    friend = SimpleNamespace(pes_pid=2, peer_ip="100.90.1.2", local_ip="100.101.102.103")
    assert ip_of(pesmatch._visible_addr(svc, addr("192.168.1.100", 5000), friend))[0] == "100.101.102.103"


def test_peer_notices_once_each_never_to_sender(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, *a: sent.append((s.pes_pid, msgid, a[-1]["data"])))
    a, b = SimpleNamespace(pes_pid=1), SimpleNamespace(pes_pid=2)
    svc.online.update({1: a, 2: b})
    pescmds._dispatch(svc, b, "CMD_SEND_SYNC_NOTICE", {"pid_list": [1, 1, 1, 2], "data": "1,"})
    assert sent == [(1, "CMD_WATCH_SYNC_NOTICE", "1,")]


def test_goal_from_both_games_counts_once(svc):
    for pid, user in ((1, "u1"), (2, "u2")):
        svc.accounts.db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, user, user))
    pesmatch._state(svc)["rooms"][1] = {"owner": 1, "members": [1, 2], "t": 0}
    a, b = SimpleNamespace(pes_pid=1, peer_ip="10.0.0.1"), SimpleNamespace(pes_pid=2, peer_ip="10.0.0.2")
    for s in (a, b):
        events.observe(svc, s, "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 2996})
    events.observe(svc, b, "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 5000})
    live = events.snapshot(svc)["live"][0]
    assert (live["home_score"], live["away_score"]) == (0, 2) and "goals_seen" not in live


def test_message_too_long_for_one_frame_is_refused(svc):
    announce._STATE.clear()
    announce.set_news(svc, "T" * 60, "a" * 600)                         # Latin text always fits
    import random
    rnd = random.Random(1)
    arabic = "".join(rnd.choice("ابتثجحخدذرزسشصضطظعغفقكلمنهوي ") for _ in range(600))
    with pytest.raises(announce.TooLong):
        title = "".join(rnd.choice("ابتثجحخدذرزسشصضطظعغفقكلمنهوي") for _ in range(60))
        announce.set_news(svc, title, arabic)                          # 1056 bytes encoded, limit 1024
    assert announce.snapshot(svc)["news"]["body"] == "a" * 600         # the old message stays


# ---- pairing only games that pass the P2P revision check (DATAPACK error otherwise) ---------------
REC_PARAM = "25D6DFBB091E75D525E090F993DBAE73"            # the sum every recorded session sent


def _searching(svc, pid, param=REC_PARAM, patch="1.01.00", send_sums=True, math=False):
    svc.accounts.db.execute("INSERT OR IGNORE INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)",
                            (pid, f"u{pid}", f"P{pid}"))
    s = SimpleNamespace(pes_pid=pid, peer_ip="192.168.1.5")
    if math:
        s.mod_features = frozenset({"math1"})                  # mod 0.4.0: "PESID2 <id> math1"
    svc.online[pid] = s
    pescmds._dispatch(svc, s, "CMD_JOIN_ONLINE_MODE", {"online_mode": "QUICK", "patch_version": patch,
                                                     "dlc_version": "1.00", "livedata_version": "0"})
    if send_sums:
        r = pescmds._dispatch(svc, s, "CMD_CHECK_CHEAT", {"param_check_sum": param, "entry_check_sum": "1E21"})
        assert r == {"result": "NOERR"}                     # any error would block online entry (CCWR450)
    pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"rqid": 1})
    return s


def _rooms(svc):
    return [r["members"] for r in pesmatch._state(svc)["rooms"].values()]


def test_players_with_different_player_data_are_not_paired(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    _searching(svc, 1)
    _searching(svc, 2, param="0" * 32)                      # edited stats / other live update
    _searching(svc, 2, param="0" * 32)                      # searching again: the host is told only once
    assert _rooms(svc) == []
    warns = [e["text"] for e in events.snapshot(svc)["feed"] if "can't play each other" in e["text"]]
    assert len(warns) == 1 and "P1" in warns[0] and "P2" in warns[0]
    _searching(svc, 3)                                      # same data as player 1, arrived later
    assert _rooms(svc) == [[1, 3]]
    assert [p for p, _ in pesmatch._state(svc)["queue"]] == [2]


def test_different_patch_is_not_paired_and_unknown_data_pairs_with_anyone(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    _searching(svc, 1)
    _searching(svc, 2, patch="1.00.00")
    assert _rooms(svc) == []
    _searching(svc, 3, send_sums=False)                     # a mode that skips CHECK_CHEAT: as before
    assert _rooms(svc) == [[1, 3]]


def test_tamper_report_reaches_the_admin_feed(svc):
    s = _searching(svc, 1)
    assert pescmds._dispatch(svc, s, "CMD_SEND_TAMPER_DATA", {"pid": 1, "data": "Doping:123"}) == {"result": "NOERR"}
    assert any("edited player data" in e["text"] for e in events.snapshot(svc)["feed"])


# ---- rating verdict: WATCH_GAMEADDPOINT answers the game's REQ after the final whistle -------------
def _played(svc, monkeypatch, goals, end=True):
    pushed = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, rq, k, sq, body: pushed.append((s.pes_pid, msgid, body)))
    a, b = _searching(svc, 1), _searching(svc, 2)
    for phase in ("1ST", "2ND"):
        for s in (a, b):
            pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": phase, "gtimer": 1})
    for gtimer, side in goals:
        for s in (a, b):                                    # both games report each goal
            pescmds._dispatch(svc, s, "CMD_ADD_SCORE", {"goal_side": side, "gtimer": gtimer})
    if end:
        for s in (a, b):                                    # (the verdict is pushed at the final whistle)
            pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "END", "gtimer": 2})
    pushed.clear()
    for s in (b, a):
        assert pescmds._dispatch(svc, s, "CMD_REQ_GAMEADDPOINT", {"pid": s.pes_pid}) == {"result": "NOERR"}
    return [(pid, body) for pid, m, body in pushed if m == "CMD_WATCH_GAMEADDPOINT"]


def _list(body):
    return {e["pid"]: e for e in (msgpack.unpackb(x, raw=False) for x in body["player_list"])}


def test_finished_match_gets_the_verdict_and_ratings_move_once(svc, monkeypatch):
    got = _played(svc, monkeypatch, [(100, "AWAY"), (200, "AWAY"), (300, "HOME")])
    assert [pid for pid, _ in got] == [2, 1]
    body = got[0][1]
    assert body is got[1][1]                                # computed once, same verdict for both
    assert (body["winner_side"], body["gameend_reason"], body["player_count"]) == ("WINNER_AWAY", "NORMAL_END", 2)
    pl = _list(body)
    assert (pl[1]["side"], pl[1]["oldrating"], pl[1]["rating"], pl[1]["contWin_num"]) == ("HOME", 1000, 984, 0)
    assert (pl[2]["side"], pl[2]["oldrating"], pl[2]["rating"], pl[2]["contWin_num"]) == ("AWAY", 1000, 1016, 1)
    assert msgpack.unpackb(pl[2]["pes_pname"], raw=False)["name"] == "P2"
    row = svc.accounts.db.execute("SELECT rating, win_streak FROM pes_players WHERE pid=2").fetchone()
    assert tuple(row) == (1016, 1)
    assert pescmds.validate("CMD_WATCH_GAMEADDPOINT", body)
    assert any("Ratings:" in e["text"] for e in events.snapshot(svc)["feed"])


def test_draw_keeps_equal_ratings(svc, monkeypatch):
    body = _played(svc, monkeypatch, [])[0][1]
    assert body["winner_side"] == "WINNER_NONE" and {e["rating"] for e in _list(body).values()} == {1000}


def test_aborted_match_gets_no_verdict(svc, monkeypatch):
    # anything but NORMAL_END would show error OLXH186; the game moves on by itself after 20 s
    assert _played(svc, monkeypatch, [(100, "HOME")], end=False) == []


def test_reconnect_takes_over_the_players_place_with_a_new_key(svc, monkeypatch):
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    old = _searching(svc, 1)                                 # waiting for an opponent when the line drops
    new = SimpleNamespace(peer_ip="192.168.1.5")
    r = pescmds._dispatch(svc, new, "CMD_RECONNECT", {"pid": 1, "platform": "STEAM", "patch_version": "1.01.00"})
    assert r["result"] == "NOERR" and r["key_size"] == 16 and len(base64.b64decode(r["key"])) == 16
    assert svc.online[1] is new and new.pes_pid == 1 and new.pes_sums == old.pes_sums
    assert pesmatch._state(svc)["queue"] == [(1, new)]
    assert pescmds._dispatch(svc, SimpleNamespace(), "CMD_RECONNECT", {"pid": 99})["result"] == "ERR_LOGIN_FAILED"


def test_same_math_mod_is_only_paired_with_same_math(svc, monkeypatch):
    # A Windows player and a Proton player fell out of sync at kick-off (SYNC_FAILED, RMYN393): their
    # C runtimes' sinf/powf/... differ. Mod 0.4.0 gives both the same functions; older mods can't.
    monkeypatch.setattr(pesmatch, "_send_later", lambda *a: None)
    _searching(svc, 1, math=True)
    _searching(svc, 2)                                      # older mod
    assert _rooms(svc) == []
    warns = [e["text"] for e in events.snapshot(svc)["feed"] if "can't play each other" in e["text"]]
    assert len(warns) == 1 and "P2 has an older mod" in warns[0]
    _searching(svc, 3)                                      # older mod too: plays player 2
    assert _rooms(svc) == [[2, 3]]
    _searching(svc, 4, math=True)
    assert _rooms(svc) == [[2, 3], [1, 4]]


def test_cancel_tells_the_waiting_search_it_ended(svc, monkeypatch):
    pushed = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, rq, k, sq, body: pushed.append((s.pes_pid, msgid, rq, body)))
    a = _searching(svc, 1)
    w = pescmds._dispatch(svc, a, "CMD_WATCH_MATCHING", {"rqid": 7})           # the game's parked watch
    assert w.get("_defer")
    assert pescmds._dispatch(svc, a, "CMD_CANCEL_MATCHING", {}) == {"result": "NOERR"}
    assert [(p, m, rq, b["matching_state"]) for p, m, rq, b in pushed] == [(1, "CMD_WATCH_MATCHING", 7, "CANCEL")]
    pushed.clear()
    pescmds._dispatch(svc, a, "CMD_CANCEL_MATCHING", {})                       # not searching: no second push
    assert pushed == []


def test_cancel_after_a_pairing_sends_no_cancel(svc, monkeypatch):
    pushed = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, rq, k, sq, body: pushed.append((s.pes_pid, msgid, body)))
    a, b = _searching(svc, 1), _searching(svc, 2)                              # paired at once
    assert {body["matching_state"] for _, _, body in pushed} == {"FINISH"}
    pescmds._dispatch(svc, a, "CMD_CANCEL_MATCHING", {})
    assert all(body["matching_state"] == "FINISH" for _, _, body in pushed)
