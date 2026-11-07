"""Matchday (pesmatchday.py): list, the two-level compe_info, joining with a squad, points after a match."""
import asyncio, time

from pesserver import pescmds, pesmatch, pesmatchday
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)


def test_event_info_join_and_points(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        lst = pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_LIST", {"is_myclub": "NO", "start_index": 0})
        ev = unb(lst["compe_list"][0])
        assert ev["compe_id"] == 1 and ev["is_entry"] == "NO" and ev["end_group_match_date"] > time.time()
        info = unb(pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_INFO", {"compe_id": 1})["compe_info"])
        ov = unb(info["info_overview"])                     # the event map lives one level down
        assert ov["compe_id"] == 1 and unb(ov["group_info_a"])["team_list"] and "all_reward_info" not in info
        assert isinstance(info["fixed_weather"], str)       # strings: an int would be read as a pointer
        for s, grp, team in ((a, "MATCHDAY_GROUP_A", 101), (b, "MATCHDAY_GROUP_B", 100)):
            pescmds._dispatch(svc, s, "CMD_JOIN_MATCHDAY", {"compe_id": 1, "entry_matchday_group": grp, "team_id": team,
                                                            "sp_gameplan_data": "QUJD", "sp_gameplan_data_size": 3,
                                                            "sp_member_list": [40571, 109123]})
        sp = pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_SP_GAMEPLAN", {"compe_id": 1})
        assert sp["sp_member_list"] == [40571, 109123] and sp["gameplan_data"] == "QUJD"
        assert pescmds.validate("CMD_GET_MATCHDAY_SP_GAMEPLAN", sp)
        info = unb(pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_INFO", {"compe_id": 1})["compe_info"])
        assert info["joined_matchday_group"] == "MATCHDAY_GROUP_A" and unb(info["info_overview"])["is_entry"] == "YES"
        # a match: Matchday players get their own queue
        pescmds._dispatch(svc, a, "CMD_START_MATCHING", {"matching_type": "MATCHDAY"})
        pescmds._dispatch(svc, b, "CMD_START_MATCHING", {"matching_type": "MATCHDAY"})
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        assert room["mode"] == "MATCHDAY"
        room.update(goals={(500, "HOME"), (900, "HOME")}, phase={1: "END", 2: "END"})
        pesmatch._verdict(svc, rid, room)
        assert pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_ACHIEVEMENT_REWARD", {"compe_id": 1})["add_matchday_point"] == 3
        assert pescmds._dispatch(svc, a, "CMD_GET_MATCHDAY_ACHIEVEMENT_REWARD", {"compe_id": 1})["add_matchday_point"] == 0
        gm = unb(unb(pescmds._dispatch(svc, b, "CMD_GET_MATCHDAY_MAINMENU_INFO", {"compe_id": 1})["mainmenu_info"])["group_match_info"])
        assert (gm["total_matchday_point_group_a"], gm["total_matchday_point_group_b"], gm["matchday_point"]) == (3, 0, 0)
    asyncio.run(go())


def test_points_screen_breakdown_in_tenths(svc):
    """Match Rewards: Gameplay Review and Squad Rating are shown in tenths (1 -> "0.1", live 2026-11-07)."""
    from types import SimpleNamespace
    r = pescmds._dispatch(svc, SimpleNamespace(pes_pid=1), "CMD_GET_MATCHDAY_ACHIEVEMENT_REWARD", {"pid": 1})
    assert r["squad_rating"] == 10 and r["gameplay_quality"] == r["add_matchday_point"] * 10


def test_side_ranking(svc):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        for pid, name in ((1, "Ann"), (2, "Bob"), (3, "Cid")):
            db.execute("INSERT OR REPLACE INTO pes_players (pid, username, name, created) VALUES (?,?,?,0)", (pid, f"u{pid}", name))
        pesmatchday._setup(db)
        for pid, grp, team, pts in ((1, "MATCHDAY_GROUP_A", 101, 4), (2, "MATCHDAY_GROUP_B", 100, 9), (3, "MATCHDAY_GROUP_A", 108, 7)):
            db.execute("INSERT INTO pes_matchday (pid, compe_id, grp, team_id, points, matches) VALUES (?,?,?,?,?,1)",
                       (pid, pesmatchday.COMPE_ID, grp, team, pts))
    r = pescmds.dispatch(svc, Sess(1), "CMD_GET_MATCHDAY_RANKING", {"pid": 1, "compe_id": 1, "start_index": 0, "view_num": 100})
    ents = [unb(e) for e in r["rank_list"]]
    assert [unb(e["pname"])["pid"] for e in ents] == [3, 1]                   # my side only, by points
    assert [e["rank"] for e in ents] == [1, 2] and ents[0]["matchday_point"] == 7 and ents[0]["team_id"] == 108
    assert unb(unb(ents[1]["pname"])["pname"])["name"] == "Ann"
    assert pesmatchday.side_rank(svc, 1) == 2 and pesmatchday.side_rank(svc, 2) == 1 and pesmatchday.side_rank(svc, 9) == 0
