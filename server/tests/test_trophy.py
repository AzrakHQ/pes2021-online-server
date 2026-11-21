"""The trophy ceremony of a cup final (mod "trophy1", httpgate /mod/final) and a competition's look (compe_db_id)."""
import asyncio, json, random

from pesserver import httpgate, pescmds, pescup, pesmatch, pestourney
from test_pesrooms import svc, Sess   # noqa: F401  (fixture)
from test_tourney import _players
from test_cup import _play, _search


def _cup(svc, n, look=None, mods=("math1", "trophy1")):
    body = {"name": "Final Cup", "start": "2020-01-01", "end": "2099-01-01", "format": "cup"}
    if look is not None:
        body["look"] = look
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(body).encode())
    assert st == "200 OK", out
    cid = json.loads(out)["compe_id"]
    _players(svc, *range(1, n + 1))
    sess = {p: Sess(p) for p in range(1, n + 1)}
    for p, s in sess.items():
        s.mod_id, s.mod_features = f"{p:032x}", frozenset(mods)
    svc.online.update(sess)
    for s in sess.values():
        pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
        pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cid})
    return pestourney.get(svc, cid), sess


def _room(svc, a):
    rid, room = pesmatch._room_of(svc, a)
    return rid, room


def test_look_is_sent_as_the_original_competition(svc):
    c, sess = _cup(svc, 2, look=33)
    info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": c["compe_id"]})
    assert info["compe_db_id"] == 33
    c2, _ = _cup(svc, 2)
    info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": c2["compe_id"]})
    assert info["compe_db_id"] == pestourney.CUP_LOOK  # no look chosen: a cup is the International Cup
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST",
                                      json.dumps({"compe_id": c2["compe_id"], "look": 0}).encode())
    info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": c2["compe_id"]})
    assert st == "200 OK" and info["compe_db_id"] == 0       # plain, chosen
    lg = json.loads(pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"name": "League", "start": "2020-01-01", "end": "2099-01-01"}).encode())[1])["compe_id"]
    info = pescmds._dispatch(svc, sess[1], "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": lg})
    assert info["compe_db_id"] == 0                          # a league: plain (no longer its own number)
    for bad in (34567, True, "33", -1):
        st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST",
                                          json.dumps({"compe_id": c["compe_id"], "look": bad}).encode())
        assert st == "400 Bad Request", bad
    snap = next(t for t in pestourney.snapshot(svc) if t["compe_id"] == c["compe_id"])
    assert snap["look"] == 33


def test_only_the_final_gets_the_ceremony(svc):
    async def go():
        c, sess = _cup(svc, 4, look=33)
        pescup.draw(svc, c, rng=random.Random(1))
        semis = [f for f in pescup.fixtures(svc, c["compe_id"]) if f["round"] == 1]
        a, b = semis[0]["a"], semis[0]["b"]
        _search(svc, sess[a], sess[b])
        rid, room = _room(svc, a)
        assert pestourney.ceremony(svc, room) == 0                     # a semi-final: none
        assert httpgate.mod_final(svc, sess[a].mod_id) == 0
        del pesmatch._state(svc)["rooms"][rid]
        for f in semis:
            _play(svc, sess, f["a"], f["b"], 1, 0)
        fin = next(f for f in pescup.fixtures(svc, c["compe_id"]) if f["round"] == 2)
        _search(svc, sess[fin["a"]], sess[fin["b"]])
        rid, room = _room(svc, fin["a"])
        assert pestourney.ceremony(svc, room) == pestourney.END_CUP_CMN
        assert httpgate.mod_final(svc, sess[fin["a"]].mod_id) == pestourney.END_CUP_CMN
        assert httpgate.mod_final(svc, sess[semis[0]["a"] if semis[0]["a"] not in (fin["a"], fin["b"]) else semis[0]["b"]].mod_id) == 0
        assert httpgate.mod_final(svc, "") == 0 and httpgate.mod_final(svc, "nobody") == 0
        # an older mod on either side: no ceremony (one game would play a minute the other skips)
        sess[fin["b"]].mod_features = frozenset({"math1"})
        assert httpgate.mod_final(svc, sess[fin["a"]].mod_id) == 0
    asyncio.run(go())


