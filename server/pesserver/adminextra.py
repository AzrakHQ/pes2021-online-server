"""What the admin page shows about the game modes and the newer server parts, on top of events.snapshot:

- where each online friend is (searching in which mode, in a Friendly Match Lobby room) and whether their mod is
  current (mod 0.5.0+ says "coop1", 0.4.0+ "math1" on its id line; gate.py);
- the mode of each live match and result (Quick Match, Online Divisions, Matchday, a lobby room, Online Co-op);
- why two friends who are both searching can't be paired (different game data, an older mod, a co-op block);
- the courtesy rating (pesmanner), Online Divisions (pesdivisions) and Matchday (pesmatchday) per player;
- the match relay (turn.py) and the host's Live Update (liveupdate.py).

Everything here reads; the only writes are the admin's "clear courtesy record" and the cleanup of a deleted player.
"""
import logging, os, time

log = logging.getLogger("pesserver.adminextra")

QUICK, DIVISIONS, MATCHDAY, LOBBY, COOP = "Quick Match", "Online Divisions", "Matchday", "Friendly Match Lobby", "Online Co-op"
MODES = {"OC_QUICK": QUICK, "OC_RANK": DIVISIONS, "MATCHDAY": MATCHDAY}


def mode_of_room(room):
    """The game mode of a pesmatch room record."""
    if not room:
        return QUICK
    if room.get("coop"):
        return COOP
    if room.get("lobby_room"):
        return LOBBY
    return MODES.get(room.get("mode") or "OC_QUICK", QUICK)


def mod_of(sess):
    """current: mod 0.5.0+ (co-op fix); no-coop: 0.4.x (plays Quick Match, co-op can't connect);
    old: before 0.4.0 (no same-math functions: can't be paired with friends on a newer mod)."""
    feats = getattr(sess, "mod_features", None) or ()
    if not getattr(sess, "mod_id", None) or "math1" not in feats:
        return "old"
    return "current" if "coop1" in feats else "no-coop"


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - an extra must never break the admin page
        log.exception("admin extra %s", getattr(fn, "__name__", "?"))
        return default


# ---- the snapshot -----------------------------------------------------------------------------------
def augment(svc, snap):
    """Add the mode-related fields to events.snapshot's result (in place)."""
    from . import pesmatch
    mm = pesmatch._state(svc)
    searching = _safe(lambda: _searching(svc, mm), {})
    in_match = {p for lv in snap["live"] if lv.get("phase") != "Full time" for p in (lv.get("home"), lv.get("away"))}
    rooms = _safe(lambda: _rooms(svc, in_match), [])
    in_room = {pid: r for r in rooms for pid in r["members"]}
    qsince = mm.get("qsince", {})
    for o in snap["online"]:
        pid = o["pid"]
        o["mod"] = mod_of(svc.online.get(pid))
        o["searching_for"] = searching.get(pid) if o["status"] != "In a match" else None
        if o["searching_for"]:
            o["status"] = "Searching"
            o["searching_since"] = o.get("searching_since") or qsince.get(pid)
        elif pid in in_room and o["status"] == "In menus":
            o["status"] = "In a match room"
        o["room"] = in_room[pid]["name"] if pid in in_room else None
    snap["searching"] = len([o for o in snap["online"] if o["searching_for"]])
    for lv in snap["live"]:
        lv["mode"] = mode_of_room(mm["rooms"].get(lv.get("room")))
    snap["rooms"] = rooms
    snap["problems"] = _safe(lambda: _problems(svc, mm), [])
    _safe(lambda: _player_extras(svc, snap["players"]), None)
    snap["matchday"] = _safe(lambda: _matchday(svc), None)
    return snap


def _searching(svc, mm):
    from . import pesmatch, pescoop
    out = {p: MODES.get(pesmatch._matching_type(s), QUICK) for p, s in mm["queue"]}
    out.update({p: COOP for p, _s, _t in pescoop._queue(svc)})
    return out


