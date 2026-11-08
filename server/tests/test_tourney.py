"""Online Tournament (pestourney.py): the monthly Friends Cup, joining, its own matchmaking queue, points, rankings."""
import asyncio, time

from pesserver import pescmds, pesmatch, pesresults, pestourney  # noqa: F401  (handlers)
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)


def _players(svc, *pids):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        for pid in pids:
            db.execute("INSERT OR REPLACE INTO pes_players (pid, username, name, created) VALUES (?,?,?,0)",
                       (pid, f"u{pid}", f"Player{pid}"))


def _enter(svc, s, team):
    lst = pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_COMPE_LIST", {"mode": "PESLEAGUE", "start_index": 0})
    cup = unb(lst["compe_list"][0])
    pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cup["compe_id"], "team_id": team})
    pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cup["compe_id"]})
    return cup


def test_list_has_friends_cup_and_replays(svc):
    lst = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_LIST", {"mode": "PESLEAGUE", "start_index": 0})
    names = [unb(x)["compe_name"] for x in lst["compe_list"]]
    assert names[0].startswith("Friends Cup ") and names[-1] == "Match Replays" and lst["list_num"] == 2
    cup = unb(lst["compe_list"][0])
    assert cup["match_type"] == "PES_LEAGUE_MATCH_1VS1" and cup["start_date"] <= time.time() < cup["end_date"]
    # the info carries the same fields inside compe_data (else the game takes the default type)
    info = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cup["compe_id"]})
    assert unb(info["compe_data"])["match_type"] == "PES_LEAGUE_MATCH_1VS1"
    # one cup per month, not one per request
    assert len(pestourney.competitions(svc)) == 1
    # Match Replays still answers with the lobby type
    rep = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": 1})
    assert unb(rep["compe_data"])["match_type"] == "PES_LEAGUE_MATCH_1VS1_LOBBY"


def test_join_match_points_ranking_results(svc):
    async def go():
        _players(svc, 1, 2, 3)
        a, b, c = Sess(1), Sess(2), Sess(3)
        svc.online.update({1: a, 2: b, 3: c})
        cup = _enter(svc, a, 9)
        _enter(svc, b, 13)
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cup["compe_id"]})["is_entry"] == "YES"
        # a Quick Match player is not paired with a tournament player
        pescmds._dispatch(svc, c, "CMD_START_MATCHING", {"matching_type": "OC_QUICK"})
        pescmds._dispatch(svc, a, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})
        assert not pesmatch._state(svc)["rooms"]
        pescmds._dispatch(svc, b, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        assert room["mode"] == "PES_LEAGUE" and room["compe"] == cup["compe_id"] and set(room["members"]) == {1, 2}
        room.update(goals={(500, "HOME"), (900, "HOME"), (1200, "AWAY")}, phase={1: "END", 2: "END"},
                    teams={1: 9, 2: 13}, match_id="m1")
        v = pesmatch._verdict(svc, rid, room)
        shown = {x["pid"]: (x["oldrating"], x["rating"]) for x in map(unb, v["player_list"])}
        assert shown == {1: (0, 3), 2: (0, 0)}            # the rating screen shows the competition points
        assert pestourney.record(svc, room) == {1: (0, 3), 2: (0, 0)}   # a second call counts nothing
        m1 = pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cup["compe_id"]})
        m2 = pescmds._dispatch(svc, b, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cup["compe_id"]})
        assert (m1["point"], m1["win_count"], m1["match_num"], m1["team_id"]) == (3, 1, 1, 9)
        assert (m2["point"], m2["lose_count"], m2["team_id"]) == (0, 1, 13)
        r = pescmds._dispatch(svc, b, "CMD_GET_PESLEAGUE_COMPE_RANKING",
                              {"compe_id": cup["compe_id"], "start_index": 0, "view_num": 100})
        ents = [unb(e) for e in r["rank_list"]]
        assert [(e["pid"], e["rank"], e["point"]) for e in ents] == [(1, 1, 3), (2, 2, 0)] and r["list_max"] == 2
        assert (ents[0]["get_score"], ents[0]["lose_score"], ents[0]["diff_score"], ents[0]["continuous_win"]) == (2, 1, 1, 1)
        assert [e["is_mydata"] for e in ents] == ["NO", "YES"] and unb(ents[0]["clan_owner_name"])["name"] == "Player1"
        assert pescmds.validate("CMD_GET_PESLEAGUE_COMPE_RANKING", r)
        res = pescmds._dispatch(svc, b, "CMD_GET_GAMERESULTS", {"game_res_type": "PESLEAGUE",
                                                                "pesleague_compe_id": cup["compe_id"]})
        e = unb(res["rlist"][0])
        assert res["rlist_num"] == 1 and e["match_category"] == "PESLEAGUE_MATCH" and e["team"] == [13, 9]
        assert [unb(s)["score_1st"] for s in e["score"]] == [1, 2]       # from the asking player's side
    asyncio.run(go())


def test_players_in_different_competitions_are_not_paired(svc):
    async def go():
        _players(svc, 1, 2)
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        cup = _enter(svc, a, 9)
        _enter(svc, b, 13)
        b.pes_compe = cup["compe_id"] + 1
        for s in (a, b):
            pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})
        assert not pesmatch._state(svc)["rooms"]
    asyncio.run(go())


