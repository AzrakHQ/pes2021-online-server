"""Knockout cups (pescup.py): the draw, pairing only the two players of a tie, results, groups, the host's calls."""
import asyncio, json, os, random, time

import pytest

from pesserver import pescmds, pescup, pesmatch, pesresults, pestourney  # noqa: F401  (handlers)
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)
from test_tourney import _players


def _cup(svc, n, name="Saturday Cup"):
    """A cup with players 1..n entered; returns (competition, {pid: session})."""
    st, body, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"name": name, "start": "2020-01-01", "end": "2099-01-01", "format": "cup"}).encode())
    assert st == "200 OK"
    cid = json.loads(body)["compe_id"]
    _players(svc, *range(1, n + 1))
    sess = {p: Sess(p) for p in range(1, n + 1)}
    svc.online.update(sess)
    for p, s in sess.items():
        pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
        pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cid})
    return pestourney.get(svc, cid), sess


def _search(svc, *sessions):
    for s in sessions:
        pescmds._dispatch(svc, s, "CMD_START_MATCHING", {"matching_type": "PES_LEAGUE"})


def _play(svc, sess, a, b, ag, bg, pk=None):
    """a and b search, are paired, and the match ends ag-bg (a is whoever became the room owner or not)."""
    before = set(pesmatch._state(svc)["rooms"])
    _search(svc, sess[a], sess[b])
    (rid,) = set(pesmatch._state(svc)["rooms"]) - before
    room = pesmatch._state(svc)["rooms"][rid]
    assert set(room["members"]) == {a, b}
    side = {room["owner"]: "HOME"}
    goals = {(100 + i, side.get(a, "AWAY")) for i in range(ag)} | {(500 + i, side.get(b, "AWAY")) for i in range(bg)}
    room.update(goals=goals, phase={a: "END", b: "END"}, teams={a: 9, b: 9}, match_id=f"m{rid}")
    if pk:
        room["pk"] = {side.get(a, "AWAY"): pk[0], side.get(b, "AWAY"): pk[1]}
    assert pesmatch._verdict(svc, rid, room) is not None
    del pesmatch._state(svc)["rooms"][rid]


def _ties(svc, c, stage="K"):
    return [f for f in pescup.fixtures(svc, c["compe_id"]) if f["stage"] == stage]


def test_knockout_of_four_to_the_final(svc):
    async def go():
        c, sess = _cup(svc, 4)
        cid = c["compe_id"]
        assert pescup.stage(svc, cid) == "entry" and "Entered: Player1, Player2, Player3, Player4" in pestourney.outline(c, svc)
        # before the draw nobody is paired
        _search(svc, sess[1], sess[2])
        assert not pesmatch._state(svc)["rooms"]
        pesmatch._state(svc)["queue"].clear()
        pescup.draw(svc, c, rng=random.Random(1))
        semis = [f for f in _ties(svc, c) if f["round"] == 1]
        assert len(semis) == 2 and len(_ties(svc, c)) == 3 and pescup.stage(svc, cid) == "knockout"
        (a, b), (x, y) = [(f["a"], f["b"]) for f in semis]
        assert sorted((a, b, x, y)) == [1, 2, 3, 4]
        # two players of different ties search: not paired; the tie's own two are
        _search(svc, sess[a], sess[x])
        assert not pesmatch._state(svc)["rooms"]
        pesmatch._state(svc)["queue"].clear()
        _play(svc, sess, a, b, 2, 0)
        assert pescup.to_play(svc, cid, a) == [] and pescup.to_play(svc, cid, x) == [y]
        _search(svc, sess[a])                              # waits: the other semi-final isn't decided yet
        _play(svc, sess, x, y, 0, 1)
        _search(svc, sess[y])
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        assert set(room["members"]) == {a, y}
        room.update(goals={(1, "HOME")}, phase={a: "END", y: "END"}, teams={a: 9, y: 9}, match_id="final")
        pesmatch._verdict(svc, rid, room)
        champ = room["owner"]
        assert pescup.stage(svc, cid) == "done"
        done = pestourney.get(svc, cid)
        assert done["end"] <= time.time()                 # the cup ended itself: the game's results notice comes
        rows = pestourney.standings(svc, cid)
        assert rows[0][1] == champ and {rows[1][1]} == {a, y} - {champ} and {r[1] for r in rows[2:]} == {b, x}
        res = pescmds._dispatch(svc, sess[champ], "CMD_GET_PESLEAGUE_COMPE_RESULT_LIST", {"is_unconfirm_list": "YES"})
        assert unb(res["result_list"][0])["player_rank"] == 1
        text = pestourney.outline(done, svc)
        assert text.startswith(f"Player{champ} won Saturday Cup.") and "FINAL" in text and "SEMI-FINALS" in text
    asyncio.run(go())


