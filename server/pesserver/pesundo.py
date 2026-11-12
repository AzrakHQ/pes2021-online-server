"""Undo for the admin's "Delete player": everything the server kept about the player is saved first and can be
put back for 30 days (pes_deleted).

What is saved: the player's row in every pes_ table that has a pid (or target) column, read just before
pescmds.purge_player removes them, and which past results were theirs (a delete keeps the results under the
player's name but takes their number off, so a later player with the same number doesn't inherit them). Putting a player back is refused when their copy of the mod has joined again
as a new player meanwhile, or someone took the name: the host sorts that out first."""
import base64, json, logging, time

log = logging.getLogger("pesserver.undo")
KEEP = 30 * 86400
SKIP = {"pes_matches", "pes_feed", "pes_deleted", "pes_seasons"}        # not removed by a delete
MATCH_COLS = ("home_pid", "away_pid", "left_pid")                       # ... but these lose the player's number


def _db(svc):
    from . import pescmds, pesnames
    db = pescmds._db(svc)
    pesnames._db(svc)                         # (the handle and blocked columns of older databases)
    if not getattr(svc, "_pes_undo_ready", False):
        with svc.accounts.lock, db:
            db.execute("CREATE TABLE IF NOT EXISTS pes_deleted(id INTEGER PRIMARY KEY, pid INTEGER, name TEXT,"
                       " username TEXT, t REAL, data TEXT)")
        svc._pes_undo_ready = True
    return db


def _enc(v):
    return {"__b": base64.b64encode(v).decode()} if isinstance(v, (bytes, bytearray)) else v


def _dec(v):
    return base64.b64decode(v["__b"]) if isinstance(v, dict) and "__b" in v else v


def capture(svc, pid):
    """Save the player before they are deleted. Returns the saved entry's id, or None if there's no such player."""
    db = _db(svc)
    with svc.accounts.lock, db:
        me = db.execute("SELECT name, username FROM pes_players WHERE pid=?", (pid,)).fetchone()
        if me is None:
            return None
        data = []
        for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pes\\_%' ESCAPE '\\'").fetchall():
            if t in SKIP:
                continue
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
            keys = [c for c in ("pid", "target") if c in cols]
            if not keys:
                continue
            rows = db.execute(f"SELECT * FROM {t} WHERE " + " OR ".join(f"{c}=?" for c in keys), [pid] * len(keys)).fetchall()
            if rows:
                data.append({"table": t, "cols": cols, "rows": [[_enc(v) for v in r] for r in rows]})
        theirs = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='pes_matches'").fetchone():
            theirs = {col: [r[0] for r in db.execute(f"SELECT id FROM pes_matches WHERE {col}=?", (pid,))]
                      for col in MATCH_COLS}
        did = db.execute("INSERT INTO pes_deleted(pid, name, username, t, data) VALUES(?,?,?,?,?)",
                         (pid, me[0], me[1], time.time(), json.dumps({"tables": data, "matches": theirs}))).lastrowid
        db.execute("DELETE FROM pes_deleted WHERE t < ?", (time.time() - KEEP,))
    return did


def listing(svc):
    """Players that can still be put back, newest first: [{id, pid, name, t}]."""
    return [{"id": i, "pid": pid, "name": name or f"Player {pid}", "t": t} for i, pid, name, t in _db(svc).execute(
        "SELECT id, pid, name, t FROM pes_deleted WHERE t >= ? ORDER BY id DESC", (time.time() - KEEP,))]


def _same_name(db, name):
    """The name of a player who has this name now, by the game's own rule (pesnames.key), or None."""
    from . import pesnames
    if not name:
        return None
    k = pesnames.key(name)
    return next((n for n, h in db.execute("SELECT name, handle FROM pes_players WHERE name IS NOT NULL")
                 if pesnames.key(h or n) == k), None)


def restore(svc, did):
    """Put a deleted player back. Returns their name. LookupError: no such entry; ValueError: why not."""
    db = _db(svc)
    with svc.accounts.lock, db:
        r = db.execute("SELECT pid, name, username, data FROM pes_deleted WHERE id=?", (int(did),)).fetchone()
        if r is None:
            raise LookupError("That player can't be restored any more.")
        pid, name, username, saved = r[0], r[1] or f"Player {r[0]}", r[2], json.loads(r[3])
        data = saved["tables"]
        again = db.execute("SELECT name FROM pes_players WHERE username=?", (username,)).fetchone()
        if again is not None:
            raise ValueError(f"Their game has joined again as {again[0] or 'a new player'}. Delete that player first.")
        if db.execute("SELECT 1 FROM pes_players WHERE pid=?", (pid,)).fetchone():
            raise ValueError("Another player has their number now, so they can't be restored.")
        me = next((dict(zip(d["cols"], row)) for d in data if d["table"] == "pes_players" for row in d["rows"]), {})
        taken = _same_name(db, me.get("handle") or me.get("name"))
        if taken:
            raise ValueError(f"Another player is called {taken} now.")
        known = {t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for d in sorted(data, key=lambda d: d["table"] != "pes_players"):       # the player first
            if d["table"] not in known:
                continue
            info = db.execute(f"PRAGMA table_info({d['table']})").fetchall()
            pk = [c[1] for c in info if c[5]]
            # A row's own number (pes_manner.id, ...) may belong to someone else's row by now: it gets a new one.
            own_id = pk[0] if len(pk) == 1 and pk[0] not in ("pid", "target") else None
            cols = [c for c in d["cols"] if c in {c[1] for c in info} and c != own_id]
            idx = [d["cols"].index(c) for c in cols]
            n = db.executemany(f"INSERT OR IGNORE INTO {d['table']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [[_dec(row[i]) for i in idx] for row in d["rows"]]).rowcount
            if d["table"] == "pes_players" and n != len(d["rows"]):
                raise ValueError("They can't be restored: something of theirs is used by another player now.")
        for col, ids in saved.get("matches", {}).items():
            if col in MATCH_COLS:
                db.executemany(f"UPDATE pes_matches SET {col}=? WHERE id=? AND {col} IS NULL", [(pid, i) for i in ids])
        db.execute("DELETE FROM pes_deleted WHERE id=?", (int(did),))
    log.info("player %s (%s) restored", pid, name)
    return name
