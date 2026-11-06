"""The admin dashboard's data: totals, chart history, the biggest win, the player panel and the message meter."""
import time
from pesserver import announce, events, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service


def _svc(tmp_path):
    svc = Service(Accounts(str(tmp_path / "a.db")))
    svc.data_dir = str(tmp_path)
    return svc


def _seed(svc):
    db = pescmds._db(svc)
    events._st(svc)
    now = time.time()
    with svc.accounts.lock, db:
        for pid, name in ((1, "Ann"), (2, "Bob"), (3, "Cid")):
            db.execute("INSERT INTO pes_players(pid, username, name, created, last_login) VALUES(?,?,?,?,?)", (pid, f"u{pid}", name, now - 99, now))
        rows = [(now - 3600, 1, 2, "Ann", "Bob", 3, 1, ""), (now - 1800, 2, 1, "Bob", "Ann", 5, 0, ""),
                (now - 600, 1, 3, "Ann", "Cid", 6, 0, "DISCONNECT"), (now - 40 * 86400, 2, 3, "Bob", "Cid", 1, 1, ""),
                (now - 300, 3, 2, "Cid", "Bob", None, None, "")]
        for t, hp, ap, hn, an, hs, as_, why in rows:
            db.execute("INSERT INTO pes_matches(started, ended, home_pid, away_pid, home_name, away_name, home_score, away_score, reason)"
                       " VALUES(?,?,?,?,?,?,?,?,?)", (t - 900, t, hp, ap, hn, an, hs, as_, why))
    return now


def test_snapshot_totals_history_and_biggest_win(tmp_path):
    svc = _svc(tmp_path); now = _seed(svc)
    snap = events.snapshot(svc)
    st = snap["stats"]
    assert snap["played_total"] == 5 and st["goals"] == 3 + 1 + 5 + 6 + 2 and st["abandoned"] == 1
    assert len(st["history"]) == 4                        # the 40-day-old match is outside the charts
    assert all(abs(h[0] - (t - 900)) < 1 for h, t in zip(st["history"], (now - 3600, now - 1800, now - 600, now - 300)))   # by kick-off
    assert st["history"][-1][1] == 0                      # missing scores count as no goals
    assert st["biggest"]["home_name"] == "Bob" and (st["biggest"]["home_score"], st["biggest"]["away_score"]) == (5, 0)   # abandoned 6-0 doesn't count
    ann = next(p for p in snap["players"] if p["name"] == "Ann")
    assert ann["rating"] == 1000 and "streak" in ann
    assert {"home_pid", "away_pid"} <= set(snap["results"][0])


def test_player_detail(tmp_path):
    svc = _svc(tmp_path); _seed(svc)
    events.feed(svc, "Ann came online", "login"); events.feed(svc, "Bob came online", "login")
    p = events.player_detail(svc, 1)
    assert p["name"] == "Ann" and not p["online"] and len(p["matches"]) == 3
    assert [f["text"] for f in p["feed"]] == ["Ann came online"]
    assert events.player_detail(svc, 99) is None


def test_message_meter(tmp_path):
    assert announce.fits("", "") == 0
    import random
    r = random.Random(1)                                  # varied letters: the encoder compresses repeats
    arabic = "".join(chr(r.randint(0x627, 0x64a)) for _ in range(600))
    small, big = announce.fits("Cup", "Friday 9 pm"), announce.fits("Cup", arabic)
    assert 0 < small < 100 and big > 100
