"""Seasons of the league table, undo for a deleted player, backups and Discord notices (2026-11-12)."""
import asyncio, json, os, time
from types import SimpleNamespace
import pytest
from pesserver import events, httpgate, pesbackup, pescmds, pesdiscord, pesseason, pesundo
from pesserver.accounts import Accounts
from pesserver.service import Service


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "pesserver.db")))
    s.data_dir = str(tmp_path)
    s.admin_password = "s3cret-enough"
    httpgate._FAILED.clear()
    db = pescmds._db(s)
    events._st(s)
    from pesserver import pesnames
    pesnames._db(s)
    with s.accounts.lock, db:
        for pid, name in ((1, "Ann"), (2, "Bob"), (3, "Cid")):
            db.execute("INSERT INTO pes_players(pid, username, name, created, handle) VALUES(?,?,?,?,?)", (pid, f"u{pid}", name, 1.0, name.lower()))
    return s


def _match(svc, hp, ap, hs, as_, t=None):
    names = {1: "Ann", 2: "Bob", 3: "Cid"}
    t = t or time.time()
    with svc.accounts.lock, svc.accounts.db as db:
        db.execute("INSERT INTO pes_matches(started, ended, home_pid, away_pid, home_name, away_name, home_score, away_score, reason)"
                   " VALUES(?,?,?,?,?,?,?,?,'')", (t - 600, t, hp, ap, names[hp], names[ap], hs, as_))


def _api(svc, route, body=None, method="POST"):
    tok = httpgate._sessions(svc).new(True)
    st, out, _ = asyncio.run(httpgate._admin(svc, method, route.split("?")[0], {"authorization": "Bearer " + tok, "host": "127.0.0.1"},
                                             json.dumps(body or {}).encode(), "127.0.0.1", False, route))
    return int(st[:3]), (json.loads(out) if isinstance(out, bytes) else out)


def _player(svc, name):
    return next(p for p in events.snapshot(svc)["players"] if p["name"] == name)


# ---- one pass over the matches gives the same table as before ---------------------------------------
def test_standings_form_and_winning_run(svc):
    for k, (hs, as_) in enumerate([(0, 1), (1, 1), (2, 0), (3, 0), (1, 0)]):       # Ann: L D W W W (oldest first)
        _match(svc, 1, 2, hs, as_, time.time() - 1000 + k)
    ann, bob, cid = _player(svc, "Ann"), _player(svc, "Bob"), _player(svc, "Cid")
    assert (ann["played"], ann["won"], ann["drawn"], ann["lost"], ann["gf"], ann["ga"]) == (5, 3, 1, 1, 7, 2)
    assert ann["form"] == ["L", "D", "W", "W", "W"] and ann["run"] == 3 and bob["run"] == 0
    assert cid["played"] == 0 and cid["form"] == [] and cid["played_ever"] == 0


# ---- seasons ----------------------------------------------------------------------------------------
def test_a_new_season_starts_the_table_from_zero_and_keeps_the_old_one(svc):
    assert events.snapshot(svc)["season"] == {"n": 1, "name": "Season 1", "started": 0}
    assert _api(svc, "/admin/api/season/new")[0] == 400                 # nothing played: nothing to close
    _match(svc, 1, 2, 3, 0, time.time() - 50); _match(svc, 2, 3, 1, 1, time.time() - 40)
    code, done = _api(svc, "/admin/api/season/new", {"name": "  Autumn   cup "})
    assert code == 200 and done["name"] == "Autumn cup" and done["winner"] == {"pid": 1, "name": "Ann", "pts": 3}
    snap = events.snapshot(svc)
    assert snap["season"]["n"] == 2 and snap["season"]["started"] > 0
    assert [(s["name"], s["players"], s["matches"]) for s in snap["seasons"]] == [("Autumn cup", 3, 2)]
    ann = _player(svc, "Ann")
    assert ann["played"] == 0 and ann["played_ever"] == 1 and snap["played_total"] == 2      # results stay
    _match(svc, 3, 1, 2, 0, time.time() + 1)                           # the new season counts new matches only
    assert _player(svc, "Cid")["won"] == 1 and _player(svc, "Ann")["lost"] == 1 and _player(svc, "Ann")["won"] == 0
    code, t = _api(svc, "/admin/api/season", {"id": snap["seasons"][0]["id"]})
    assert code == 200 and [(r["name"], r["pts"]) for r in t["table"]] == [("Ann", 3), ("Cid", 1), ("Bob", 1)]
    assert _api(svc, "/admin/api/season", {"id": 99})[0] == 404
    # reopening undoes it: one season again, with every match
    assert _api(svc, "/admin/api/season/reopen")[1] == {"name": "Autumn cup"}
    assert events.snapshot(svc)["season"]["n"] == 1 and _player(svc, "Ann")["played"] == 2
    assert _api(svc, "/admin/api/season/reopen")[0] == 404


