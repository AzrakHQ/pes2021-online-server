"""Live server activity for the admin page: who is online, matches in progress, finished results and
an activity feed. Fed by pescmds.dispatch (observe) and gate disconnects; results are kept in SQLite."""
import collections, logging, os, sys, time

log = logging.getLogger("pesserver.events")

PHASES = {"DATASYNC": "Pre-match", "TEAMSELECT": "Pre-match", "MATCHMENU": "Pre-match",
          "GAMELOAD": "Loading", "ENTRY": "Walking out", "1ST": "1st half", "1ST_15MIN": "1st half",
          "1ST_30MIN": "1st half", "BREAK1": "Half-time", "2ND": "2nd half", "2ND_15MIN": "2nd half",
          "2ND_30MIN": "2nd half", "END": "Full time"}


def _st(svc):
    if not hasattr(svc, "pes_events"):
        svc.pes_events = {"feed": collections.deque(maxlen=200), "live": {}, "started": time.time(),
                          "present": set()}
        db = svc.accounts.db
        with svc.accounts.lock, db:
            db.execute("""CREATE TABLE IF NOT EXISTS pes_matches(
                id INTEGER PRIMARY KEY, room INTEGER, started REAL, ended REAL,
                home_pid INTEGER, away_pid INTEGER, home_name TEXT, away_name TEXT,
                home_score INTEGER, away_score INTEGER, reason TEXT)""")
            db.execute("CREATE TABLE IF NOT EXISTS pes_feed(id INTEGER PRIMARY KEY, t REAL, kind TEXT, text TEXT)")
            # the host can leave an unfair result (e.g. a dropped connection) out of the table
            cols = {r[1] for r in db.execute("PRAGMA table_info(pes_matches)")}
            if "voided" not in cols:
                db.execute("ALTER TABLE pes_matches ADD COLUMN voided INTEGER NOT NULL DEFAULT 0")
            # who left a match that ended early, and when (so the host can judge whether to count it)
            if "left_pid" not in cols:
                db.execute("ALTER TABLE pes_matches ADD COLUMN left_pid INTEGER")
                db.execute("ALTER TABLE pes_matches ADD COLUMN left_at REAL")
            rows = db.execute("SELECT t, kind, text FROM pes_feed ORDER BY id DESC LIMIT 200").fetchall()
        svc.pes_events["feed"].extend({"t": t, "kind": k, "text": x} for t, k, x in rows)
    return svc.pes_events


def name_of(svc, pid):
    try:
        r = svc.accounts.db.execute("SELECT name, username FROM pes_players WHERE pid=?", (pid,)).fetchone()
    except Exception:  # noqa: BLE001
        r = None
    return (r[0] or f"Player {pid}") if r else f"Player {pid}"


def feed(svc, text, kind="info"):
    """An activity line for the admin page; kept in SQLite (last 500) so it survives restarts."""
    item = {"t": time.time(), "kind": kind, "text": text}
    _st(svc)["feed"].appendleft(item)
    if os.environ.get("PES_CONSOLE") == "1":      # foreground: the host's window shows the same feed
        line = time.strftime("%H:%M", time.localtime(item["t"])) + "  " + text
        color = {"goal": "1;32", "result": "1", "warn": "33"}.get(kind)
        print(f"   \033[{color}m{line}\033[0m" if color and sys.stdout.isatty() else "   " + line, flush=True)
    try:
        db = svc.accounts.db
        with svc.accounts.lock, db:
            cur = db.execute("INSERT INTO pes_feed(t, kind, text) VALUES(?,?,?)", (item["t"], kind, text))
            if cur.lastrowid % 50 == 0:
                db.execute("DELETE FROM pes_feed WHERE id <= ?", (cur.lastrowid - 500,))
    except Exception:  # noqa: BLE001
        log.exception("saving an activity line")


def _room_of(svc, pid):
    from . import pesmatch
    return pesmatch._room_of(svc, pid)


def _live(svc, rid, room):
    lv = _st(svc)["live"]
    if rid not in lv:
        home, away = room["owner"], next((p for p in room["members"] if p != room["owner"]), None)
        for old_rid, old in list(lv.items()):        # a new pairing replaces an unfinished one
            if {old["home"], old["away"]} & {home, away} and old["phase"] != "Full time":
                lv.pop(old_rid)
        lv[rid] = {"room": rid, "home": home, "away": away, "home_name": name_of(svc, home),
                   "away_name": name_of(svc, away) if away else "", "phase": "Pre-match", "raw_phase": "",
                   "home_score": 0, "away_score": 0, "started": time.time(), "kickoff": None, "saved": False}
    return lv[rid]