ROOM_STATES = {"WAITING": "In the room", "SETENV": "Starting a match"}


def _rooms(svc, in_match):
    from . import pesrooms
    return [{**r, "state": "Playing" if set(r["members"]) & in_match else ROOM_STATES.get(r["state"], r["state"].title())}
            for r in pesrooms.snapshot(svc)]


def _problems(svc, mm):
    """Friends who are searching at the same time but can't be paired, and why."""
    from . import pesmatch, pescoop
    out, q = [], list(mm["queue"])
    for i in range(len(q)):
        for j in range(i + 1, len(q)):
            (a, sa), (b, sb) = q[i], q[j]
            if sa is None or sb is None or pesmatch._matching_type(sa) != pesmatch._matching_type(sb):
                continue
            if not pesmatch._compatible(sa, sb):
                if pesmatch._same_math(sa) != pesmatch._same_math(sb):
                    out.append({"kind": "mod", "pids": [a, b], "old": a if pesmatch._same_math(sb) else b})
                else:
                    out.append({"kind": "data", "pids": [a, b]})
    cq = list(pescoop._queue(svc))
    for p, s, _t in cq:
        if s is not None and not pescoop._fixed(s):
            out.append({"kind": "coop_mod", "pids": [p]})
    for i in range(len(cq)):
        for j in range(i + 1, len(cq)):
            if pescoop.blocked(svc, cq[i][0], cq[j][0]):
                out.append({"kind": "coop_blocked", "pids": [cq[i][0], cq[j][0]]})
    return out


def _courtesy_all(svc, db):
    from . import pesmanner
    pesmanner._setup(db)
    seen = {}
    for pid, early in db.execute("SELECT pid, left_early FROM pes_manner ORDER BY id DESC"):
        r = seen.setdefault(pid, [])
        if len(r) < pesmanner.RECENT:
            r.append(early)
    return {pid: {"grade": pesmanner.grade_for(sum(r)), "quits": sum(r), "matches": len(r)} for pid, r in seen.items()}


def _divisions_all(svc, db):
    from . import pesdivisions
    pesdivisions._setup(db)
    cur = {(p, s): (n, pts or 0) for p, s, n, pts in db.execute(
        "SELECT pid, season, COUNT(*), SUM(points) FROM pes_division_match GROUP BY pid, season")}
    out = {}
    for pid, div, season, w, d, l in db.execute("SELECT pid, division, season, wins, draws, losses FROM pes_division"):
        n, pts = cur.get((pid, season), (0, 0))
        if not (w or d or l or n or season > 1):
            continue                                    # opened the mode once, never played it
        up, up2, down = pesdivisions.thresholds(div)
        out[pid] = {"div": div, "season": season, "played": n, "points": pts, "of": pesdivisions.SEASON,
                    "wins": w, "draws": d, "losses": l, "up": up, "down": down, "top": div <= 1,
                    "bottom": div >= pesdivisions.DIVISIONS}
    return out


def _player_extras(svc, players):
    from . import pescmds
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        court, divs = _courtesy_all(svc, db), _divisions_all(svc, db)
    for p in players:
        p["courtesy"] = court.get(p["pid"])
        p["division"] = divs.get(p["pid"])


def _matchday(svc):
    from . import pescmds, pesmatchday
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        pesmatchday._setup(db)
        rows = db.execute("SELECT pid, grp, team_id, points, matches FROM pes_matchday WHERE compe_id=?",
                          (pesmatchday.COMPE_ID,)).fetchall()
    if not rows:
        return None
    sides = [{"key": k, "name": name, "points": sum(r[3] for r in rows if r[1] == k),
              "players": sum(1 for r in rows if r[1] == k)} for k, (name, _teams) in pesmatchday.SIDES.items()]
    return {"name": pesmatchday.NAME, "sides": sides,
            "entries": sorted(({"pid": pid, "side": pesmatchday.SIDES.get(g, (g,))[0], "points": pts, "matches": n}
                               for pid, g, _t, pts, n in rows), key=lambda e: (-e["points"], -e["matches"]))}


