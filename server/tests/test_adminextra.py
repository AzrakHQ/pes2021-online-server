"""The admin page's view of the game modes: where friends are, why two can't be paired, courtesy, Online Divisions,
Matchday, match rooms, the relay and the Live Update."""
import time
from types import SimpleNamespace

from pesserver import adminextra, events, pescmds, pescoop, pesdivisions, pesmanner, pesmatch, pesmatchday, pesrooms
from test_dashboard import _seed, _svc


def _sess(pid, feats=("math1", "coop1"), sums=("p1", "e1"), mtype=None):
    s = SimpleNamespace(pes_pid=pid, peer_ip=f"10.0.0.{pid}", connected_at=time.time() - 60, mod_id="ab" * 16,
                        mod_features=frozenset(feats), pes_sums={"param": sums[0], "entry": sums[1]},
                        pes_versions={}, close=lambda: None)
    if mtype:
        s.pes_match_req = {"matching_type": mtype}
    return s


def test_where_friends_are_and_their_mods(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    ann, bob, cid = _sess(1), _sess(2, feats=("math1",), mtype="OC_RANK"), _sess(3, feats=())
    svc.online.update({1: ann, 2: bob, 3: cid})
    mm = pesmatch._state(svc)
    mm["queue"].append((2, bob)); mm.setdefault("qsince", {})[2] = time.time() - 200
    pescoop._queue(svc).append((1, ann, 0)); mm["qsince"][1] = time.time() - 50
    pesrooms._st(svc)["rooms"][5] = {"id": 5, "owner": 3, "members": [3], "state": "WAITING",
                                     "opts": {"name": "Cid's room", "password": "x"}}
    snap = adminextra.augment(svc, events.snapshot(svc))
    by = {o["pid"]: o for o in snap["online"]}
    assert by[1]["searching_for"] == "Online Co-op" and by[1]["status"] == "Searching" and by[1]["searching_since"]
    assert by[2]["searching_for"] == "Online Divisions" and by[2]["mod"] == "no-coop"
    assert by[3]["status"] == "In a match room" and by[3]["room"] == "Cid's room" and by[3]["mod"] == "old"
    assert by[1]["mod"] == "current" and snap["searching"] == 2
    assert snap["rooms"] == [{"id": 5, "name": "Cid's room", "owner": 3, "members": [3], "state": "In the room", "password": True}]


def test_why_two_searching_friends_are_not_paired(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    mm = pesmatch._state(svc)
    a, b, c = _sess(1), _sess(2, sums=("p2", "e1")), _sess(3, feats=())
    mm["queue"] += [(1, a), (2, b), (3, c)]
    kinds = {(p["kind"], tuple(p["pids"])) for p in adminextra._problems(svc, mm)}
    assert ("data", (1, 2)) in kinds and ("mod", (1, 3)) in kinds
    assert next(p for p in adminextra._problems(svc, mm) if p["kind"] == "mod")["old"] == 3
    mm["queue"][:] = [(1, a), (2, _sess(2, sums=("p2", "e1"), mtype="OC_RANK"))]
    assert adminextra._problems(svc, mm) == []          # different modes never meet: not a data problem
    mm["queue"][:] = []
    pescoop._queue(svc)[:] = [(1, a, 0), (3, c, 0)]
    pescmds.dispatch(svc, a, "CMD_SET_COOP_REJECT", {"set_reject_pid_list": [3]})
    kinds = {(p["kind"], tuple(p["pids"])) for p in adminextra._problems(svc, mm)}
    assert kinds == {("coop_mod", (3,)), ("coop_blocked", (1, 3))}


def test_courtesy_divisions_and_matchday_per_player(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    for early in (True, True, False, True, True):
        pesmanner._add(svc, 2, early, "EXIT" if early else "")
    pesmanner._add(svc, 1, False)
    room = {"owner": 1, "members": [1, 2], "goals": {(1, "HOME"), (2, "HOME")}, "match_id": "m1"}
    pesdivisions._state(svc, 1); pesdivisions._state(svc, 2); pesdivisions._state(svc, 3)    # Cid opened the mode only
    pesdivisions.record(svc, room)
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        pesmatchday._setup(db)
        db.execute("INSERT INTO pes_matchday(pid, compe_id, grp, team_id, points, matches) VALUES (1, 1, 'MATCHDAY_GROUP_A', 101, 6, 2)")
        db.execute("INSERT INTO pes_matchday(pid, compe_id, grp, team_id, points, matches) VALUES (2, 1, 'MATCHDAY_GROUP_B', 100, 1, 2)")
    snap = adminextra.augment(svc, events.snapshot(svc))
    by = {p["pid"]: p for p in snap["players"]}
    assert by[2]["courtesy"] == {"grade": "C", "quits": 4, "matches": 5} and by[1]["courtesy"]["grade"] == "A"
    assert by[3]["courtesy"] is None
    assert by[1]["division"]["div"] == 10 and by[1]["division"]["points"] == 3 and by[1]["division"]["played"] == 1
    assert by[2]["division"]["losses"] == 1 and by[3]["division"] is None
    md = snap["matchday"]
    assert [s["points"] for s in md["sides"]] == [6, 1] and md["entries"][0] == {"pid": 1, "side": "Side A", "points": 6, "matches": 2}
    one = adminextra.player(svc, 2)
    assert one["courtesy"]["grade"] == "C" and one["courtesy"]["early"][0]["why"] == "quit from the pause menu"
    assert one["matchday"]["side"] == "Side B" and one["mod"] is None
    adminextra.clear_courtesy(svc, 2)
    assert adminextra.player(svc, 2)["courtesy"] is None
    adminextra.forget(svc, 1)
    assert adminextra.player(svc, 1)["division"] is None and adminextra.player(svc, 1)["matchday"] is None


def test_live_match_and_result_modes(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    mm = pesmatch._state(svc)
    mm["rooms"][7] = {"owner": 1, "members": [1, 2], "t": time.time(), "mode": "OC_RANK"}
    a, b = _sess(1), _sess(2)
    svc.online.update({1: a, 2: b})
    events.observe(svc, a, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST"})
    snap = adminextra.augment(svc, events.snapshot(svc))
    assert snap["live"][0]["mode"] == "Online Divisions"
    events.observe(svc, a, "CMD_SEND_GAME_RESULT", {"home_score": 1, "away_score": 0})
    assert events.results_page(svc, mode="Online Divisions")[1] == 1
    assert events.results_page(svc, mode="Quick Match")[1] == 5       # older rows have no mode: Quick Match
    assert adminextra.mode_of_room({"lobby_room": 3}) == "Friendly Match Lobby"
    assert adminextra.mode_of_room({"coop": True}) == "Online Co-op"


def test_relay_and_live_update_card(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    srv = adminextra.server(svc)
    assert srv["relay"]["on"] is False and srv["liveupdate"] == {"folder": str(tmp_path / "liveupdate")}
    svc.online[2] = _sess(2)
    adminextra.note_liveupdate(svc, "10.0.0.2", "roster.cpk")
    adminextra.note_liveupdate(svc, "10.0.0.2", "roster.cpk")        # once per game and file
    lines = [f["text"] for f in events._st(svc)["feed"] if "Live Update" in f["text"]]
    assert lines == ["Bob's game downloaded the Live Update (roster.cpk)"]


def test_tournament_mode_blocks_and_competitions(tmp_path):
    from pesserver import pesblock, pestourney
    svc = _svc(tmp_path); _seed(svc)
    cid = pestourney.save(svc, {"name": "Saturday Cup", "start": int(time.time()) - 60, "end": int(time.time()) + 3600,
                                "teams": [45, 50], "match_minutes": 6})
    a, b = _sess(1, mtype="PES_LEAGUE"), _sess(2, mtype="PES_LEAGUE")
    a.pes_compe = b.pes_compe = cid
    svc.online.update({1: a, 2: b})
    mm = pesmatch._state(svc)
    mm["queue"] += [(1, a), (2, b)]
    pescmds.dispatch(svc, a, "CMD_SET_MATCHREJECTPID", {"reject_pid": 2, "is_reject": "YES"})
    snap = adminextra.augment(svc, events.snapshot(svc))
    by = {o["pid"]: o for o in snap["online"]}
    assert by[1]["searching_for"] == "Online Tournament" and by[1]["competition"] == "Saturday Cup"
    assert snap["problems"] == [{"kind": "blocked", "pids": [1, 2], "by": [1]}]
    b.pes_compe = cid + 1                                # another competition: a different queue, no problem
    assert adminextra._problems(svc, mm) == []
    t = next(c for c in snap["tourneys"] if c["compe_id"] == cid)
    assert t["state"] == "running" and t["minutes"] == 6 and t["team_names"] == {"45": "Brazil", "50": "Argentina"}
    assert t["auto"] is False and t["matches"] == 0 and "6-minute" in t["outline"]
    room = {"compe": cid, "owner": 1, "members": [1, 2], "goals": {(1, "HOME")}, "teams": {1: 45, 2: 50},
            "match_id": "t1", "t": time.time()}
    pestourney.record(svc, room)
    t = next(c for c in adminextra._tourneys(svc) if c["compe_id"] == cid)
    assert t["matches"] == 1 and [(r["name"], r["team"], r["points"]) for r in t["table"]] == [("Ann", "Brazil", 3), ("Bob", "Argentina", 0)]
    one = adminextra.player(svc, 1)
    assert one["match_blocks"] == [2] and adminextra.player(svc, 2)["match_blocked_by"] == [1]
    assert one["tourneys"] == [{"compe_id": cid, "name": "Saturday Cup", "state": "running", "rank": 1, "of": 2,
                                "points": 3, "win": 1, "draw": 0, "lose": 0, "team": "Brazil"}]
    assert adminextra.mode_of_room({"mode": "PES_LEAGUE"}) == "Online Tournament"


def test_profile_coop_level_and_replays(tmp_path):
    from pesserver import pesprofile
    svc = _svc(tmp_path); _seed(svc)
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        pesprofile._setup(db)
        db.execute("INSERT INTO pes_profile (pid, team, player, intro, t) VALUES (1, 'Brazil', 'Pele', 'hi', 0)")
    pescoop.award(svc, {"owner": 1, "members": [1, 2], "goals": {(1, "HOME")}, "match_id": "c1"})
    one = adminextra.player(svc, 1)
    assert one["profile"] == {"team": "Brazil", "player": "Pele", "intro": "hi"}
    assert one["coop_level"] == {"level": 1, "points": 30, "matches": 1, "next": 70}
    assert adminextra.player(svc, 3)["coop_level"] is None and adminextra.player(svc, 3)["profile"] is None
    d = tmp_path / "replays" / "m1"
    d.mkdir(parents=True)
    (d / "meta.json").write_text('{"home": 1, "away": 2, "home_name": "Ann", "away_name": "Bob", "t0": 5, "n": 3, '
                                 '"closed": true, "home_score": 2, "away_score": 1, "home_team": 45, "away_team": 0}')
    snap = adminextra.augment(svc, events.snapshot(svc))
    assert snap["replays"] == [{"match_id": "m1", "home": 1, "away": 2, "home_name": "Ann", "away_name": "Bob",
                                "home_score": 2, "away_score": 1, "t": 5, "chunks": 3, "recording": False,
                                "home_team": "Brazil", "away_team": None}]
    assert adminextra.server(svc)["replays"]["on"] is True


def test_word_filter_from_the_admin_page(tmp_path):
    from pesserver import peswords
    svc = _svc(tmp_path); _seed(svc)
    assert adminextra.server(svc)["words"]["words"] == []
    f = tmp_path / "blocked_words.txt"
    f.write_text("# my own note\nold\n")
    w = adminextra.set_words(svc, ["Bad", "bad ", "two  words", "", "x#y"])
    assert w["words"] == ["bad", "two words", "x y"]
    assert f.read_text() == "# my own note\nBad\ntwo words\nx y\n"
    assert peswords.blocked(svc, "a BAD name") == "bad" and peswords.blocked(svc, "old") is None
    import pytest
    with pytest.raises(ValueError):
        adminextra.set_words(svc, "bad")


def test_admin_routes_teams_words_and_tournament_log(tmp_path, monkeypatch):
    import asyncio, json
    from pesserver import httpgate, netsetup
    svc = _svc(tmp_path); _seed(svc); svc.admin_password = "pw-123456789"
    httpgate._FAILED.clear(); httpgate.load_remote(str(tmp_path))
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.50")
    h = {"host": "localhost:8080", "x-pes-local": "1"}
    tok = json.loads(asyncio.run(httpgate._admin(svc, "POST", "/admin/api/login", h, b'{"local": true}', "127.0.0.1"))[1])["token"]
    ha = {**h, "authorization": "Bearer " + tok}

    def call(route, body=b"", method="POST"):
        code, out, _ = asyncio.run(httpgate._admin(svc, method, route, ha, body, "127.0.0.1"))
        return int(code.split()[0]), json.loads(out)

    code, t = call("/admin/api/teams", method="GET")
    assert code == 200 and t["names"]["45"] == "Brazil" and 45 in t["national"][2]["teams"]
    assert call("/admin/api/words", b'{"words": ["bad"]}') == (200, {"path": str(tmp_path / "blocked_words.txt"), "words": ["bad"]})
    assert call("/admin/api/words", b'{"words": "bad"}')[0] == 400
    code, r = call("/admin/api/tourneys/save", json.dumps({"name": "Cup", "start": "2030-01-01", "end": "2030-01-02"}).encode())
    assert code == 200
    call("/admin/api/tourneys/save", json.dumps({"compe_id": r["compe_id"], "name": "Big Cup"}).encode())
    call("/admin/api/tourneys/delete", json.dumps({"compe_id": r["compe_id"]}).encode())
    lines = [f["text"] for f in events._st(svc)["feed"] if "Tournament" in f["text"] or "word" in f["text"]]
    assert lines == ["The admin deleted the Online Tournament “Big Cup”", "The admin changed the Online Tournament “Big Cup”",
                     "The admin made the Online Tournament “Cup”", "The admin changed the word filter (1 word)"]
