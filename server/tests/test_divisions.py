"""Online Divisions (pesdivisions.py): the division_info reply, a ten-match season, promotion, separate queues."""
import asyncio

import msgpack

from pesserver import pescmds, pesdivisions, pesmatch
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)


def _user(svc, pid, name):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        db.execute("INSERT OR REPLACE INTO pes_players (pid, username, name) VALUES (?, ?, ?)", (pid, f"u{pid}", name))


def test_new_player_gets_division_10_and_a_full_season(svc):
    a = Sess(1); svc.online[1] = a
    r = pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {"pid": 1, "is_season_end": "NO"})
    info = unb(r["division_info"])
    assert info["division"] == "D10" and info["other_match_num"] == 10    # 0 would start the season-end screens
    assert info["promotion_point"] == 12 and info["match_history_list"] == []
    assert isinstance(unb(info["match_record"])["rating"], float)
    assert pescmds._dispatch(svc, a, "CMD_GET_SERVER_ENV", {})["max_division"] == "D10"


def test_season_of_ten_then_promotion(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        for pid, s in ((1, a), (2, b)):
            s.pes_username = f"u{pid}"
        for n in range(10):
            room = {"owner": 1, "members": [1, 2], "t": n, "mode": "OC_RANK", "match_id": f"m{n}",
                    "goals": {(100, "HOME")}, "phase": {1: "END", 2: "END"}}
            pesmatch._verdict(svc, n + 1, room)                      # A wins 1-0 every time
        ia = unb(pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])
        ib = unb(pescmds._dispatch(svc, b, "CMD_GET_DIVISION_INFO", {})["division_info"])
        assert ia["other_match_num"] == 0 and sum(unb(h)["division_point"] for h in ia["match_history_list"]) == 30
        assert sum(unb(h)["division_point"] for h in ib["match_history_list"]) == 0
        assert unb(ia["match_history_list"][0])["win_lose"] == "WIN" and unb(ib["match_history_list"][0])["win_lose"] == "LOSE"
        # the same match reported twice counts once
        pesdivisions.record(svc, {"owner": 1, "members": [1, 2], "match_id": "m0", "goals": {(1, "HOME")}})
        assert len(unb(pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])["match_history_list"]) == 10
        # season end: 30 points >= double promotion (21) -> D8; 0 points in D10 -> stays D10
        pescmds._dispatch(svc, a, "CMD_FINISH_SEASON", {})
        pescmds._dispatch(svc, b, "CMD_FINISH_SEASON", {})
        ia = unb(pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])
        ib = unb(pescmds._dispatch(svc, b, "CMD_GET_DIVISION_INFO", {})["division_info"])
        assert (ia["division"], ia["other_match_num"]) == ("D8", 10) and ib["division"] == "D10"
        assert unb(ia["match_record"])["win_count"] == 10
        # asking twice doesn't move anyone again
        pescmds._dispatch(svc, a, "CMD_FINISH_SEASON", {})
        assert unb(pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])["division"] == "D8"
    asyncio.run(go())


def test_divisions_and_quick_match_are_separate_queues(svc):
    async def go():
        a, b, c = Sess(1), Sess(2), Sess(3)
        svc.online.update({1: a, 2: b, 3: c})
        pescmds._dispatch(svc, a, "CMD_START_MATCHING", {"matching_type": "OC_RANK"})
        pescmds._dispatch(svc, b, "CMD_START_MATCHING", {"matching_type": "OC_QUICK"})
        assert pesmatch._state(svc)["rooms"] == {}                   # different modes: no match
        pescmds._dispatch(svc, c, "CMD_START_MATCHING", {"matching_type": "OC_RANK"})
        (room,) = pesmatch._state(svc)["rooms"].values()
        assert set(room["members"]) == {1, 3} and room["mode"] == "OC_RANK"
    asyncio.run(go())


def test_thresholds():
    assert pesdivisions.thresholds(10)[2] == 0 and pesdivisions.thresholds(1)[0] > 30


def test_set_gameteam_gives_the_history_the_opponents_team(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        for s in (a, b):
            pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"matching_type": "OC_RANK"})
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        pescmds._dispatch(svc, a, "CMD_SET_GAMETEAM", {"home_team_id": 108, "away_team_id": 101})
        room["phase"] = {1: "END", 2: "END"}
        pesmatch._verdict(svc, rid, room)
        ha = unb(unb(pescmds._dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])["match_history_list"][0])
        hb = unb(unb(pescmds._dispatch(svc, b, "CMD_GET_DIVISION_INFO", {})["division_info"])["match_history_list"][0])
        assert ha["enemy_team_id"] == 101 and hb["enemy_team_id"] == 108
    asyncio.run(go())