# ---- one player (the side panel) --------------------------------------------------------------------
REASONS = {"GIVEUP": "gave up", "EXIT": "quit from the pause menu", "NO_OPERATION": "stopped playing (no input)",
           "DISCONNECTED": "game closed or connection lost"}


def player(svc, pid):
    """Extra fields for events.player_detail."""
    from . import pescmds, pesmanner, pesmatchday, pescoop
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        pesmanner._setup(db)
        rows = db.execute("SELECT left_early, reason, t FROM pes_manner WHERE pid=? ORDER BY id DESC LIMIT ?",
                          (pid, pesmanner.RECENT)).fetchall()
        divs = _divisions_all(svc, db)
        pesmatchday._setup(db)
        md = db.execute("SELECT grp, points, matches FROM pes_matchday WHERE pid=? AND compe_id=?",
                        (pid, pesmatchday.COMPE_ID)).fetchone()
        pescoop._setup(db)
        blocked_by = [r[0] for r in db.execute("SELECT pid FROM pes_coop_reject WHERE target=?", (pid,))]
    quits = sum(r[0] for r in rows)
    courtesy = {"grade": pesmanner.grade_for(quits), "quits": quits, "matches": len(rows),
                "early": [{"t": t, "why": REASONS.get(why, why.lower())} for early, why, t in rows if early]} if rows else None
    sess = svc.online.get(pid)
    return {"courtesy": courtesy, "division": divs.get(pid),
            "matchday": {"side": pesmatchday.SIDES.get(md[0], (md[0],))[0], "points": md[1], "matches": md[2]} if md else None,
            "coop_blocks": pescoop.rejects(svc, pid), "coop_blocked_by": blocked_by,
            "mod": mod_of(sess) if sess is not None else None}


def clear_courtesy(svc, pid):
    from . import pesmanner
    pesmanner.forget(svc, pid)


def forget(svc, pid):
    """A deleted player: drop their Online Divisions and Matchday rows too (a new player may get the same number)."""
    from . import pescmds, pesdivisions, pesmatchday
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        pesdivisions._setup(db)
        pesmatchday._setup(db)
        for t in ("pes_division", "pes_division_match", "pes_matchday", "pes_matchday_match"):
            db.execute(f"DELETE FROM {t} WHERE pid=?", (pid,))


# ---- the server card --------------------------------------------------------------------------------
_SENT = {}                                  # data dir -> {(ip, file name)}: Live Update downloads this run


def server(svc):
    from . import turn, liveupdate
    srv = turn.STATE.get("server")
    relay = {"on": srv is not None, "in_use": len(getattr(srv, "allocations", {}) or {}) if srv else 0,
             "enabled": turn.enabled()}
    lu = {"folder": liveupdate.folder(svc)}
    cur = _safe(lambda: liveupdate.current(svc), None)
    if cur:
        path, info = cur
        v = info["dlc_version"]
        lu.update(name=info["name"], size=info["size"], version=f"{v[:-2]}.{v[-2:]}" if len(v) >= 3 else v,
                  since=_safe(lambda: os.path.getmtime(path), None),
                  sent=len({ip for ip, n in _SENT.get(liveupdate.folder(svc), set()) if n == info["name"]}))
    return {"relay": relay, "liveupdate": lu}


def note_liveupdate(svc, ip, name):
    """A game downloaded the Live Update: one activity line per game and file."""
    from . import liveupdate, events
    sent = _SENT.setdefault(liveupdate.folder(svc), set())
    if (ip, name) in sent:
        return
    sent.add((ip, name))
    who = next((events.name_of(svc, pid) for pid, s in list(svc.online.items())
                if isinstance(pid, int) and getattr(s, "peer_ip", None) == ip), None)
    events.feed(svc, f"{who}'s game downloaded the Live Update ({name})" if who
                else f"A game downloaded the Live Update ({name})", "info")