def test_ceremony_start_counts_as_the_final_whistle(svc):
    async def go():
        c, sess = _cup(svc, 2)
        pescup.draw(svc, c, rng=random.Random(1))
        _search(svc, sess[1], sess[2])
        rid, room = _room(svc, 1)
        assert httpgate.mod_final(svc, sess[1].mod_id) == pestourney.END_CUP_CMN
        room.update(phase={1: "PK", 2: "PK"}, goals=set(), teams={1: 9, 2: 9}, match_id="m1",
                    shootout=1000, pk={"HOME": 4, "AWAY": 2})
        for s in sess.values():
            s.pushes()
        assert httpgate.mod_final(svc, sess[1].mod_id, over=True) == pestourney.END_CUP_CMN
        assert room["phase"][1] == "END" and "pushed_at_end" not in room      # one game so far
        httpgate.mod_final(svc, sess[2].mod_id, over=True)
        await asyncio.sleep(0.05)
        assert room.get("pushed_at_end")                                      # both: settled, verdict pushed
        assert all(s.pushes("CMD_WATCH_GAMEADDPOINT") for s in sess.values())
        httpgate.mod_final(svc, sess[2].mod_id, over=True)                    # again: nothing changes
        assert pescup.stage(svc, c["compe_id"]) == "done"
    asyncio.run(go())


def test_mod_final_over_http(svc):
    c, sess = _cup(svc, 2, look=33)
    pescup.draw(svc, c, rng=random.Random(1))
    _search(svc, sess[1], sess[2])

    class W:
        def get_extra_info(self, k):
            return ("127.0.0.1", 5) if k == "peername" else None
    st, body, ctype, extra = httpgate._mod(svc, "/mod/final", {}, W(), "/mod/final?id=" + sess[2].mod_id)
    assert st == "200 OK" and body == b"group=0x70007\n"
    st, body, *_ = httpgate._mod(svc, "/mod/final", {}, W(), "/mod/final?id=zzz")
    assert body == b"group=0x0\n"


def test_a_plain_cup_has_no_ceremony(svc):
    c, sess = _cup(svc, 2, look=0)
    pescup.draw(svc, c, rng=random.Random(1))
    _search(svc, sess[1], sess[2])
    assert httpgate.mod_final(svc, sess[1].mod_id) == 0


def test_finalists_with_older_rooms_still_get_it_and_over_needs_a_final(svc):
    async def go():
        c, sess = _cup(svc, 4, look=33)
        pescup.draw(svc, c, rng=random.Random(1))
        semis = [f for f in pescup.fixtures(svc, c["compe_id"]) if f["round"] == 1]
        for f in semis:                                  # semi-final rooms stay in memory, as on the server
            _search(svc, sess[f["a"]], sess[f["b"]])
            rid, room = _room(svc, f["a"])
            assert httpgate.mod_final(svc, sess[f["a"]].mod_id, over=True) == 0     # not a final: nothing
            assert (room.get("phase") or {}).get(f["a"]) != "END"
            goals = {(1, "HOME" if room["owner"] == f["a"] else "AWAY")}
            room.update(goals=goals, phase={f["a"]: "END", f["b"]: "END"}, teams={f["a"]: 9, f["b"]: 9}, match_id=f"m{rid}")
            assert pesmatch._verdict(svc, rid, room) is not None
        fin = next(f for f in pescup.fixtures(svc, c["compe_id"]) if f["round"] == 2)
        _search(svc, sess[fin["a"]], sess[fin["b"]])
        assert httpgate.mod_final(svc, sess[fin["a"]].mod_id) == pestourney.END_CUP_CMN
        assert httpgate.mod_final(svc, sess[fin["b"]].mod_id) == pestourney.END_CUP_CMN
    asyncio.run(go())


def test_over_in_a_league_match_changes_nothing(svc):
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"name": "League", "start": "2020-01-01", "end": "2099-01-01"}).encode())
    cid = json.loads(out)["compe_id"]
    _players(svc, 1, 2)
    sess = {p: Sess(p) for p in (1, 2)}
    for p, s in sess.items():
        s.mod_id, s.mod_features = f"{p:032x}", frozenset({"trophy1"})
    svc.online.update(sess)
    for s in sess.values():
        pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
        pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cid})
    _search(svc, sess[1], sess[2])
    rid, room = _room(svc, 1)
    assert httpgate.mod_final(svc, sess[1].mod_id, over=True) == 0
    assert (room.get("phase") or {}).get(1) != "END"


def test_an_old_ready_made_cup_without_a_look_keeps_the_cup_default(svc):
    p = {"name": "Old", "format": "cup", "teams": [9]}
    assert "look" not in pestourney.save_preset.__defaults__ if pestourney.save_preset.__defaults__ else True
    cid = json.loads(pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {**p, "start": "2020-01-01", "end": "2099-01-01"}).encode())[1])["compe_id"]
    assert pestourney.look(pestourney.get(svc, cid)) == pestourney.CUP_LOOK