def _note_left(svc, lv, pid):
    """A player left a match after kick-off: remember it for the result that may still arrive."""
    if lv.get("kickoff"):
        _st(svc).setdefault("leavers", {})[pid] = {"t": time.time(), "kickoff": lv["kickoff"], "pair": {lv["home"], lv["away"]}}


def _leaver(svc, home, away):
    now = time.time()
    for pid, x in list(_st(svc).get("leavers", {}).items()):
        if now - x["t"] > 900:
            _st(svc)["leavers"].pop(pid, None)
        elif pid in (home, away) and x["pair"] == {home, away}:
            return pid, x
    return None, None


def observe(svc, sess, msgid, req):
    """Called for every decoded client command after it was handled."""
    pid = getattr(sess, "pes_pid", None)
    if msgid == "CMD_LOGIN" and pid is not None:
        sess.connected_at = time.time()
        present = _st(svc)["present"]
        if pid not in present:            # the game logs in twice (ACCOUNT, then MENU): announce once
            present.add(pid)
            feed(svc, f"{name_of(svc, pid)} came online", "login")
        return
    if msgid == "CMD_CREATE_PLAYER" and pid is not None:
        feed(svc, f"New player: {name_of(svc, pid)}", "login")
        return
    if msgid == "CMD_START_MATCHING" and pid is not None:
        feed(svc, f"{name_of(svc, pid)} is looking for a match", "search")
        return
    if pid is None:
        return
    rid, room = _room_of(svc, pid)
    if room is None:
        return
    lv = _live(svc, rid, room)
    side = "home" if pid == lv["home"] else "away"
    if msgid == "CMD_CHANGE_GAMEPHASE":
        raw = str(req.get("phase", ""))
        if raw and raw != lv["raw_phase"] and side == "home":        # both clients report; log once
            lv["raw_phase"], lv["phase"] = raw, PHASES.get(raw, raw.title())
            if raw == "1ST" and not lv["kickoff"]:
                lv["kickoff"] = time.time()
                feed(svc, f"Kick-off: {lv['home_name']} vs {lv['away_name']}", "match")
            elif raw == "BREAK1":
                feed(svc, f"Half-time: {lv['home_name']} {lv['home_score']}–{lv['away_score']} {lv['away_name']}", "match")
    elif msgid == "CMD_ADD_SCORE":
        # goals arrive as CMD_ADD_SCORE {goal_side, goal_player_pid, gtimer, ...} from BOTH games, with
        # the same game clock; count each (gtimer, side) once. (There is no CMD_ADD_GOAL in the game.)
        scorer = str(req.get("goal_side") or "").upper()
        key = (req.get("gtimer"), scorer)
        seen = lv.setdefault("goals_seen", [])
        if scorer in ("HOME", "AWAY") and key not in seen:
            seen.append(key)
            lv[scorer.lower() + "_score"] += 1
            feed(svc, f"GOAL! {lv['home_name']} {lv['home_score']}–{lv['away_score']} {lv['away_name']}", "goal")
    elif msgid == "CMD_SEND_GAME_RESULT":
        hs, as_ = req.get("home_score"), req.get("away_score")
        if isinstance(hs, int) and isinstance(as_, int):
            lv["home_score"], lv["away_score"] = hs, as_
        lv["phase"] = "Full time"
        if not lv["saved"]:
            lv["saved"] = True
            reason = str(req.get("abnormalend_reason", "NOERR"))
            left_pid, lx = _leaver(svc, lv["home"], lv["away"]) if reason != "NOERR" else (None, None)
            db = svc.accounts.db
            with svc.accounts.lock, db:
                db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name,"
                           " home_score, away_score, reason, left_pid, left_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (rid, lv["kickoff"] or (lx and lx["kickoff"]) or lv["started"], time.time(), lv["home"], lv["away"],
                            lv["home_name"], lv["away_name"], lv["home_score"], lv["away_score"],
                            "" if reason == "NOERR" else reason, left_pid, lx and lx["t"]))
            feed(svc, f"Full time: {lv['home_name']} {lv['home_score']}–{lv['away_score']} {lv['away_name']}", "result")
    elif msgid == "CMD_LEAVE_ROOM":
        lv.setdefault("left", set()).add(pid)
        if lv["phase"] != "Full time":
            if not lv["kickoff"]:
                feed(svc, f"Match cancelled before kick-off: {name_of(svc, pid)} left", "warn")
            else:
                _note_left(svc, lv, pid)
                feed(svc, f"Match ended early: {name_of(svc, pid)} left", "warn")
            _st(svc)["live"].pop(rid, None)
        elif lv["left"] >= {lv["home"], lv["away"]}:
            _st(svc)["live"].pop(rid, None)


