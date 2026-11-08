"""Online Co-op 1 vs 1 (pescoop.py): matching, the push layout, and the two session details co-op needs."""
from types import SimpleNamespace
import msgpack
import pytest
from pesserver import events, netsetup, pescmds, pescoop, pesmatch  # noqa: F401  (pescoop registers handlers)
from pesserver.accounts import Accounts
from pesserver.service import Service

REC_PARAM = "25D6DFBB091E75D525E090F993DBAE73"            # the sum every recorded session sent


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setitem(netsetup.STATE, "wan", "81.2.69.160")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.100")
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def _player(svc, pid, coop=True, param=REC_PARAM):
    svc.accounts.db.execute("INSERT OR IGNORE INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)",
                            (pid, f"u{pid}", f"P{pid}"))
    s = SimpleNamespace(pes_pid=pid, peer_ip="192.168.1.5",
                        mod_features=frozenset({"math1", "coop1"} if coop else {"math1"}))
    svc.online[pid] = s
    pescmds._dispatch(svc, s, "CMD_JOIN_ONLINE_MODE", {"online_mode": "COOP", "patch_version": "1.01.00",
                                                     "dlc_version": "1.00", "livedata_version": "0"})
    pescmds._dispatch(svc, s, "CMD_CHECK_CHEAT", {"param_check_sum": param, "entry_check_sum": "1E21"})
    return s


def _search(svc, s):
    return pescmds._dispatch(svc, s, "CMD_START_COOP_MATCHING",
                             {"matching_type": "ONLINE_COOP", "use_team_id": 262143, "retry_num": 0})


@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, rqid, kind, seq, body: sent.append((s.pes_pid, msgid, body)))
    return sent


def _unpack(b):
    return msgpack.unpackb(b, raw=False)


def test_quickmatch_option_skips_group_matching(svc):
    r = pescmds._dispatch(svc, _player(svc, 1), "CMD_GET_COOP_QUICKMATCHOPTION", {})
    assert r["force_alone"] == "YES" and r["is_vscom"] == "NO"


def test_two_players_get_one_push_each_with_one_owner(svc, pushes):
    a, b = _player(svc, 1), _player(svc, 2)
    assert _search(svc, a) == {"result": "NOERR"} and pushes == []
    assert _search(svc, b) == {"result": "NOERR"}
    assert sorted(p for p, m, _ in pushes) == [1, 2] and {m for _, m, _ in pushes} == {"CMD_WATCH_COOP_MATCHING"}
    body = pushes[0][2]
    assert body["matching_state"] == "FINISH" and body["group_list_num"] == 2
    groups = [_unpack(g) for g in body["group_list"]]
    members = [_unpack(_unpack(g["member_list"][0])["member"]) for g in groups]
    assert [m["pid"] for m in members] == [1, 2]
    assert [m["side"] for m in members] == ["HOME", "AWAY"]
    assert [m["is_owner"] for m in members] == ["YES", "NO"]          # exactly one owner: the HOME player
    assert [g["owner_pid"] for g in groups] == [1, 2] and [g["member_list_num"] for g in groups] == [1, 1]
    teams = [g["use_team_id"] for g in groups]                # no team-select screen: the server picks two
    assert len(set(teams)) == 2 and 262143 not in teams       # "no team" on both sides crashed the load
    rooms = [r for r in pesmatch._state(svc)["rooms"].values()]
    assert [(r["members"], r.get("coop")) for r in rooms] == [([1, 2], True)]


def test_gamerelay_says_p2p_for_a_coop_room_only(svc, pushes, monkeypatch):
    monkeypatch.setattr(netsetup, "advertised_addr", lambda svc, sess: "192.168.1.100")
    a, b, c = _player(svc, 1), _player(svc, 2), _player(svc, 3)
    _search(svc, a); _search(svc, b)
    http = SimpleNamespace()                                  # GET_GAMERELAY arrives over HTTP: pid in the form
    assert pescmds._dispatch(svc, http, "CMD_GET_GAMERELAY", {"pid": "1"})["game_sycom"] == "P2P"
    assert pescmds._dispatch(svc, http, "CMD_GET_GAMERELAY", {"pid": "3"})["game_sycom"] == ""