# ---- undo for Delete player -------------------------------------------------------------------------
def test_a_deleted_player_can_be_put_back(svc):
    from pesserver import pesmanner, pesprofile
    db = svc.accounts.db
    pesmanner._add(svc, 2, True, "EXIT")
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_players SET rating=1234 WHERE pid=2")
    _match(svc, 1, 2, 0, 2)
    code, r = _api(svc, "/admin/api/players/delete", {"pid": 2})
    assert code == 200 and r["undo"]
    assert db.execute("SELECT COUNT(*) FROM pes_players WHERE pid=2").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM pes_manner WHERE pid=2").fetchone()[0] == 0
    gone = events.snapshot(svc)["deleted"]
    assert [(d["name"], d["pid"]) for d in gone] == [("Bob", 2)]
    code, r = _api(svc, "/admin/api/players/restore", {"id": gone[0]["id"]})
    assert code == 200 and r["name"] == "Bob" and events.snapshot(svc)["deleted"] == []
    assert db.execute("SELECT name, rating, username FROM pes_players WHERE pid=2").fetchone() == ("Bob", 1234, "u2")
    assert db.execute("SELECT left_early, reason FROM pes_manner WHERE pid=2").fetchall() == [(1, "EXIT")]
    assert _player(svc, "Bob")["won"] == 1                             # their results never left
    assert _api(svc, "/admin/api/players/restore", {"id": gone[0]["id"]})[0] == 404


def test_what_others_did_meanwhile_does_not_push_out_a_restored_players_rows(svc):
    from pesserver import pesmanner
    db = svc.accounts.db
    pesmanner._add(svc, 2, True, "EXIT"); pesmanner._add(svc, 2, True, "EXIT")
    did = _api(svc, "/admin/api/players/delete", {"pid": 2})[1]["undo"]
    for _ in range(3):                                                 # the others play on: the freed row numbers
        pesmanner._add(svc, 1, False)                                  # are taken by their rows
    assert _api(svc, "/admin/api/players/restore", {"id": did})[0] == 200
    assert db.execute("SELECT left_early, reason FROM pes_manner WHERE pid=2").fetchall() == [(1, "EXIT")] * 2
    assert db.execute("SELECT COUNT(*) FROM pes_manner WHERE pid=1").fetchone()[0] == 3


def test_restore_is_refused_when_their_game_already_joined_again(svc):
    db = svc.accounts.db
    did = _api(svc, "/admin/api/players/delete", {"pid": 2})[1]["undo"]
    with svc.accounts.lock, db:
        db.execute("INSERT INTO pes_players(pid, username, name, created, handle) VALUES(9, 'u2', 'Bobby', 1.0, 'bobby')")
    code, r = _api(svc, "/admin/api/players/restore", {"id": did})
    assert code == 409 and "joined again as Bobby" in r["error"]
    with svc.accounts.lock, db:
        db.execute("DELETE FROM pes_players WHERE pid=9")
        db.execute("INSERT INTO pes_players(pid, username, name, created, handle) VALUES(9, 'other', 'bob', 1.0, 'bob')")
    code, r = _api(svc, "/admin/api/players/restore", {"id": did})
    assert code == 409 and "is called bob now" in r["error"]
    with svc.accounts.lock, db:                                        # also a name the game would call the same
        db.execute("UPDATE pes_players SET name='B_o-b', handle='b_o-b' WHERE pid=9")
    code, r = _api(svc, "/admin/api/players/restore", {"id": did})
    assert code == 409 and "is called B_o-b now" in r["error"]
    assert db.execute("SELECT COUNT(*) FROM pes_deleted WHERE id=?", (did,)).fetchone()[0] == 1   # still there to restore