def disconnected(svc, sess):
    pid = getattr(sess, "pes_pid", None)
    if pid is None or not getattr(sess, "connected_at", None) or svc.online.get(pid) is not sess:
        return                            # an older connection of a player who is still online
    _st(svc)["present"].discard(pid)
    feed(svc, f"{name_of(svc, pid)} went offline", "logout")
    try:                                  # "last seen" means when they left, not when they signed in
        db = svc.accounts.db
        with svc.accounts.lock, db:
            db.execute("UPDATE pes_players SET last_login=? WHERE pid=?", (time.time(), pid))
    except Exception:  # noqa: BLE001
        pass
    for rid, lv in list(_st(svc)["live"].items()):
        if pid in (lv["home"], lv["away"]) and lv["phase"] != "Full time":
            _note_left(svc, lv, pid)
            feed(svc, f"Match ended early: {name_of(svc, pid)} disconnected", "warn")
            _st(svc)["live"].pop(rid, None)


def _week(db, now):
    """The last 7 days for the Highlights card, over every match (not just the latest few):
    most wins and most goals, with everyone tied at the top."""
    wins, goals, names = {}, {}, {}
    for hp, ap, hn, an, hs, as_ in db.execute("SELECT home_pid, away_pid, home_name, away_name, home_score, away_score"
                                              " FROM pes_matches WHERE ended >= ? AND voided = 0", (now - 7 * 86400,)):
        if hs is None or as_ is None:
            continue
        for pid, name, f, a in ((hp, hn, hs, as_), (ap, an, as_, hs)):
            if pid is None:
                continue
            names[pid] = name
            goals[pid] = goals.get(pid, 0) + f
            wins[pid] = wins.get(pid, 0) + (f > a)
    def top(m):
        best = max(m.values(), default=0)
        return {"n": best, "who": sorted(([p, names[p]] for p, v in m.items() if v == best), key=lambda x: x[1].lower())} if best else None
    return {"wins": top(wins), "goals": top(goals)}


def snapshot(svc):
    """Everything the admin page shows."""
    from . import pesmatch, pescmds
    st = _st(svc)
    mm = pesmatch._state(svc)
    queued = {p for p, _ in mm["queue"]}
    in_match = {p for lv in st["live"].values() for p in (lv["home"], lv["away"])}
    now = time.time()
    online = [{"pid": pid, "name": name_of(svc, pid), "ip": getattr(s, "peer_ip", ""),
               "since": getattr(s, "connected_at", now),
               "status": "In a match" if pid in in_match else "Searching" if pid in queued else "In menus",
               "searching_since": mm.get("qsince", {}).get(pid) if pid in queued else None}
              for pid, s in sorted(svc.online.items()) if isinstance(pid, int)]
    db = svc.accounts.db
    results = [dict(zip(("id", "started", "ended", "home_name", "away_name", "home_score", "away_score", "reason",
                         "home_pid", "away_pid", "voided", "left_pid", "left_at"), r))
               for r in db.execute("SELECT id, started, ended, home_name, away_name, home_score, away_score, reason,"
                                   " home_pid, away_pid, voided, left_pid, left_at"
                                   " FROM pes_matches ORDER BY id DESC LIMIT 100")]
    played_total, goals_total, abandoned = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(home_score + away_score), 0), COALESCE(SUM(COALESCE(reason, '') != ''), 0)"
        " FROM pes_matches").fetchone()
    # the last 30 days for the charts; the browser groups them by its own calendar days
    # (by kick-off time: "when friends play")
    history = [list(r) for r in db.execute("SELECT COALESCE(started, ended), COALESCE(home_score, 0) + COALESCE(away_score, 0),"
                                           " COALESCE(reason, '') != '' FROM pes_matches"
                                           " WHERE COALESCE(started, ended) > ? ORDER BY 1", (now - 31 * 86400,))]
    big = db.execute("SELECT home_name, away_name, home_score, away_score, ended FROM pes_matches"
                     " WHERE COALESCE(reason, '') = '' AND voided = 0 AND home_score IS NOT NULL AND away_score IS NOT NULL"
                     " AND home_score != away_score ORDER BY ABS(home_score - away_score) DESC, home_score + away_score DESC,"
                     " ended DESC LIMIT 1").fetchone()
    players = []
    try:
        pescmds._db(svc)                  # adds the rating columns to older databases
        rows = db.execute("SELECT pid, name, username, created, last_login, rating, win_streak FROM pes_players"
                          " ORDER BY last_login DESC").fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    for pid, name, username, created, last, rating, streak in rows:
        w = d = l = gf = ga = 0
        form, run, running = [], 0, True          # newest first while reading
        for hp, ap, hs, as_ in db.execute("SELECT home_pid, away_pid, home_score, away_score FROM pes_matches"
                                          " WHERE (home_pid=? OR away_pid=?) AND voided = 0 ORDER BY id DESC", (pid, pid)):
            mine, theirs = (hs, as_) if hp == pid else (as_, hs)
            if mine is None or theirs is None:
                continue
            w, d, l = w + (mine > theirs), d + (mine == theirs), l + (mine < theirs)
            gf, ga = gf + mine, ga + theirs
            res = "W" if mine > theirs else "L" if mine < theirs else "D"
            if len(form) < 5:
                form.append(res)
            running = running and res == "W"
            run += running
        players.append({"pid": pid, "name": name or f"Player {pid}", "created": created, "last_login": last,
                        "online": pid in svc.online, "played": w + d + l, "won": w, "drawn": d, "lost": l,
                        "gf": gf, "ga": ga, "rating": rating, "streak": streak,
                        "form": form[::-1], "run": run})
    live = [{k: v for k, v in lv.items() if k not in ("left", "saved", "goals_seen")} for lv in st["live"].values()]
    return {"now": now, "uptime": now - st["started"], "online": online, "searching": len(queued),
            "live": live, "results": results, "played_total": played_total, "players": players,
            "week": _week(db, now),
            "stats": {"goals": goals_total, "abandoned": abandoned, "history": history,
                      "biggest": dict(zip(("home_name", "away_name", "home_score", "away_score", "ended"), big)) if big else None},
            "feed": list(st["feed"])[:200]}