def test_three_players_one_bye_and_a_level_tie_is_played_again(svc):
    async def go():
        c, sess = _cup(svc, 3)
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(2))
        r1 = [f for f in _ties(svc, c) if f["round"] == 1]
        bye = next(f for f in r1 if f["how"] == "bye")
        tie = next(f for f in r1 if f["how"] is None)
        final = next(f for f in _ties(svc, c) if f["round"] == 2)
        assert bye["winner"] in (final["a"], final["b"]) and None in (final["a"], final["b"])
        a, b = tie["a"], tie["b"]
        _play(svc, sess, a, b, 1, 1)                       # level: nothing decided, they can be paired again
        assert _ties(svc, c)[r1.index(tie)]["replays"] == 1 and pescup.to_play(svc, cid, a) == [b]
        _play(svc, sess, a, b, 2, 2, pk=(4, 5))            # level again, decided on penalties
        f = next(f for f in _ties(svc, c) if f["fid"] == tie["fid"])
        assert f["winner"] == b and (f["a_goals"], f["b_goals"], f["a_pk"], f["b_pk"]) == (2, 2, 4, 5)
        assert sorted(pescup.to_play(svc, cid, b)) == [bye["winner"]]
    asyncio.run(go())


def test_groups_then_knockout(svc):
    async def go():
        c, sess = _cup(svc, 6)
        cid = c["compe_id"]
        pescup.draw(svc, c, groups=True, rng=random.Random(3))
        g = _ties(svc, c, "G")
        assert len(g) == 6 and {f["grp"] for f in g} == {0, 1} and pescup.stage(svc, cid) == "groups"
        members = {grp: sorted({p for f in g if f["grp"] == grp for p in (f["a"], f["b"])}) for grp in (0, 1)}
        # players of different groups are not paired
        _search(svc, sess[members[0][0]], sess[members[1][0]])
        assert not pesmatch._state(svc)["rooms"]
        pesmatch._state(svc)["queue"].clear()
        for grp, (p, q, r) in members.items():             # p beats q and r, q beats r
            _play(svc, sess, p, q, 2, 0)
            assert not _ties(svc, c)                       # no knockout before the groups are finished
            _play(svc, sess, p, r, 1, 0)
            _play(svc, sess, q, r, 3, 1)
        ko = _ties(svc, c)
        semis = [f for f in ko if f["round"] == 1]
        assert pescup.stage(svc, cid) == "knockout" and len(ko) == 3
        w, ru = [members[0][0], members[1][0]], [members[0][1], members[1][1]]
        for f in semis:                                    # a group winner meets the other group's runner-up
            assert (f["a"] in w) != (f["b"] in w)
            assert (f["a"] in members[0]) != (f["b"] in members[0])
        snap = pescup.snapshot(svc, c)
        assert [x["name"] for x in snap["groups"]] == ["Group A", "Group B"] and snap["groups"][0]["table"][0]["pts"] == 6
        assert [r["name"] for r in snap["rounds"]] == ["Semi-finals", "Final"]
        text = pestourney.outline(c, svc)
        assert "GROUP A" in text and "SEMI-FINALS" in text and len(text) <= 2000
        # a played group pair can't play again
        assert not pescup.can_pair(svc, cid, members[0][1], members[0][2])
    asyncio.run(go())


def test_host_calls_draw_award_undraw(svc):
    async def go():
        c, sess = _cup(svc, 4)
        cid = c["compe_id"]
        api = lambda route, **body: pestourney.admin_api(svc, "/admin/api/tourneys/" + route, "POST",   # noqa: E731
                                                         json.dumps({"compe_id": cid, **body}).encode())
        assert api("draw", groups=True)[0] == "200 OK"                    # 4: one group, the top two in the final
        assert len(_ties(svc, c, "G")) == 6
        assert api("undraw")[0] == "200 OK"
        assert api("draw")[0] == "200 OK" and api("draw")[0] == "400 Bad Request"
        assert api("undraw")[0] == "200 OK" and pescup.stage(svc, cid) == "entry"
        assert api("draw")[0] == "200 OK"
        # after the draw a new friend is not entered
        _players(svc, 9)
        late = Sess(9)
        pescmds._dispatch(svc, late, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
        assert pestourney.entry(svc, cid, 9) is None
        tie = next(f for f in _ties(svc, c) if f["round"] == 1)
        assert api("award", fid=tie["fid"], winner=99)[0] == "400 Bad Request"
        other = next(f for f in _ties(svc, c) if f["round"] == 1 and f["fid"] != tie["fid"])
        _search(svc, sess[tie["b"]], sess[other["b"]])     # both wait for their own opponents
        assert not pesmatch._state(svc)["rooms"]
        assert api("award", fid=tie["fid"], winner=tie["b"])[0] == "200 OK"
        f = next(f for f in _ties(svc, c) if f["fid"] == tie["fid"])
        assert (f["winner"], f["how"], f["a_goals"], f["b_goals"]) == (tie["b"], "walkover", 0, 3)
        assert api("undraw")[0] == "400 Bad Request"                      # a tie is decided: the draw stays
        assert api("award", fid=tie["fid"], winner=tie["a"])[0] == "400 Bad Request"
        snap = json.loads(pestourney.admin_api(svc, "/admin/api/tourneys", "GET", b"")[1])
        mine = next(t for t in snap["tourneys"] if t["compe_id"] == cid)
        assert mine["format"] == "cup" and mine["cup"]["stage"] == "knockout"
        league = json.loads(pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
            {"name": "League", "start": "2020-01-01", "end": "2099-01-01"}).encode())[1])["compe_id"]
        assert next(t for t in pestourney.snapshot(svc) if t["compe_id"] == league)["cup"] is None
        # a deleted player's open tie goes to the opponent, and the two waiting finalists are paired at once
        pestourney.forget(svc, other["a"])
        assert next(f for f in _ties(svc, c) if f["fid"] == other["fid"])["winner"] == other["b"]
        final = next(f for f in _ties(svc, c) if f["round"] == 2)
        assert {final["a"], final["b"]} == {tie["b"], other["b"]}
        (room,) = pesmatch._state(svc)["rooms"].values()
        assert set(room["members"]) == {tie["b"], other["b"]}
        # a league can't be drawn; the format stays once matches are played
        assert pestourney.admin_api(svc, "/admin/api/tourneys/draw", "POST", json.dumps({"compe_id": league}).encode())[0] == "400 Bad Request"
        assert api("save", name="Saturday Cup", format="league")[0] == "400 Bad Request"
        pestourney.delete(svc, cid)
        assert pescup.fixtures(svc, cid) == []
    asyncio.run(go())