def test_sync_progress_also_goes_back_to_the_sender(svc, pushes):
    a, b = _player(svc, 1), _player(svc, 2)
    data = '{"notice_key":"SYNC_PROGRESS","pid_list":[1]}\n'
    pescmds._dispatch(svc, a, "CMD_SEND_NOTICE", {"pid_list": [1], "data": data})
    assert [(p, m) for p, m, _ in pushes] == [(1, "CMD_WATCH_NOTICE")]
    pushes.clear()                                            # other notices still never go back to the sender
    pescmds._dispatch(svc, a, "CMD_SEND_NOTICE", {"pid_list": [1, 2], "data": '{"notice_key":"ROOM_SHARE_INFO"}'})
    assert [p for p, _, _ in pushes] == [2]


def test_players_without_the_coop_fix_are_not_paired(svc, pushes):
    old, new = _player(svc, 1, coop=False), _player(svc, 2)
    _search(svc, old); _search(svc, new)
    assert pushes == [] and pesmatch._state(svc)["rooms"] == {}
    warns = [e["text"] for e in events.snapshot(svc)["feed"] if "co-op" in e["text"].lower()]
    assert len(warns) == 1 and "P1" in warns[0]
    _search(svc, _player(svc, 3))
    assert sorted(p for p, _, _ in pushes) == [2, 3]


def test_different_data_is_not_paired_and_cancel_leaves_the_queue(svc, pushes):
    a, b = _player(svc, 1), _player(svc, 2, param="0" * 32)
    _search(svc, a); _search(svc, b)
    assert pushes == []
    pescmds._dispatch(svc, a, "CMD_CANCEL_COOP_MATCHING", {})
    _search(svc, _player(svc, 3))
    assert pushes == [] and [x[0] for x in pesmatch._state(svc)["coop_queue"]] == [2, 3]


def test_coop_level_points_at_full_time(svc, pushes):
    a, b = _player(svc, 1), _player(svc, 2)
    _search(svc, a); _search(svc, b)
    (rid, room), = pesmatch._state(svc)["rooms"].items()
    room["goals"] = {(500, "AWAY"), (700, "AWAY"), (900, "HOME")}
    for s in (a, b):
        pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "END"})
    pescmds._dispatch(svc, a, "CMD_CHANGE_GAMEPHASE", {"phase": "END"})    # repeated END: counted once
    assert pescoop.level(svc, 1) == (1, 10, 1) and pescoop.level(svc, 2) == (1, 30, 1)
    pesmatch._state(svc)["rooms"].clear()                     # the game asks after leaving the room
    r = pescmds._dispatch(svc, b, "CMD_GET_COOP_LEVEL_RESULT", {"pid": 2, "match_id": "x"})
    rows = {(u := _unpack(_unpack(e)["user_id"]))["pid"]: _unpack(e) for e in r["coop_level_user_list"]}
    assert set(rows) == {1, 2} and (rows[2]["before_point"], rows[2]["after_point"], rows[2]["point"]) == (0, 30, 30)
    assert rows[1]["after_level"] == 1 and _unpack(_unpack(r["coop_level_user_list"][0])["user_id"])["pname"]
    prof = _unpack(pescmds._dispatch(svc, a, "CMD_GET_COOP_PROFILE", {"pid": 2})["coop_profile_data"])
    assert (prof["coop_level"], prof["coop_level_point"], prof["online_match"]) == (1, 30, 1)
    room2 = {"owner": 1, "members": [1, 2], "coop": True, "goals": {(1, "HOME")} }
    for _ in range(4):
        room2.pop("coop_awarded", None); pescoop.award(svc, room2)
    assert pescoop.level(svc, 1)[0] == 2                      # 10 + 4 x 30 = 130 points: level 2
    pescoop.forget(svc, 1)
    assert pescoop.level(svc, 1) == (1, 0, 0)