def player_detail(svc, pid):
    """One player for the admin page's side panel: every match they played (all time) and their activity lines."""
    from . import pescmds
    db = pescmds._db(svc)
    r = db.execute("SELECT pid, name, created, last_login, rating, win_streak FROM pes_players WHERE pid=?", (pid,)).fetchone()
    if r is None:
        return None
    _st(svc)
    keys = ("id", "started", "ended", "home_pid", "away_pid", "home_name", "away_name", "home_score", "away_score", "reason", "voided", "left_pid", "left_at")
    matches = [dict(zip(keys, m)) for m in db.execute(
        "SELECT " + ", ".join(keys) + " FROM pes_matches WHERE home_pid=? OR away_pid=? ORDER BY id DESC", (pid, pid))]
    name = r[1] or f"Player {pid}"
    sess = svc.online.get(pid)
    return {"pid": pid, "name": name, "created": r[2], "last_login": r[3], "rating": r[4], "streak": r[5],
            "online": sess is not None, "ip": getattr(sess, "peer_ip", "") if sess else "",
            "since": getattr(sess, "connected_at", None) if sess else None, "matches": matches,
            "feed": [f for f in _st(svc)["feed"] if name in f["text"]][:40]}


def set_counted(svc, mid, counted):
    """Count a finished match in the league table again, or leave it out (it stays in Results, marked).
    Returns the match's names and score, or None when there's no such match."""
    db = svc.accounts.db
    _st(svc)
    with svc.accounts.lock, db:
        r = db.execute("SELECT home_name, away_name, home_score, away_score FROM pes_matches WHERE id=?", (int(mid),)).fetchone()
        if r is None:
            return None
        db.execute("UPDATE pes_matches SET voided=? WHERE id=?", (0 if counted else 1, int(mid)))
    score = f"{r[0]} {r[2] if r[2] is not None else '?'}–{r[3] if r[3] is not None else '?'} {r[1]}"
    feed(svc, f"{'The admin counted a result again' if counted else 'The admin left a result out of the table'}: {score}", "info")
    return {"home_name": r[0], "away_name": r[1], "home_score": r[2], "away_score": r[3]}


def results_page(svc, q="", since=0, limit=25, until=None, early=False):
    """Finished matches for the admin page's Results list: newest first, optionally only one player's
    (name contains q) or only since a time. Returns (rows, how many match in all)."""
    q = str(q or "").strip().lower()
    where, args = ["ended >= ?"], [float(since or 0)]
    if early:
        where.append("COALESCE(reason, '') != ''")
    if until:
        where.append("ended < ?"); args.append(float(until))
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(LOWER(home_name) LIKE ? ESCAPE '\\' OR LOWER(away_name) LIKE ? ESCAPE '\\')")
        args += [like, like]
    w = " WHERE " + " AND ".join(where)
    db = svc.accounts.db
    _st(svc)
    keys = ("id", "started", "ended", "home_name", "away_name", "home_score", "away_score", "reason", "home_pid", "away_pid", "voided", "left_pid", "left_at")
    rows = [dict(zip(keys, r)) for r in db.execute("SELECT " + ", ".join(keys) + " FROM pes_matches" + w +
                                                   " ORDER BY id DESC LIMIT ?", (*args, max(1, min(int(limit), 1000))))]
    return rows, db.execute("SELECT COUNT(*) FROM pes_matches" + w, args).fetchone()[0]