@pytest.mark.parametrize("n", [2, 5, 7, 8, 9, 13, 16, 21])
def test_any_number_of_players(svc, n):
    """Every size draws a complete bracket (and groups from 6), and it can be played out to one winner."""
    async def go():
        for groups in ([False, True] if n >= pescup.GROUPS_FROM else [False]):
            c, sess = _cup(svc, n, name=f"Cup {n} {groups}")
            cid = c["compe_id"]
            pescup.draw(svc, c, groups=groups, rng=random.Random(n))
            for _ in range(200):
                opens = [f for f in pescup.fixtures(svc, cid) if f["winner"] is None and f["a"] and f["b"]]
                if not opens:
                    break
                f = opens[0]
                _play(svc, sess, f["a"], f["b"], 1, 0)
            assert pescup.stage(svc, cid) == "done"
            rows = pestourney.standings(svc, cid)
            assert len(rows) == n and [r[0] for r in rows] == list(range(1, n + 1))
            if groups:
                sizes = [len(g["table"]) for g in pescup.snapshot(svc, c)["groups"]]
                assert min(sizes) >= 3 and max(sizes) - min(sizes) <= 1
    asyncio.run(go())


def test_shootout_penalties_are_not_match_goals_and_decide_the_tie(svc):
    async def go():
        c, sess = _cup(svc, 2)
        cid = c["compe_id"]
        pescup.draw(svc, c)
        # knockout rules: extra time and penalties; the games read them with the competition's info
        info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cid})
        reg = unb(info["compe_regulation"])
        assert (reg["extra_time"], reg["pk"]) == ("YES", "YES") and pescmds.validate("CMD_GET_PESLEAGUE_COMPE_INFO", info)
        _search(svc, sess[1], sess[2])
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        home = room["owner"]
        away = 3 - home
        room.update(teams={1: 9, 2: 9}, match_id="pens")
        for s in sess.values():
            pescmds._dispatch(svc, s, "CMD_ADD_SCORE", {"goal_side": "HOME", "gtimer": 500})      # 1-0 in the match
            pescmds._dispatch(svc, s, "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 900})      # 1-1
        for ph in ("EX1ST", "EX2ND", "PK"):
            for s in sess.values():
                pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": ph})
        for t, side in ((60000, "HOME"), (61000, "AWAY"), (62000, "AWAY")):                       # 1-2 on penalties
            for s in sess.values():
                pescmds._dispatch(svc, s, "CMD_ADD_SCORE", {"goal_side": side, "gtimer": t})
        for s in sess.values():
            pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "END"})
        v = room["verdict"]
        assert (v["winner_side"], v["is_pk"]) == ("WINNER_AWAY", "YES")
        assert room["pk"] == {"HOME": 1, "AWAY": 2} and len(room["goals"]) == 2
        f, = _ties(svc, c)
        ag, bg = (1, 1)
        assert f["winner"] == away and (f["a_goals"], f["b_goals"]) == (ag, bg)
        assert {f["a_pk"], f["b_pk"]} == {1, 2} and pescup.stage(svc, cid) == "done"
        assert "on penalties" in pestourney.outline(pestourney.get(svc, cid), svc)
        e = pestourney.entry(svc, cid, away)
        assert (e["win"], e["draw"], e["gf"], e["ga"]) == (1, 0, 1, 1)   # a win; the match's goals stay 1-1
    asyncio.run(go())


