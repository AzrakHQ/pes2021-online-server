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


def test_results_pages_filters_and_counts(tmp_path):
    svc = _svc(tmp_path); now = _seed(svc)
    rows, total = events.results_page(svc, limit=2)
    assert total == 5 and len(rows) == 2 and rows[0]["id"] > rows[1]["id"]        # newest first, all time
    assert events.results_page(svc, "cid")[1] == 3                                 # one player's matches
    assert events.results_page(svc, since=now - 7200, until=now - 1000)[1] == 2   # one day's slice
    assert events.results_page(svc, since=now - 7200)[1] == 4                       # a period
    assert events.results_page(svc, "%")[1] == 0 and events.results_page(svc, "_")[1] == 0   # LIKE wildcards are literal


def test_message_to_one_player(tmp_path):
    from types import SimpleNamespace
    svc = _svc(tmp_path); _seed(svc); announce._STATE.clear()
    announce.tell(svc, 2, "Hi Bob", "Final at 9", kind="note")
    snap = announce.snapshot(svc)
    assert snap["pending"] == 1 and snap["personal"][0]["pid"] == 2 and snap["personal"][0]["kind"] == "note"
    announce.tell(svc, 2, "Hi again", "Final moved to 10", kind="note")         # replaces the unseen one
    assert [m["title"] for m in announce.snapshot(svc)["personal"]] == ["Hi again"]
    r = announce.urgent(svc, SimpleNamespace(pes_pid=2), {"pid": 2})
    assert r["is_announcement"] == "YES" and announce.snapshot(svc)["pending"] == 0
    assert events._st(svc)["feed"][0]["text"] == "Bob saw your message “Hi again”"
    assert announce.snapshot(svc)["seen_notes"][0]["title"] == "Hi again"
    assert announce.urgent(svc, SimpleNamespace(pes_pid=2), {"pid": 2}) == {"result": "NOERR"}   # once only
    announce.tell(svc, 3, "Hey", "x", kind="note")
    assert announce.untell(svc, 3) and not announce.untell(svc, 3)
    import random
    r = random.Random(2)
    try:
        announce.tell(svc, 1, "Cup", "".join(chr(r.randint(0x4e00, 0x9fff)) for _ in range(600)), kind="note")   # 3 bytes a letter
        assert False, "too long for one frame"
    except announce.TooLong:
        pass


def test_admin_page_structure():
    """Every view sits inside #content (a stray closing tag once pushed two views into the sidebar column),
    tags balance, and ids are unique."""
    import os, collections
    from html.parser import HTMLParser
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    class P(HTMLParser):
        def __init__(self):
            super().__init__(); self.stack, self.views, self.ids, self.errors = [], {}, collections.Counter(), []
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if a.get("id"):
                self.ids[a["id"]] += 1
            if tag == "section" and "view" in (a.get("class") or "").split():
                self.views[a.get("id")] = [x[1].get("id") for x in self.stack]
            if tag not in VOID:
                self.stack.append((tag, a))
        def handle_endtag(self, tag):
            if tag in VOID:
                return
            if not self.stack or self.stack[-1][0] != tag:
                self.errors.append(f"</{tag}> closes <{self.stack[-1][0] if self.stack else None}> at line {self.getpos()[0]}")
                return
            self.stack.pop()

    html = open(os.path.join(os.path.dirname(__file__), "..", "pesserver", "web", "admin.html"), encoding="utf-8").read()
    html = html[:html.index("<script>", html.index("</style>"))] + html[html.rindex("</script>") + 9:]   # markup only
    p = P(); p.feed(html)
    assert not p.errors, p.errors[:3]
    assert set(p.views) == {"v-overview", "v-players", "v-matches", "v-activity", "v-messages", "v-invite", "v-server"}
    for v, parents in p.views.items():
        assert "content" in parents and "app" in parents, (v, parents)
    assert [i for i, n in p.ids.items() if n > 1] == []


def test_earlier_messages_to_everyone(tmp_path):
    svc = _svc(tmp_path); announce._STATE.clear()
    announce.set_news(svc, "Cup", "Friday 9 pm")
    announce.set_news(svc, "Cup moved", "Saturday 9 pm")
    announce.clear_news(svc)
    snap = announce.snapshot(svc)
    assert snap["news"] is None and [h["title"] for h in snap["history"]] == ["Cup moved", "Cup"]


def test_form_run_and_week_cover_all_matches(tmp_path):
    svc = _svc(tmp_path); now = _seed(svc)
    snap = events.snapshot(svc)
    ann = next(p for p in snap["players"] if p["name"] == "Ann")
    # Ann: 3-1 W (oldest), 0-5 L, 6-0 W (abandoned, still counts with its score) -> oldest first
    assert ann["form"] == ["W", "L", "W"] and ann["run"] == 1
    wk = snap["week"]
    assert wk["wins"]["n"] == 2 and [w[1] for w in wk["wins"]["who"]] == ["Ann"]
    assert wk["goals"]["n"] == 9 and wk["goals"]["who"][0][1] == "Ann"