# ---- backups ----------------------------------------------------------------------------------------
def test_backup_download_and_restore(svc):
    _match(svc, 1, 2, 3, 0)
    code, made = _api(svc, "/admin/api/backups/new")
    assert code == 200 and made["name"].startswith("manual-") and made["size"] > 0
    assert [b["name"] for b in pesbackup.listing(svc)] == [made["name"]]
    state = _api(svc, "/admin/api/state", method="GET")[1]
    assert [b["name"] for b in state["server"]["backups"]["list"]] == [made["name"]]
    assert state["results"] == []                                      # (for a page from before the update)
    code, f = _api(svc, "/admin/api/backups/file?name=" + made["name"], method="GET")
    assert code == 200 and f[0] == "file" and f[1].endswith(made["name"]) and f[2] == made["size"]
    assert _api(svc, "/admin/api/backups/file?name=../pesserver.db", method="GET")[0] == 404
    assert pesbackup.file_for(svc, "../pesserver.db") is None and pesbackup.file_for(svc, "x.txt") is None
    # change things, then go back
    _api(svc, "/admin/api/players/delete", {"pid": 3}); _match(svc, 1, 2, 0, 5)
    closed = []
    svc.online[1] = SimpleNamespace(pes_pid=1, close=lambda: closed.append(1))
    # a copy that fails changes nothing: nobody is disconnected, and the page is told
    real = pesbackup.sqlite3.connect
    class Broken:
        def __init__(self, *a, **k): self.c = real(*a, **k)
        def backup(self, dst): raise pesbackup.sqlite3.OperationalError("destination database is in use")
        def __getattr__(self, n): return getattr(self.c, n)
    calls = []
    def connect(*a, **k):
        calls.append(a) if k.get("uri") else None                      # opening the backup: 1 the check, 2 the copy
        return Broken(*a, **k) if k.get("uri") and len(calls) == 2 else real(*a, **k)
    pesbackup.sqlite3.connect = connect
    try:
        code, err = _api(svc, "/admin/api/backups/restore", {"name": made["name"]})
    finally:
        pesbackup.sqlite3.connect = real
    assert code == 500 and "Nothing was changed" in err["error"] and closed == [] and 1 in svc.online
    assert events.snapshot(svc)["played_total"] == 2
    code, found = _api(svc, "/admin/api/backups/restore", {"name": made["name"]})
    assert code == 200 and found == {"players": 3, "matches": 1} and closed == [1]
    # the game's own reconnect is refused: it has to sign in again, as the player the backup knows
    assert pescmds._dispatch(svc, SimpleNamespace(), "CMD_RECONNECT", {"pid": 1})["result"] == "ERR_LOGIN_FAILED"
    snap = events.snapshot(svc)
    assert snap["played_total"] == 1 and {p["name"] for p in snap["players"]} == {"Ann", "Bob", "Cid"}
    assert any(b["name"].startswith("before-restore-") for b in pesbackup.listing(svc))    # the way back
    assert "restored the backup" in snap["feed"][0]["text"]
    assert _api(svc, "/admin/api/backups/restore", {"name": "nope.db"})[0] == 404
    open(os.path.join(pesbackup.folder(svc), "junk.db"), "w").write("not a database")
    assert _api(svc, "/admin/api/backups/restore", {"name": "junk.db"})[0] == 400


