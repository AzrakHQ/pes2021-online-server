"""Live server activity for the admin page: who is online, matches in progress, finished results and
an activity feed. Fed by pescmds.dispatch (observe) and gate disconnects; results are kept in SQLite."""
import collections, logging, time

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
            db = svc.accounts.db
            with svc.accounts.lock, db:
                db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name,"
                           " home_score, away_score, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (rid, lv["kickoff"] or lv["started"], time.time(), lv["home"], lv["away"],
                            lv["home_name"], lv["away_name"], lv["home_score"], lv["away_score"],
                            "" if reason == "NOERR" else reason))
            feed(svc, f"Full time: {lv['home_name']} {lv['home_score']}–{lv['away_score']} {lv['away_name']}", "result")
    elif msgid == "CMD_LEAVE_ROOM":
        lv.setdefault("left", set()).add(pid)
        if lv["phase"] != "Full time":
            if not lv["kickoff"]:
                feed(svc, f"Match cancelled before kick-off: {name_of(svc, pid)} left", "warn")
            else:
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
    for rid, lv in list(_st(svc)["live"].items()):
        if pid in (lv["home"], lv["away"]) and lv["phase"] != "Full time":
            feed(svc, f"Match ended early: {name_of(svc, pid)} disconnected", "warn")
            _st(svc)["live"].pop(rid, None)


def snapshot(svc):
    """Everything the admin page shows."""
    from . import pesmatch
    st = _st(svc)
    mm = pesmatch._state(svc)
    queued = {p for p, _ in mm["queue"]}
    in_match = {p for lv in st["live"].values() for p in (lv["home"], lv["away"])}
    now = time.time()
    online = [{"pid": pid, "name": name_of(svc, pid), "ip": getattr(s, "peer_ip", ""),
               "since": getattr(s, "connected_at", now),
               "status": "In a match" if pid in in_match else "Searching" if pid in queued else "In menus"}
              for pid, s in sorted(svc.online.items()) if isinstance(pid, int)]
    db = svc.accounts.db
    results = [dict(zip(("id", "started", "ended", "home_name", "away_name", "home_score", "away_score", "reason"), r))
               for r in db.execute("SELECT id, started, ended, home_name, away_name, home_score, away_score, reason"
                                   " FROM pes_matches ORDER BY id DESC LIMIT 50")]
    played_total = db.execute("SELECT COUNT(*) FROM pes_matches").fetchone()[0]
    players = []
    try:
        rows = db.execute("SELECT pid, name, username, created, last_login FROM pes_players ORDER BY last_login DESC").fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    for pid, name, username, created, last in rows:
        w = d = l = gf = ga = 0
        for hp, ap, hs, as_ in db.execute("SELECT home_pid, away_pid, home_score, away_score FROM pes_matches"
                                          " WHERE home_pid=? OR away_pid=?", (pid, pid)):
            mine, theirs = (hs, as_) if hp == pid else (as_, hs)
            if mine is None or theirs is None:
                continue
            w, d, l = w + (mine > theirs), d + (mine == theirs), l + (mine < theirs)
            gf, ga = gf + mine, ga + theirs
        players.append({"pid": pid, "name": name or f"Player {pid}", "created": created, "last_login": last,
                        "online": pid in svc.online, "played": w + d + l, "won": w, "drawn": d, "lost": l,
                        "gf": gf, "ga": ga})
    live = [{k: v for k, v in lv.items() if k not in ("left", "saved", "goals_seen")} for lv in st["live"].values()]
    return {"now": now, "uptime": now - st["started"], "online": online, "searching": len(queued),
            "live": live, "results": results, "played_total": played_total, "players": players,
            "feed": list(st["feed"])[:60]}