def test_every_cup_match_has_extra_time_and_penalties_and_the_owner_has_the_cup_rules(svc):
    async def go():
        # a league that friends opened, then the host makes it a cup: their games still hold the league's rules
        cid = pestourney.save(svc, {"name": "Changed", "start": "2020-01-01", "end": "2099-01-01"})
        _players(svc, *range(1, 7))
        sess = {p: Sess(p) for p in range(1, 7)}
        svc.online.update(sess)
        reg = lambda s: unb(pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cid})["compe_regulation"])  # noqa: E731
        assert set(reg(sess[1])) == {"match_time", "is_equalization"}         # a league is not touched
        for s in sess.values():
            reg(s)
            pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
        pestourney.save(svc, {"compe_id": cid, "format": "cup"})
        c = pestourney.get(svc, cid)
        pescup.draw(svc, c, groups=True, rng=random.Random(3))
        f = _ties(svc, c, "G")[0]
        a, b = f["a"], f["b"]
        assert (reg(sess[b])["extra_time"], reg(sess[b])["pk"]) == ("YES", "YES")     # the groups too; b opened it again
        _search(svc, sess[a], sess[b])                      # a waited longer, but b's game has the cup's rules
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        assert room["owner"] == b and set(room["members"]) == {a, b}
        # a group match level after extra time: the shootout's winner takes the three points, the goals stay level
        room.update(teams={a: 9, b: 9}, match_id="g1", phase={a: "END", b: "END"}, shootout=True,
                    goals={(1, "HOME"), (2, "AWAY")}, pk_goals={(9, "HOME"), (10, "HOME"), (11, "AWAY")})
        pesmatch._verdict(svc, rid, room)
        del pesmatch._state(svc)["rooms"][rid]
        f = _ties(svc, c, "G")[0]
        assert f["winner"] == b and (f["a_goals"], f["b_goals"]) == (1, 1) and {f["a_pk"], f["b_pk"]} == {1, 2}
        table = next(g["table"] for g in pescup.snapshot(svc, c)["groups"] if any(r["pid"] == b for r in g["table"]))
        assert [(r["pid"], r["pts"], r["win"], r["lose"], r["gf"], r["ga"]) for r in table[:1]] == [(b, 3, 1, 0, 1, 1)]
        eb, ea = pestourney.entry(svc, cid, b), pestourney.entry(svc, cid, a)
        assert (eb["points"], eb["win"], eb["draw"]) == (3, 1, 0) and (ea["points"], ea["lose"], ea["draw"]) == (0, 1, 0)
        # level with no shootout (two games without the cup's rules): a draw in the group
        f = next(x for x in _ties(svc, c, "G") if x["winner"] is None)
        _play(svc, sess, f["a"], f["b"], 2, 2)
        f = next(x for x in _ties(svc, c, "G") if x["fid"] == f["fid"])
        assert f["winner"] == 0 and f["a_pk"] is None
    asyncio.run(go())


def _cup3(svc, n, name="Third Cup"):
    c, sess = _cup(svc, n, name=name)
    pestourney.save(svc, {"compe_id": c["compe_id"], "third_place": True})
    return pestourney.get(svc, c["compe_id"]), sess


@pytest.mark.parametrize("final_first", [True, False])
def test_third_place_match(svc, final_first):
    async def go():
        c, sess = _cup3(svc, 4)
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(2))
        (third,) = _ties(svc, c, "T")
        assert third["a"] is None and third["b"] is None and len(_ties(svc, c)) == 3
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        _play(svc, sess, s1["a"], s1["b"], 2, 0)
        assert pescup.to_play(svc, cid, s1["b"]) == [] and not pescup.out(svc, cid, s1["b"])     # waits for the other loser
        _play(svc, sess, s2["a"], s2["b"], 0, 1)
        (third,) = _ties(svc, c, "T")
        assert (third["a"], third["b"]) == (s1["b"], s2["a"])
        assert pescup.to_play(svc, cid, s1["b"]) == [s2["a"]] and pescup.to_play(svc, cid, s1["a"]) == [s2["b"]]
        text = pestourney.outline(pestourney.get(svc, cid), svc)
        assert text.index("FINAL") < text.index("THIRD PLACE") < text.index("SEMI-FINALS")
        # the third-place match is a knockout tie: level without a shootout winner is played again
        _play(svc, sess, s1["b"], s2["a"], 1, 1)
        assert _ties(svc, c, "T")[0]["replays"] == 1
        order = [(s1["a"], s2["b"]), (s1["b"], s2["a"])]
        for a, b in (order if final_first else order[::-1]):
            assert pescup.stage(svc, cid) == "knockout" and pestourney.get(svc, cid)["end"] > time.time()
            _play(svc, sess, a, b, 1, 0)
        assert pescup.stage(svc, cid) == "done" and pestourney.get(svc, cid)["end"] <= time.time()
        assert [r[1] for r in pestourney.standings(svc, cid)] == [s1["a"], s2["b"], s1["b"], s2["a"]]
        snap = next(x for x in pestourney.snapshot(svc) if x["compe_id"] == cid)
        assert snap["third_place"] and [r["name"] for r in snap["cup"]["rounds"]] == ["Semi-finals", "Final", "Third place"]
        assert "took third place" in " ".join(f["text"] for f in __import__("pesserver.events", fromlist=["x"])._st(svc)["feed"])
    asyncio.run(go())