def test_daily_backups_keep_the_newest_seven(svc, monkeypatch):
    stamps = iter(f"202611{d:02d}-000000" for d in range(1, 12))
    monkeypatch.setattr(pesbackup.time, "strftime", lambda fmt: next(stamps))
    for i in range(10):
        b = pesbackup.make(svc, "auto")
        os.utime(os.path.join(pesbackup.folder(svc), b["name"]), (1000 + i, 1000 + i))
    names = [b["name"] for b in pesbackup.listing(svc)]
    assert len(names) <= pesbackup.KEEP_AUTO + 1 and "auto-20261101-000000.db" not in names


# ---- Discord ----------------------------------------------------------------------------------------
HOOK = "https://discord.com/api/webhooks/123456789012345678/" + "a" * 60


def test_discord_notices(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesdiscord, "post", lambda s, text: sent.append(text))
    events.feed(svc, "Full time: Ann 2–1 Bob", "result", [1, 2])
    assert sent == []                                                  # off until an address is saved
    assert _api(svc, "/admin/api/discord", {"url": "https://example.com/api/webhooks/1/x"})[0] == 400
    assert _api(svc, "/admin/api/discord", {"url": "http://discord.com/api/webhooks/123456789012345678/" + "a" * 60})[0] == 400
    code, st = _api(svc, "/admin/api/discord", {"url": HOOK})
    assert code == 200 and st == {"on": True, "search": True, "results": True, "error": ""}
    assert HOOK not in json.dumps(events.snapshot(svc)["feed"]) and "url" not in st       # the address is a secret
    assert oct(os.stat(os.path.join(svc.data_dir, "discord.json")).st_mode & 0o777) == "0o600"
    events.feed(svc, "Full time: Ann 2–1 Bob", "result", [1, 2])
    events.feed(svc, "Ratings: Ann 1000 → 1012, Bob 1000 → 988", "result", [1, 2])        # not a notice
    events.feed(svc, "Ann came online", "login", [1])
    assert sent == ["⚽ Full time: Ann 2–1 Bob"]
    # searching: only while still waiting, and once per player per 10 minutes
    events.feed(svc, "Ann is looking for a match in Quick Match", "search", [1])
    assert len(sent) == 1                                              # paired at once: not in the queue
    svc.pes_mm = {"queue": [(1, None)]}
    events.feed(svc, "Ann is looking for a match in Quick Match", "search", [1])
    events.feed(svc, "Ann is looking for a match in Quick Match", "search", [1])
    assert sent[1:] == ["🔎 Ann is looking for a match in Quick Match"]
    _api(svc, "/admin/api/discord", {"results": False})
    events.feed(svc, "Full time: Ann 0–0 Bob", "result", [1, 2])
    assert len(sent) == 2 and pesdiscord.status(svc)["on"]             # the address was kept
    del svc._pes_discord                                               # a restart reads the saved settings
    assert pesdiscord.status(svc) == {"on": True, "search": True, "results": False, "error": ""}
    assert _api(svc, "/admin/api/discord", {"url": ""})[1]["on"] is False


def test_discord_test_message_reports_the_problem(svc, monkeypatch):
    calls = []

    def fake(req, timeout=0):
        calls.append((req.full_url, json.loads(req.data), req.get_header("User-agent")))
        raise pesdiscord.urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)
    monkeypatch.setattr(pesdiscord.urllib.request, "urlopen", fake)
    assert _api(svc, "/admin/api/discord", {"test": True})[1]["sent"] is False and calls == []   # nothing saved
    _api(svc, "/admin/api/discord", {"url": HOOK})
    code, st = _api(svc, "/admin/api/discord", {"test": True})
    assert code == 200 and st["sent"] is False and "doesn't know this webhook" in st["error"]
    assert calls[0][0] == HOOK and calls[0][1]["allowed_mentions"] == {"parse": []} and "pes2021" in calls[0][2]


def test_an_answer_cut_short_does_not_stop_the_notices(svc, monkeypatch):
    import http.client
    def cut(*a, **k):
        raise http.client.IncompleteRead(b"")
    monkeypatch.setattr(pesdiscord.urllib.request, "urlopen", cut)
    err = pesdiscord._send("https://discord.com/api/webhooks/1/x", "hi")
    assert err.startswith("couldn't reach Discord")
