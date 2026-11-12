"""Seasons of the friends' league table (the admin page's Players table).

The table counts the matches of the current season only. "Start a new season" keeps the finished season's final
table (pes_seasons) and starts counting from zero; results, the game's own ratings, Online Divisions and
tournaments are not touched. The last finished season can be reopened, which undoes the start of the new one.
"""
import json, logging, time

log = logging.getLogger("pesserver.season")
NAME_MAX = 40


def _db(svc):
    db = svc.accounts.db
    if not getattr(svc, "_pes_season_ready", False):
        with svc.accounts.lock, db:
            db.execute("CREATE TABLE IF NOT EXISTS pes_seasons(id INTEGER PRIMARY KEY, name TEXT NOT NULL,"
                       " started REAL NOT NULL, ended REAL NOT NULL, summary TEXT NOT NULL, tbl TEXT NOT NULL)")
        svc._pes_season_ready = True
    return db


def current(svc):
    """{n, name, started}: the season the table is counting now (started 0 = since the first match)."""
    last = _db(svc).execute("SELECT COUNT(*), COALESCE(MAX(ended), 0) FROM pes_seasons").fetchone()
    return {"n": last[0] + 1, "name": f"Season {last[0] + 1}", "started": last[1]}


def past(svc):
    """Finished seasons, newest first: [{id, name, started, ended, players, matches, winner}]."""
    return [{"id": i, "name": name, "started": a, "ended": b, **json.loads(summary)}
            for i, name, a, b, summary in _db(svc).execute("SELECT id, name, started, ended, summary FROM pes_seasons ORDER BY id DESC")]


def table(svc, sid):
    """A finished season's final table, best first, or None."""
    r = _db(svc).execute("SELECT name, started, ended, tbl FROM pes_seasons WHERE id=?", (int(sid),)).fetchone()
    return None if r is None else {"id": int(sid), "name": r[0], "started": r[1], "ended": r[2], "table": json.loads(r[3])}


def _final_table(svc, since, until):
    from . import events
    db = svc.accounts.db
    names = dict(db.execute("SELECT pid, name FROM pes_players"))
    rows = [{"pid": pid, "name": names.get(pid) or f"Player {pid}", **{k: v for k, v in rec.items() if k not in ("form", "run")},
             "pts": rec["won"] * 3 + rec["drawn"]} for pid, rec in events.standings(db, since, until).items()]
    rows.sort(key=lambda r: (-r["pts"], -(r["gf"] - r["ga"]), r["played"], r["name"].lower()))      # the page's order
    return rows


def start_new(svc, name=None):
    """Close the current season (its final table is kept) and start the next. Returns the closed season."""
    db, cur, now = _db(svc), current(svc), time.time()
    rows = _final_table(svc, cur["started"], now)
    if not rows:
        raise ValueError("no matches in this season yet")
    name = " ".join(str(name or "").split())[:NAME_MAX] or cur["name"]
    started = cur["started"] or db.execute("SELECT MIN(ended) FROM pes_matches").fetchone()[0] or now
    top = rows[0]
    summary = {"players": len(rows), "matches": sum(r["played"] for r in rows) // 2,
               "winner": {"pid": top["pid"], "name": top["name"], "pts": top["pts"]} if top["pts"] else None}
    with svc.accounts.lock, db:
        sid = db.execute("INSERT INTO pes_seasons(name, started, ended, summary, tbl) VALUES(?,?,?,?,?)",
                         (name, started, now, json.dumps(summary), json.dumps(rows))).lastrowid
    log.info("season %d (%s) closed with %d players", sid, name, len(rows))
    return {"id": sid, "name": name, **summary}


def reopen(svc):
    """Undo the last "start a new season": the newest finished season becomes the current one again, with the
    matches played since. Returns its name, or None when there is no finished season."""
    db = _db(svc)
    with svc.accounts.lock, db:
        r = db.execute("SELECT id, name FROM pes_seasons ORDER BY id DESC LIMIT 1").fetchone()
        if r is None:
            return None
        db.execute("DELETE FROM pes_seasons WHERE id=?", (r[0],))
    return r[1]