def test_third_place_needs_two_played_semi_finals_and_is_fixed_at_the_draw(svc):
    async def go():
        c, sess = _cup3(svc, 3, "Three")
        pescup.draw(svc, c)
        assert _ties(svc, c, "T") == []                                 # one semi-final is a bye: no match for third
        with pytest.raises(ValueError):
            pestourney.save(svc, {"compe_id": c["compe_id"], "third_place": False})
        pestourney.save(svc, {"compe_id": c["compe_id"], "third_place": True, "name": "Three again"})   # unchanged: fine
        # after groups too; and a league never keeps the setting
        c, sess = _cup3(svc, 6, "Six")
        pescup.draw(svc, c, groups=True, rng=random.Random(5))
        for f in _ties(svc, c, "G"):
            _play(svc, sess, f["a"], f["b"], 1, 0)
        assert len(_ties(svc, c, "T")) == 1 and len(_ties(svc, c)) == 3
        lg = pestourney.save(svc, {"name": "L", "start": "2020-01-01", "end": "2099-01-01", "format": "league", "third_place": True})
        assert "third_place" not in pestourney.get(svc, lg)["rules"]
    asyncio.run(go())


def test_a_player_with_nothing_left_to_play_is_not_left_searching(svc):
    async def go():
        c, sess = _cup(svc, 4)
        cid = c["compe_id"]
        # before the draw a search waits (the draw may pair them)
        _search(svc, sess[1])
        await asyncio.sleep(0)
        assert [p for p, _ in pesmatch._state(svc)["queue"]] == [1] and not sess[1].pushes("CMD_WATCH_MATCHING")
        pesmatch._state(svc)["queue"].clear()
        pescup.draw(svc, c, rng=random.Random(1))
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        # somebody who entered too late: the game's own "entries are closed" message, and no entry
        _players(svc, 9)
        late = Sess(9)
        svc.online[9] = late
        assert pescmds._dispatch(svc, late, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})["result"] == "ERR_COMPE_AFTER_ENTRYTIME"
        assert pestourney.entry(svc, cid, 9) is None and pescup.out(svc, cid, 9)
        _play(svc, sess, s1["a"], s1["b"], 1, 0)
        await asyncio.sleep(0)
        for s in sess.values():
            s.pushes()
        # the winner waits for the other semi-final; the loser's search ends at once
        _search(svc, sess[s1["a"]], sess[s1["b"]])
        await asyncio.sleep(0)
        assert [p for p, _ in pesmatch._state(svc)["queue"]] == [s1["a"]]
        (push,) = sess[s1["b"]].pushes("CMD_WATCH_MATCHING")
        assert push[1]["matching_state"] == pesmatch.CUP_OUT_STATE and not sess[s1["a"]].pushes("CMD_WATCH_MATCHING")
        assert s1["b"] not in pesmatch._state(svc).get("qsince", {})
        # a league and the other queues are not touched
        assert not pescup.out(svc, cid, s1["a"]) and not pescup.out(svc, cid, s2["a"])
        pescmds._dispatch(svc, sess[s1["b"]], "CMD_START_MATCHING", {"matching_type": "OC_QUICK"})
        assert s1["b"] in [p for p, _ in pesmatch._state(svc)["queue"]]
    asyncio.run(go())


def test_a_shootout_shows_in_the_result_lists(svc):
    async def go():
        from pesserver import events, pesresults
        c, sess = _cup(svc, 2)
        cid = c["compe_id"]
        pescup.draw(svc, c)
        _search(svc, sess[1], sess[2])
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        home, away = room["owner"], 3 - room["owner"]
        room.update(teams={1: 9, 2: 9}, match_id="pens", phase={1: "END", 2: "END"}, shootout=True,
                    pk_goals={(1, "HOME"), (2, "AWAY"), (3, "AWAY"), (4, "AWAY")})
        pesmatch._verdict(svc, rid, room)
        mine = pescmds._dispatch(svc, sess[away], "CMD_GET_GAMERESULTS", {"game_res_type": "PESLEAGUE", "pesleague_compe_id": cid})
        e = unb(mine["rlist"][0])
        assert e["pk_flag"] == "YES" and [unb(s)["score_pk"] for s in e["score"]] == [3, 1]       # the player's side first
        e = unb(pescmds._dispatch(svc, sess[home], "CMD_GET_GAMERESULTS", {"game_res_type": "PESLEAGUE", "pesleague_compe_id": cid})["rlist"][0])
        assert [unb(s)["score_pk"] for s in e["score"]] == [1, 3]
        # Last 20 results: from the saved match (events), which keeps the shootout too
        events._st(svc)
        db = svc.accounts.db
        with svc.accounts.lock, db:
            db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name, home_score, "
                       "away_score, reason, mode, home_pk, away_pk) VALUES (1, 1, 2, 1, 2, 'a', 'b', 0, 0, '', 'Quick Match', 4, 5)")
            db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name, home_score, "
                       "away_score, reason, mode) VALUES (1, 1, 2, 1, 2, 'a', 'b', 2, 0, '', 'Quick Match')")
        plain, pens = [unb(x) for x in pesresults.history(svc, 1)]
        assert plain["pk_flag"] == "NO" and [unb(s)["score_pk"] for s in plain["score"]] == [0, 0]
        assert pens["pk_flag"] == "YES" and [unb(s)["score_pk"] for s in pens["score"]] == [4, 5]
        rows, _ = events.results_page(svc)
        assert (rows[1]["home_pk"], rows[1]["away_pk"]) == (4, 5) and rows[0]["home_pk"] is None
    asyncio.run(go())


