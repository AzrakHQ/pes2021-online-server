"""Cup formats: group size, how many go through (and the best third-placed), playing twice, one team per friend,
the ready-made cups and the host's own."""
import asyncio, json, random

import pytest

from pesserver import adminextra, pescmds, pescup, pestourney
from test_pesrooms import svc, Sess   # noqa: F401  (fixture)
from test_tourney import _players
from test_cup import _play, _ties


def _api(svc, route, **body):
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/" + route, "POST", json.dumps(body).encode())
    return st, json.loads(out)


def _new(svc, **rules):
    st, out = _api(svc, "save", name="Format Cup", start="2020-01-01", end="2099-01-01", format="cup", **rules)
    assert st == "200 OK", out
    return out["compe_id"]


def _enter(svc, cid, n, team=lambda p: 9):
    _players(svc, *range(1, n + 1))
    sess = {p: Sess(p) for p in range(1, n + 1)}
    svc.online.update(sess)
    for p, s in sess.items():
        assert pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": team(p)})["result"] == "NOERR"
    return sess


@pytest.mark.parametrize("n,size,qualify,fill,want", [
    (8, 4, 2, False, (2, [4, 4], 0, 4)),
    (12, 4, 2, True, (3, [4, 4, 4], 2, 8)),        # 6 group places + the 2 best third-placed: a knockout of 8
    (24, 4, 2, True, (6, [4] * 6, 4, 16)),         # the European Championship: 12 + 4
    (10, 4, 2, False, (3, [4, 3, 3], 0, 6)),
    (5, 4, 2, False, (1, [5], 0, 2)),              # two groups of 3 and 2 would leave nobody out of the smaller
    (4, 4, 2, False, (1, [4], 0, 2)),
    (9, 3, 1, True, (3, [3, 3, 3], 1, 4)),         # three winners and the best runner-up
    (3, 4, 1, True, (1, [3], 1, 2)),
    (20, 4, 2, True, (5, [4] * 5, 5, 15)),         # 10 + 5 (one bye): there are only five third-placed
])
def test_plan(n, size, qualify, fill, want):
    p = pescup.plan(n, size, qualify, fill)
    assert (p["groups"], p["sizes"], p["extra"], p["through"]) == want


@pytest.mark.parametrize("n,size,qualify,fill", [(2, 4, 2, False), (3, 4, 1, False), (1, 3, 1, True)])
def test_plan_too_few(n, size, qualify, fill):
    with pytest.raises(ValueError):
        pescup.plan(n, size, qualify, fill)


def test_saving_group_rules(svc):
    cid = _new(svc, groups=3, qualify=1, fill=True, legs=2, third_place=True)
    c = pestourney.get(svc, cid)
    assert pescup.group_rules(c) == (3, 1, True, 2) and pescup.third_place(c)
    for bad in ({"groups": 7}, {"groups": True}, {"qualify": 3}, {"fill": "yes"}, {"legs": 3}, {"one_team": 1},
                {"groups": 2 ** 70}):
        assert _api(svc, "save", compe_id=cid, **bad)[0] == "400 Bad Request", bad
    assert _api(svc, "save", compe_id=cid, groups=0)[0] == "200 OK"           # no groups: the rest goes too
    assert pestourney.get(svc, cid)["rules"] == {"format": "cup", "third_place": True}
    assert _api(svc, "save", compe_id=cid, groups=4, qualify=2)[0] == "200 OK"
    assert pestourney.get(svc, cid)["rules"] == {"format": "cup", "third_place": True, "groups": 4}
    _enter(svc, cid, 6)
    snap = next(t for t in pestourney.snapshot(svc) if t["compe_id"] == cid)
    assert snap["plan"]["groups"] == 2 and snap["plan"]["through"] == 4 and snap["groups"] == 4
    assert _api(svc, "draw", compe_id=cid)[0] == "200 OK"                     # the rules say groups
    assert len(_ties(svc, pestourney.get(svc, cid), "G")) == 6
    st, out = _api(svc, "save", compe_id=cid, groups=5)
    assert st == "400 Bad Request" and "group stage" in out["error"]
    assert _api(svc, "save", compe_id=cid, groups=4, name="Renamed")[0] == "200 OK"   # the same: fine
    lg = json.loads(pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"name": "League", "start": "2020-01-01", "end": "2099-01-01", "groups": 4}).encode())[1])["compe_id"]
    assert "groups" not in pestourney.get(svc, lg)["rules"]                   # a league has no groups