def test_forfeit_counts_and_delete_forgets(svc):
    async def go():
        _players(svc, 1, 2)
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        cup = _enter(svc, a, 9)
        _enter(svc, b, 13)
        for s in (a, b):
            pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        room.update(phase={1: "1ST", 2: "1ST"}, match_id="m2")
        assert pesmatch.forfeit(svc, rid, 1)
        assert pestourney.entry(svc, cup["compe_id"], 2)["points"] == 3
        assert pestourney.entry(svc, cup["compe_id"], 1)["lose"] == 1
        pestourney.forget(svc, 1)
        assert pestourney.entry(svc, cup["compe_id"], 1) is None
        assert [r[1] for r in pestourney.standings(svc, cup["compe_id"])] == [2]
    asyncio.run(go())


def test_admin_api_create_end_delete_and_monthly_cup(svc):
    import json
    st, body, _ = pestourney.admin_api(svc, "/admin/api/tourneys", "GET", b"")
    snap = json.loads(body)
    assert st == "200 OK" and snap["monthly_cup"] is True and len(snap["tourneys"]) == 1
    auto = snap["tourneys"][0]
    # a host-made cup next to it, with its own team list
    st, body, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST",
                                       json.dumps({"name": "Saturday Cup", "start": "2020-01-01",
                                                   "end": "2099-01-01 18:00", "teams": [45, 50]}).encode())
    cid = json.loads(body)["compe_id"]
    assert st == "200 OK" and cid > auto["compe_id"]
    assert pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_TEAM_LIST", {"compe_id": cid})["team_list"] == [45, 50]
    assert pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", b'{"name": ""}')[0] == "400 Bad Request"
    assert pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST",
                                b'{"name": "x", "start": "2030-01-01", "end": "2029-01-01"}')[0] == "400 Bad Request"
    assert pestourney.admin_api(svc, "/admin/api/tourneys/end", "POST", b'{"compe_id": 999}')[0] == "404 Not Found"
    # ending the monthly cup does not make a new one; ended competitions leave the game's list
    pestourney.admin_api(svc, "/admin/api/tourneys/end", "POST", json.dumps({"compe_id": auto["compe_id"]}).encode())
    names = [unb(x)["compe_name"] for x in
             pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_LIST", {"mode": "PESLEAGUE"})["compe_list"]]
    assert names == ["Saturday Cup", "Match Replays"]
    past = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_LIST", {"mode": "PESLEAGUE", "is_end": "YES"})
    assert [unb(x)["compe_name"] for x in past["compe_list"]] == [auto["name"]]
    # deleting everything: the monthly cup stays away this month, and can be switched off
    for c in pestourney.competitions(svc):
        pestourney.admin_api(svc, "/admin/api/tourneys/delete", "POST", json.dumps({"compe_id": c["compe_id"]}).encode())
    assert pestourney.competitions(svc) == []
    pestourney.admin_api(svc, "/admin/api/tourneys/monthly", "POST", b'{"on": false}')
    assert pestourney.monthly_cup(svc) is False


def test_monthly_cup_comes_each_month(svc):
    now = time.time()
    first = pestourney.competitions(svc, now)
    nxt = pestourney.competitions(svc, first[0]["end"] + 3600)
    assert len(first) == 1 and len(nxt) == 2 and nxt[0]["start"] == first[0]["end"]
    assert nxt[0]["name"] != first[0]["name"]


def test_match_rules_in_compe_info(svc):
    """compe_regulation (top level of COMPE_INFO): match_time in minutes, always sent (the game's own default is
    about 2 minutes); the host can change it. No evened-out ratings (the server has no such stats to give)."""
    import json
    cup = pestourney.competitions(svc)[0]
    info = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cup["compe_id"]})
    assert unb(info["compe_regulation"]) == {"match_time": 10, "is_equalization": "NO"}
    st, _, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"compe_id": cup["compe_id"], "match_minutes": 15}).encode())
    assert st == "200 OK"
    info = pescmds._dispatch(svc, Sess(1), "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cup["compe_id"]})
    assert unb(info["compe_regulation"]) == {"match_time": 15, "is_equalization": "NO"}
    assert pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"compe_id": cup["compe_id"], "match_minutes": 3}).encode())[0] == "400 Bad Request"


def test_results_notice_after_the_end(svc):
    async def go():
        _players(svc, 1, 2)
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        cup = _enter(svc, a, 9)
        _enter(svc, b, 13)
        for s in (a, b):
            pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        room.update(goals={(500, "AWAY")}, phase={1: "END", 2: "END"}, match_id="m3")
        pesmatch._verdict(svc, rid, room)
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_MAINMENU_INFO", {"mode": "PESLEAGUE"})["new_result"] == "NO"
        pestourney.end_now(svc, cup["compe_id"])
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_MAINMENU_INFO", {"mode": "PESLEAGUE"})["new_result"] == "YES"
        r = pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_COMPE_RESULT_LIST", {"compe_id": 0, "is_unconfirm_list": "YES"})
        e = unb(r["result_list"][0])
        assert (r["result_list_num"], e["compe_id"], e["player_rank"], e["entry_user_num"]) == (1, cup["compe_id"], 2, 2)
        assert pescmds.validate("CMD_GET_PESLEAGUE_COMPE_RESULT_LIST", r)
        # seen once: no new notice, but the past result stays listed
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_MAINMENU_INFO", {"mode": "PESLEAGUE"})["new_result"] == "NO"
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_COMPE_RESULT_LIST", {"is_unconfirm_list": "YES"})["result_list_num"] == 0
        assert pescmds._dispatch(svc, a, "CMD_GET_PESLEAGUE_COMPE_RESULT_LIST", {"is_unconfirm_list": "NO"})["result_list_num"] == 1
        assert pescmds._dispatch(svc, b, "CMD_GET_PESLEAGUE_MAINMENU_INFO", {"mode": "PESLEAGUE"})["new_result"] == "YES"
    asyncio.run(go())