def test_cup_news_goes_to_discord_with_the_result_notices(svc, monkeypatch):
    async def go():
        from pesserver import pesdiscord
        sent = []
        monkeypatch.setattr(pesdiscord, "post", lambda s, text: sent.append(text))
        pesdiscord.save(svc, url="https://discord.com/api/webhooks/123456789012345678/" + "a" * 60, results=True)
        c, sess = _cup3(svc, 4, "Discord Cup")
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(4))
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        _play(svc, sess, s1["a"], s1["b"], 1, 0)
        pescup.walkover(svc, pestourney.get(svc, cid), s2["fid"], s2["a"])
        _play(svc, sess, s1["a"], s2["a"], 2, 1)
        _play(svc, sess, s1["b"], s2["b"], 0, 0, pk=(4, 3))
        cup = [t for t in sent if t.startswith("🏆 ")]
        assert len(cup) == 5 and "the draw is made (4 players, knockout)" in cup[0]
        assert "final: " in cup[1] and "third place: " in cup[2]
        assert cup[3] == f"🏆 Player{s1['a']} won Discord Cup" and cup[4] == f"🏆 Player{s1['b']} took third place in Discord Cup"
        assert not any("awarded" in t for t in sent)                    # the host's own call is not news
        sent.clear()
        pesdiscord.save(svc, results=False)
        c, sess = _cup(svc, 2, "Quiet Cup")
        pescup.draw(svc, c)
        assert sent == []
    asyncio.run(go())


