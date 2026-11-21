"""The knockout for the game's own bracket screen (/mod/bracket, pescup.bracket)."""
import asyncio, json, random

from pesserver import httpgate, pescmds, pescup, pesmatch, pestourney
from test_pesrooms import svc, Sess   # noqa: F401  (fixture)
from test_tourney import _players
from test_cup import _play


def _cup(svc, n, groups=False):
    body = {"name": "Bracket Cup", "start": "2020-01-01", "end": "2099-01-01", "format": "cup", "third_place": True}
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(body).encode())
    cid = json.loads(out)["compe_id"]
    _players(svc, *range(1, n + 1))
    sess = {p: Sess(p) for p in range(1, n + 1)}
    for p, s in sess.items():
        s.mod_id = f"{p:032x}"
    svc.online.update(sess)
    for p, s in sess.items():
        pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": pestourney.TEAMS[p]})
        pescmds._dispatch(svc, s, "CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO", {"compe_id": cid})
    return pestourney.get(svc, cid), sess


def _lines(text):
    out = {}
    for ln in text.splitlines():
        k, *v = ln.split()
        out.setdefault(k, []).append([int(x) for x in v])
    return out


def test_bracket_of_a_four_player_cup(svc):
    async def go():
        c, sess = _cup(svc, 4)
        assert httpgate.mod_bracket(svc, sess[1].mod_id) == ""            # before the draw: nothing
        pescup.draw(svc, c, rng=random.Random(1))
        b = _lines(httpgate.mod_bracket(svc, sess[1].mod_id))
        assert b["size"] == [[4]] and len(b["slot"]) == 4 and b["thirdplace"] == [[1]]
        assert sorted(t for _, t in b["slot"]) == sorted(pestourney.TEAMS[1:5])
        assert len(b["tie"]) == 2 and all(t[0] == 1 and t[6] == -1 and t[7] == 0 for t in b["tie"])
        semis = [f for f in pescup.fixtures(svc, c["compe_id"]) if f["round"] == 1]
        _play(svc, sess, semis[0]["a"], semis[0]["b"], 2, 1)
        b = _lines(httpgate.mod_bracket(svc, sess[1].mod_id))
        done = [t for t in b["tie"] if t[0] == 1 and t[7]]
        assert len(done) == 1 and done[0][6] in (0, 1) and sorted(done[0][4:6]) == [1, 2]
        _play(svc, sess, semis[1]["a"], semis[1]["b"], 0, 0, pk=(4, 3))
        b = _lines(httpgate.mod_bracket(svc, sess[1].mod_id))
        final = [t for t in b["tie"] if t[0] == 2]
        assert len(final) == 1 and final[0][2] and final[0][3] and final[0][7] == 0      # both finalists known
        assert b["third"] and b["third"][0][0] and b["third"][0][1]
        pk_tie = next(t for t in b["tie"] if t[0] == 1 and t[4] == t[5])                  # 0-0, decided on penalties
        assert pk_tie[6] in (0, 1) and pk_tie[7] == 1
    asyncio.run(go())


def test_bracket_with_byes_and_unknown_players(svc):
    c, sess = _cup(svc, 3)
    pescup.draw(svc, c, rng=random.Random(2))
    b = _lines(httpgate.mod_bracket(svc, sess[1].mod_id))
    assert b["size"] == [[4]] and sorted(t for _, t in b["slot"]).count(0) == 1          # one empty place: a bye
    assert httpgate.mod_bracket(svc, "nobody") == ""


def test_a_league_has_no_bracket(svc):
    st, out, _ = pestourney.admin_api(svc, "/admin/api/tourneys/save", "POST", json.dumps(
        {"name": "L", "start": "2020-01-01", "end": "2099-01-01"}).encode())
    cid = json.loads(out)["compe_id"]
    _players(svc, 1)
    s = Sess(1); s.mod_id = "1" * 32
    svc.online[1] = s
    pescmds._dispatch(svc, s, "CMD_JOIN_PESLEAGUE_COMPE", {"compe_id": cid, "team_id": 9})
    assert httpgate.mod_bracket(svc, s.mod_id) == ""


def test_two_players_no_third_place(svc):
    c, sess = _cup(svc, 2)
    pescup.draw(svc, c, rng=random.Random(1))
    b = _lines(httpgate.mod_bracket(svc, sess[1].mod_id))
    assert b["size"] == [[2]] and b["thirdplace"] == [[0]] and len(b["tie"]) == 1