def test_groups_of_three_winners_and_best_runner_up(svc):
    async def go():
        cid = _new(svc, groups=3, qualify=1, fill=True)
        sess = _enter(svc, cid, 9)
        c = pestourney.get(svc, cid)
        pescup.draw(svc, c, rng=random.Random(4))
        assert pescup.stage(svc, cid) == "groups"
        for f in _ties(svc, c, "G"):                       # the lower number always wins, by its group's order
            g = sorted(x for t in _ties(svc, c, "G") if t["grp"] == f["grp"] for x in (t["a"], t["b"]))
            lo, hi = min(f["a"], f["b"]), max(f["a"], f["b"])
            _play(svc, sess, lo, hi, 1 if lo == g[0] else 2, 0)  # the group's top player wins 1-0, others 2-0
        tables = pescup._tables(pescup.fixtures(svc, cid))
        winners = [t[0]["pid"] for _, t in tables]
        seconds = sorted((t[1] for _, t in tables), key=pescup._across)
        ko = _ties(svc, c)
        firsts = [p for f in ko if f["round"] == 1 for p in (f["a"], f["b"])]
        assert len(ko) == 3 and set(firsts) == set(winners) | {seconds[0]["pid"]}
        assert "The winner of each group goes to the knockout, with the best second-placed." not in pestourney.outline(
            pestourney.get(svc, cid), svc)                 # (the knockout is drawn: that line is gone)
    asyncio.run(go())


def test_outline_says_who_goes_through(svc):
    cid = _new(svc, groups=4, qualify=2, fill=True)
    _enter(svc, cid, 12)
    pescup.draw(svc, pestourney.get(svc, cid), rng=random.Random(1))
    text = pestourney.outline(pestourney.get(svc, cid), svc)
    assert "The top 2 of each group go to the knockout, with the best 2 third-placed." in text
    snap = pescup.snapshot(svc, pestourney.get(svc, cid))
    assert snap["qualify"] == 2 and snap["extra"] == 2 and len(snap["groups"]) == 3


def test_playing_each_other_twice(svc):
    async def go():
        cid = _new(svc, groups=4, legs=2)
        sess = _enter(svc, cid, 4)
        c = pestourney.get(svc, cid)
        pescup.draw(svc, c, rng=random.Random(2))
        g = _ties(svc, c, "G")
        assert len(g) == 12 and len({(f["a"], f["b"]) for f in g}) == 12          # each way round once
        _play(svc, sess, 1, 2, 1, 0)
        assert pescup.can_pair(svc, cid, 1, 2)                                     # the second match is still to play
        _play(svc, sess, 1, 2, 0, 3)
        assert not pescup.can_pair(svc, cid, 1, 2)
        row = next(r for _, t in pescup._tables(pescup.fixtures(svc, cid)) for r in t if r["pid"] == 2)
        assert (row["played"], row["win"], row["lose"], row["gf"], row["ga"]) == (2, 1, 1, 3, 1)
    asyncio.run(go())


def test_one_team_per_friend(svc):
    cid = _new(svc)
    assert _api(svc, "save", compe_id=cid, one_team=True, teams=[8, 9, 45])[0] == "200 OK"
    _players(svc, 1, 2, 3, 4)
    s = {p: Sess(p) for p in (1, 2, 3, 4)}
    join = lambda p, t: pescmds._dispatch(svc, s[p], "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": t})["result"]   # noqa: E731
    teams = lambda p: pescmds._dispatch(svc, s[p], "CMD_GET_PESLEAGUE_COMPE_TEAM_LIST", {"compe_id": cid})["team_list"]     # noqa: E731
    info = pescmds._dispatch(svc, s[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cid})
    assert info["entry_user_max"] == 3
    assert teams(1) == [8, 9, 45] and join(1, 9) == "NOERR"
    assert teams(2) == [8, 45] and teams(1) == [8, 9, 45]                  # their own team stays in their list
    assert join(2, 9) == "ERR_COMPE_FULL" and pestourney.entry(svc, cid, 2) is None
    assert join(1, 9) == "NOERR"                                            # after a match: the same team again
    assert join(2, 45) == "NOERR" and join(3, 8) == "NOERR"
    assert teams(4) == [8, 9, 45]                                           # all taken: the list stays whole ...
    assert join(4, 8) == "ERR_COMPE_FULL" and join(4, 77) == "ERR_COMPE_FULL"   # ... and JOIN says it's full
    assert join(1, 8) == "NOERR" and pestourney.entry(svc, cid, 1)["team_id"] == 9    # in already: keeps theirs
    info = pescmds._dispatch(svc, s[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cid})
    assert info["entry_user_max"] == 3 and info["entry_user_limit"] == "YES"
    assert _api(svc, "save", compe_id=cid, one_team=False)[0] == "200 OK"
    assert join(4, 8) == "NOERR"                                            # off: anyone with any team