@pytest.mark.parametrize("seed", range(int(os.environ.get("CUP_SEEDS", 12))))
def test_random_cups_always_finish_with_one_winner(svc, seed):
    """Whole cups with random sizes, formats, results, shootouts, replays, awarded ties and deleted players."""
    async def go():
        rng = random.Random(1000 + seed)
        n = rng.randint(2, 26)
        groups = n >= 3 and rng.random() < 0.6
        third = rng.random() < 0.5
        c, sess = (_cup3 if third else _cup)(svc, n, name=f"Random {seed}")
        cid = c["compe_id"]
        through = None
        if groups:                                                     # random group rules (groups of 3-6, ...)
            rules = {"groups": rng.choice(pescup.GROUP_SIZES), "qualify": rng.choice((1, 2)),
                     "fill": rng.random() < 0.5, "legs": rng.choice((1, 2))}
            try:
                through = pescup.plan(n, rules["groups"], rules["qualify"], rules["fill"], rules["legs"])["through"]
            except ValueError:
                groups = False
            else:
                pestourney.save(svc, {"compe_id": cid, **rules})
                c = pestourney.get(svc, cid)
        pescup.draw(svc, c, groups=groups, rng=rng)
        gone, steps = set(), 0
        while pescup.stage(svc, cid) != "done":
            steps += 1
            assert steps < 2000
            c = pestourney.get(svc, cid)
            fx = pescup.fixtures(svc, cid)
            opens = [f for f in fx if f["winner"] is None and f["a"] is not None and f["b"] is not None]
            assert opens, "an unfinished cup always has a tie that can be played"
            if pescup.stage(svc, cid) == "knockout":                   # nobody has two knockout ties at once
                busy = [p for f in opens for p in (f["a"], f["b"])]
                assert len(busy) == len(set(busy))
            # two players without a tie against each other are never paired
            x, y = rng.sample(range(1, n + 1), 2)
            assert pescup.can_pair(svc, cid, x, y) == any({f["a"], f["b"]} == {x, y} for f in opens)
            assert len(pestourney.outline(c, svc)) <= 2000 and pescup.snapshot(svc, c)["stage"] != "entry"
            f = rng.choice(opens)
            roll = rng.random()
            assert all(f["a"] > 0 and f["b"] > 0 for f in opens)       # a deleted player never holds a tie up
            if roll < 0.08:
                pescup.walkover(svc, c, f["fid"], rng.choice((f["a"], f["b"])))
            elif roll < 0.14 and len(gone) < n - 2:                    # the host deletes a player, anyone still in the draw
                pid = rng.choice(sorted({p for g in fx for p in (g["a"], g["b"]) if p and p > 0}))
                gone.add(pid)
                pestourney.forget(svc, pid)
            else:
                ag, bg = rng.randint(0, 3), rng.randint(0, 3)
                pk = None
                if ag == bg and f["stage"] != "G" and rng.random() < 0.7:
                    pk = rng.choice([(5, 4), (3, 4), (2, 0)])
                _play(svc, sess, f["a"], f["b"], ag, bg, pk=pk)
        fx = pescup.fixtures(svc, cid)
        ko = [f for f in fx if f["stage"] == "K"]
        final = max(ko, key=lambda f: f["round"])
        qualified = {p for f in ko for p in (f["a"], f["b"]) if p is not None}
        if through is not None:                                        # as planned (fewer when players were deleted)
            assert len(qualified) <= through and (gone or len(qualified) == through)
        size = 2
        while size < len(qualified):
            size *= 2
        assert len(ko) == size - 1 and all(f["winner"] for f in ko)
        assert len([f for f in fx if f["stage"] == "T"]) == (1 if third and len(qualified) >= 4 else 0)
        assert not any(-p in {q for f in fx for q in (f["a"], f["b"])} for p in range(1, n + 1) if p not in gone)
        for f in ko:                                                    # every winner went on to the next round
            if f is not final:
                nxt = next(g for g in ko if g["round"] == f["round"] + 1 and g["slot"] == f["slot"] // 2)
                assert f["winner"] in (nxt["a"], nxt["b"])
        rows = pestourney.standings(svc, cid)
        loser = final["b"] if final["winner"] == final["a"] else final["a"]
        top = [p for p in (final["winner"], loser) if p > 0]            # (deleted players are not in the standings)
        for t in (f for f in fx if f["stage"] == "T"):
            top += [p for p in (t["winner"], t["a"] if t["winner"] == t["b"] else t["b"]) if p > 0]
        assert [r[0] for r in rows] == list(range(1, n - len(gone) + 1)) and [r[1] for r in rows[:len(top)]] == top
        assert pestourney.get(svc, cid)["end"] <= time.time()
        assert all(pescup.out(svc, cid, p) for p in range(1, n + 1))
        champ = f"Player{final['winner']}" if final["winner"] > 0 else "a deleted player"
        assert pestourney.outline(pestourney.get(svc, cid), svc).startswith(champ + " won")
    asyncio.run(go())


def test_leaving_a_cup_match_early_gives_the_tie_to_the_opponent(svc):
    async def go():
        c, sess = _cup(svc, 4)
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(7))
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        _search(svc, sess[s1["a"]], sess[s1["b"]])
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        room.update(teams={p: 9 for p in room["members"]}, match_id="left", shootout=True, pk_goals={(1, "HOME")})
        assert pesmatch.forfeit(svc, rid, s1["a"])                      # even in the middle of a shootout
        f = next(x for x in _ties(svc, c) if x["fid"] == s1["fid"])
        assert f["winner"] == s1["b"] and {f["a_goals"], f["b_goals"]} == {0, 3} and f["a_pk"] is None
        final = max(_ties(svc, c), key=lambda x: x["round"])
        assert s1["b"] in (final["a"], final["b"]) and pescup.out(svc, cid, s1["a"])
    asyncio.run(go())


def test_a_deleted_player_never_holds_the_cup_up(svc):
    async def go():
        # the finalist who is waiting is deleted: the other semi-final's winner wins the cup
        c, sess = _cup3(svc, 4, "Ghost final")
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(2))
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        _play(svc, sess, s1["a"], s1["b"], 1, 0)
        pestourney.forget(svc, s1["a"])
        final = max(_ties(svc, c), key=lambda f: f["round"])
        assert -s1["a"] in (final["a"], final["b"]) and pescup.stage(svc, cid) == "knockout"
        _play(svc, sess, s2["a"], s2["b"], 2, 0)                        # the final goes to its winner at once
        final = max(_ties(svc, c), key=lambda f: f["round"])
        assert final["winner"] == s2["a"] and final["how"] == "walkover"
        (third,) = _ties(svc, c, "T")
        assert {third["a"], third["b"]} == {s1["b"], s2["b"]} and third["winner"] is None    # still to play
        assert "a deleted player" in pestourney.outline(pestourney.get(svc, cid), svc)
        snap = pescup.snapshot(svc, pestourney.get(svc, cid))
        assert any(t["a"]["name"] == "A deleted player" or t["b"]["name"] == "A deleted player" for r in snap["rounds"] for t in r["ties"])
        _play(svc, sess, s1["b"], s2["b"], 1, 0)
        assert pescup.stage(svc, cid) == "done" and pestourney.standings(svc, cid)[0][1] == s2["a"]
        pescup.restore(svc, s1["a"])                                    # put back (pesundo): the name is back in the draw
        assert s1["a"] in [p for f in _ties(svc, c) for p in (f["a"], f["b"])]
        # deleted with the semi-final still open: the opponent goes through, the third-place match is settled too
        c, sess = _cup3(svc, 4, "Ghost semi")
        cid = c["compe_id"]
        pescup.draw(svc, c, rng=random.Random(2))
        s1, s2 = [f for f in _ties(svc, c) if f["round"] == 1]
        pestourney.forget(svc, s1["a"])
        _play(svc, sess, s2["a"], s2["b"], 0, 1)
        (third,) = _ties(svc, c, "T")
        assert third["winner"] == s2["a"] and third["how"] == "walkover"
        _play(svc, sess, s1["b"], s2["b"], 3, 1)
        assert pescup.stage(svc, cid) == "done"
        # a group: a deleted player does not go through, and a search that became pointless is ended
        c, sess = _cup(svc, 6, "Ghost group")
        cid = c["compe_id"]
        pescup.draw(svc, c, groups=True, rng=random.Random(3))
        g0 = [f for f in _ties(svc, c, "G") if f["grp"] == 0]
        members = sorted({p for f in g0 for p in (f["a"], f["b"])})
        best, mid, last = members
        for f in g0:
            hi, lo = sorted((f["a"], f["b"]))
            _play(svc, sess, hi, lo, 0, 2)                              # the lower number wins: best > mid > last
        await asyncio.sleep(0)
        _search(svc, sess[last])                                        # third in the group, waits for the other group
        await asyncio.sleep(0)
        sess[last].pushes()
        assert [p for p, _ in pesmatch._state(svc)["queue"]] == [last]
        pestourney.forget(svc, best)                                    # now mid and last go through
        for f in [f for f in _ties(svc, c, "G") if f["winner"] is None]:
            pescup.walkover(svc, pestourney.get(svc, cid), f["fid"], f["a"])
        ko = {p for f in _ties(svc, c) for p in (f["a"], f["b"]) if p}
        assert {mid, last} <= ko and -best not in ko and best not in ko
        # ... and the other way: somebody searching who is then knocked out by the host's award
        tie = next(f for f in _ties(svc, c) if f["round"] == 1 and last in (f["a"], f["b"]))
        pescup.walkover(svc, pestourney.get(svc, cid), tie["fid"], tie["a"] if tie["b"] == last else tie["b"])
        await asyncio.sleep(0)
        assert last not in [p for p, _ in pesmatch._state(svc)["queue"]]
        assert [b["matching_state"] for _, b in sess[last].pushes("CMD_WATCH_MATCHING")] == [pesmatch.CUP_OUT_STATE]
    asyncio.run(go())


def test_bad_admin_calls_get_an_answer_and_a_late_goal_report_is_not_a_penalty(svc):
    async def go():
        c, sess = _cup(svc, 2)
        cid = c["compe_id"]
        api = lambda route, d: pestourney.admin_api(svc, "/admin/api/tourneys/" + route, "POST", json.dumps(d).encode())[0]  # noqa: E731
        assert api("draw", {"compe_id": 1e400}) == "404 Not Found" and api("award", {"compe_id": cid, "fid": 1e30, "winner": 1}) == "400 Bad Request"
        assert api("save", {"compe_id": cid, "third_place": "no"}) == "400 Bad Request"
        pescup.draw(svc, c)
        _search(svc, sess[1], sess[2])
        (rid, room), = pesmatch._state(svc)["rooms"].items()
        room.update(teams={1: 9, 2: 9}, match_id="late")
        pescmds._dispatch(svc, sess[1], "CMD_ADD_SCORE", {"goal_side": "HOME", "gtimer": 500})
        pescmds._dispatch(svc, sess[2], "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 26000})
        for s in sess.values():
            pescmds._dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "PK", "gtimer": 27076})
        pescmds._dispatch(svc, sess[1], "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 26000})     # the same goal, late
        pescmds._dispatch(svc, sess[1], "CMD_ADD_SCORE", {"goal_side": "HOME", "gtimer": 65253})     # a penalty
        assert len(room["goals"]) == 2 and room["pk_goals"] == {(65253, "HOME")}
    asyncio.run(go())


def test_an_awarded_tie_counts_in_the_table_the_game_shows(svc):
    async def go():
        c, sess = _cup(svc, 2)
        cid = c["compe_id"]
        pescup.draw(svc, c)
        (f,) = _ties(svc, c)
        pescup.walkover(svc, c, f["fid"], f["b"])
        w, l = pestourney.entry(svc, cid, f["b"]), pestourney.entry(svc, cid, f["a"])
        assert (w["points"], w["win"], w["gf"], w["ga"]) == (3, 1, 3, 0) and (l["points"], l["lose"], l["gf"], l["ga"]) == (0, 1, 0, 3)
    asyncio.run(go())