def test_ready_made_cups(svc):
    st, body, _ = pestourney.admin_api(svc, "/admin/api/tourneys", "GET", b"")
    pre = json.loads(body)["presets"]
    names = adminextra.teams()["names"]
    national = {t for r in adminextra.teams()["national"] for t in r["teams"]}
    clubs = {t for r in adminextra.teams()["clubs"] for t in r["teams"]}
    assert [p["key"] for p in pre] == ["world", "euro", "copa", "africa", "asia", "ucl", "uel", "libertadores", "afccl"]
    assert [len(p["teams"]) for p in pre] == [31, 24, 15, 14, 16, 34, 45, 24, 32]
    for p in pre:
        assert set(p["teams"]) <= (clubs if p["legs"] == 2 else national), p["key"]
        assert len(set(p["teams"])) == len(p["teams"]) and not p["own"]
        assert all(str(t) in names for t in p["teams"])
        # each one can be saved as a competition as it is
        cid = _api(svc, "save", start="2020-01-01", end="2099-01-01",
                   **{k: v for k, v in p.items() if k in pestourney.PRESET_KEYS})[1]["compe_id"]
        c = pestourney.get(svc, cid)
        assert pescup.is_cup(c) and pestourney.one_team(c) and pescup.group_rules(c)[0] == 4
    assert {names[str(t)] for t in pre[1]["teams"]} >= {"Germany", "Spain", "England", "Albania", "Georgia", "Serbia"}
    assert {names[str(t)] for t in pre[5]["teams"]} >= {"FC Barcelona", "Juventus", "PSG"}


def test_own_ready_made_cups(svc):
    st, out = _api(svc, "preset", name="Office Cup", format="cup", teams=[9, 8], third_place=True, groups=3,
                   qualify=1, fill=True, one_team=True, match_minutes=6)
    assert st == "200 OK" and out["key"] == "own-1"
    assert _api(svc, "preset", name="office cup", format="cup", teams=[9], groups=0)[1]["key"] == "own-1"  # replaced
    assert _api(svc, "preset", name="Sunday League", teams=[9, 45])[1]["key"] == "own-2"
    own = [p for p in pestourney.presets(svc) if p["own"]]
    assert [(p["key"], p["name"]) for p in own] == [("own-1", "office cup"), ("own-2", "Sunday League")]
    assert own[0]["groups"] == 0 and own[0]["third_place"] is False and own[1]["format"] == "league"
    for bad in ({"name": ""}, {"name": "X", "teams": []}, {"name": "X", "teams": ["a"]},
                {"name": "X", "teams": [9], "format": "swiss"}, {"name": "X", "teams": [9], "match_minutes": 99},
                {"name": "X", "teams": [9], "format": "cup", "groups": 9}):
        assert _api(svc, "preset", **bad)[0] == "400 Bad Request", bad
    assert _api(svc, "preset/delete", key="own-1")[0] == "200 OK"
    assert _api(svc, "preset/delete", key="own-1")[0] == "404 Not Found"
    assert _api(svc, "preset/delete", key="world")[0] == "404 Not Found"     # the ready-made ones stay
    for i in range(pestourney.MAX_PRESETS - 1):
        assert _api(svc, "preset", name=f"Cup {i}", teams=[9])[0] == "200 OK"
    assert _api(svc, "preset", name="One too many", teams=[9])[0] == "400 Bad Request"
    assert _api(svc, "preset", name="Cup 3", teams=[8])[0] == "200 OK"        # replacing one is still fine


def test_one_team_turned_on_late_never_locks_anyone_out(svc):
    cid = _new(svc)
    sess = _enter(svc, cid, 3)                                   # all three with team 9
    assert _api(svc, "save", compe_id=cid, one_team=True, teams=[9, 9, 8])[0] == "200 OK"
    assert pestourney.get(svc, cid)["teams"] == [9, 8]           # (duplicates dropped)
    for p in (1, 2, 3):
        assert pescmds._dispatch(svc, sess[p], "CMD_GET_PESLEAGUE_COMPE_TEAM_LIST", {"compe_id": cid})["team_list"] == [9, 8]
        assert pescmds._dispatch(svc, sess[p], "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})["result"] == "NOERR"
    info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": cid})
    assert info["entry_user_max"] == 3 and info["entry_user_num"] == 3          # never fewer places than friends in
    _players(svc, 4)
    late = Sess(4)
    assert pescmds._dispatch(svc, late, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 8})["result"] == "NOERR"
    pescup.draw(svc, pestourney.get(svc, cid), rng=random.Random(1))
    _players(svc, 5)
    # after the draw a newcomer hears that entries are closed, even with a taken team
    assert pescmds._dispatch(svc, Sess(5), "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})["result"] == "ERR_COMPE_AFTER_ENTRYTIME"


def test_deleting_a_player_who_would_finish_the_groups_does_not_seed_them(svc):
    async def go():
        cid = _new(svc, groups=4)
        sess = _enter(svc, cid, 4)
        c = pestourney.get(svc, cid)
        pescup.draw(svc, c, rng=random.Random(2))
        g = _ties(svc, c, "G")
        one = [f for f in g if 1 in (f["a"], f["b"])]
        for f in one[:2]:                                        # player 1 wins two, one left to play
            _play(svc, sess, 1, f["b"] if f["a"] == 1 else f["a"], 2, 0)
        for f in g:
            if 1 not in (f["a"], f["b"]):
                _play(svc, sess, f["a"], f["b"], 1, 0)
        pestourney.forget(svc, 1)
        ko = _ties(svc, pestourney.get(svc, cid))
        assert len(ko) == 1 and all(p > 0 for p in (ko[0]["a"], ko[0]["b"])) and ko[0]["winner"] is None
        assert pescup.stage(svc, cid) == "knockout"
    asyncio.run(go())
