"""Online Tournament: friends' competitions in the game's own PES League screens (eFootball -> Online Tournament).

How the game runs a competition (flow files Online/PesLg/*, notes/re/functions/tourney.md):
- Competition list -> details -> team select -> JOIN_PESLEAGUE_COMPE {compe_id, team_id} ->
  GET_PESLEAGUE_1VS1_MAINMENU_INFO -> the competition's 1v1 menu (match_type PES_LEAGUE_MATCH_1VS1).
- "Match" in that menu goes to the Quick Match matchmaking flow (Online/Quick/QuickProcessInit); pesmatch pairs
  two players of the same competition. Results count 3 points for a win, 1 for a draw.
- The "Match Replays" competition (pesreplay, compe_id 1) is listed next to these and keeps its lobby path.

Competitions live in the server database (pes_tourney). With none running, a monthly "Friends Cup" is made.
"""
import json, logging, time

import msgpack

from .pescmds import NOERR, cmd, _db, _player
from . import pesmatch, pesreplay

log = logging.getLogger("pesserver.tourney")
POINTS = {"WIN": 3, "DRAW": 1, "LOSE": 0}
FIRST_ID = 100                      # compe_id 1 is the Match Replays competition
MATCH_1VS1 = "PES_LEAGUE_MATCH_1VS1"
TEAMS = [8, 9, 45, 14, 50, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13]   # national teams (pesdb.py), editable per competition


def _pack(o):
    return msgpack.packb(o, use_bin_type=True)


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_tourney (compe_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                  match_type TEXT NOT NULL, start_date INTEGER NOT NULL, end_date INTEGER NOT NULL,
                  teams TEXT NOT NULL, info TEXT NOT NULL DEFAULT '', auto INTEGER NOT NULL DEFAULT 0,
                  created REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pes_tourney_entry (compe_id INTEGER, pid INTEGER, team_id INTEGER,
                  points INTEGER NOT NULL DEFAULT 0, win INTEGER NOT NULL DEFAULT 0, draw INTEGER NOT NULL DEFAULT 0,
                  lose INTEGER NOT NULL DEFAULT 0, goals_for INTEGER NOT NULL DEFAULT 0,
                  goals_against INTEGER NOT NULL DEFAULT 0, joined REAL NOT NULL, PRIMARY KEY (compe_id, pid))""")
    if "rules" not in {r[1] for r in db.execute("PRAGMA table_info(pes_tourney)")}:
        db.execute("ALTER TABLE pes_tourney ADD COLUMN rules TEXT NOT NULL DEFAULT '{}'")
    db.execute("CREATE TABLE IF NOT EXISTS pes_tourney_cfg (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pes_tourney_seen (pid INTEGER, compe_id INTEGER, PRIMARY KEY (pid, compe_id))")
    db.execute("""CREATE TABLE IF NOT EXISTS pes_tourney_match (compe_id INTEGER, match_id TEXT, pid INTEGER,
                  opp INTEGER, my_team INTEGER, opp_team INTEGER, my_goals INTEGER, opp_goals INTEGER,
                  result TEXT, t REAL, PRIMARY KEY (compe_id, match_id, pid))""")


def _month(now):
    t = time.localtime(now)
    y, m = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    start = int(time.mktime((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, -1)))
    end = int(time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)))       # local midnight, also across a clock change
    return start, end, time.strftime("%B %Y", t)


def competitions(svc, now=None):
    """All competitions, newest first, as dicts. Makes this month's Friends Cup if nothing is running."""
    now = now or time.time()
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        start, end, label = _month(now)
        cfg = dict(db.execute("SELECT key, value FROM pes_tourney_cfg").fetchall())
        if (cfg.get("monthly_cup", "on") == "on" and str(start) not in cfg.get("skipped", "").split()
                and not db.execute("SELECT 1 FROM pes_tourney WHERE auto=?", (start,)).fetchone()
                and not db.execute("SELECT 1 FROM pes_tourney WHERE end_date>?", (now,)).fetchone()):
            # nothing running or planned, and no cup yet this month: this month's Friends Cup
            cid = max(FIRST_ID, (db.execute("SELECT MAX(compe_id) FROM pes_tourney").fetchone()[0] or 0) + 1)
            db.execute("INSERT INTO pes_tourney (compe_id, name, match_type, start_date, end_date, teams, auto, "
                       "created) VALUES (?,?,?,?,?,?,?,?)",
                       (cid, f"Friends Cup {label}", MATCH_1VS1, start, end, json.dumps(TEAMS), start, now))
            log.info("TOURNEY new competition %d: Friends Cup %s", cid, label)
        rows = db.execute("SELECT compe_id, name, match_type, start_date, end_date, teams, info, rules FROM pes_tourney "
                          "ORDER BY start_date DESC, compe_id DESC").fetchall()
    return [{"compe_id": r[0], "name": r[1], "match_type": r[2], "start": r[3], "end": r[4],
             "teams": json.loads(r[5]), "info": r[6], "rules": json.loads(r[7] or "{}")} for r in rows]


# compe_regulation, at the top level of GET_PESLEAGUE_COMPE_INFO (layout from the game's parser, 2026-11-08):
# match_time NUM, condition STR (CONDITION_WORST..CONDITION_BEST), ranking_match_num NUM, trade_num NUM,
# extra_time STR, guest STR (GUEST / GUEST_NONE / PLATFORM_GUEST), is_equalization / is_3member_coop /
# is_ex_substitution "YES"/"NO". match_time is the match length in real minutes (live 2026-11-08: 5 gave a
# 2.5-minute half); without it the game plays a match of about 2 minutes, so it is always sent.
MATCH_MINUTES = 10                  # like Quick Match
def regulation(c):
    r = c.get("rules") or {}
    # is_equalization NO: with YES the game tells players their ratings are evened out and asks the server for
    # the evened-out stats (GET_PLAYER_PARAMETERLIST is_equalization YES), which this server does not make; teams
    # play at their real strength, as in Quick Match, and the game says so (no notice; live 2026-11-08).
    return {"match_time": int(r.get("match_minutes") or MATCH_MINUTES), "is_equalization": "NO"}


def get(svc, compe_id):
    try:
        cid = int(compe_id)
    except (TypeError, ValueError):
        return None
    return next((c for c in competitions(svc) if c["compe_id"] == cid), None)


def _entry_num(svc, cid):
    db = _db(svc)
    with svc.accounts.lock, db:
        return db.execute("SELECT COUNT(*) FROM pes_tourney_entry WHERE compe_id=?", (cid,)).fetchone()[0]


def entry(svc, cid, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT team_id, points, win, draw, lose, goals_for, goals_against FROM pes_tourney_entry "
                       "WHERE compe_id=? AND pid=?", (cid, pid)).fetchone()
    return dict(zip(("team_id", "points", "win", "draw", "lose", "gf", "ga"), r)) if r else None


def _fields(svc, c, pid=None):
    now = int(time.time())
    ended = c["end"] <= now
    joined = pid is not None and entry(svc, c["compe_id"], pid) is not None
    return {"compe_id": c["compe_id"], "compe_db_id": c["compe_id"], "compe_name": c["name"], "is_myclub": "NO",
            "match_type": c["match_type"], "start_date": c["start"], "end_date": c["end"],
            "match_end_date": c["end"], "entry_limit_date": c["end"], "entry_user_limit": "NO",
            "entry_user_max": 1000, "entry_user_num": _entry_num(svc, c["compe_id"]), "entry_limit_division": "NO",
            "entry_limit_division_min": 1, "entry_limit_division_max": 10, "is_end": "YES" if ended else "NO",
            "is_entry": "YES" if joined else "NO", "is_gm_account": "NO", "is_no_check": "NO", "mode": "PESLEAGUE"}


def _pid(sess, req):
    return getattr(sess, "pes_pid", None) or req.get("pid")


# ---- the competition screens --------------------------------------------------------------------------------
@cmd("CMD_GET_PESLEAGUE_COMPE_LIST")
def compe_list(svc, sess, req):
    pid = _pid(sess, req)
    now = time.time()
    past = str(req.get("is_end") or "").upper() == "YES"          # the past-competitions list
    items = [{**_fields(svc, c, pid), "banner_file_info": _pack({}), "list_banner_file_info": _pack({})}
             for c in competitions(svc) if (c["end"] <= now) == past]
    if not past:
        items.append({**pesreplay._compe(), "banner_file_info": _pack({}), "list_banner_file_info": _pack({})})
    return {"result": NOERR, "compe_list": [_pack(x) for x in items], "list_num": len(items),
            "list_max": len(items), "is_end": "YES"}


@cmd("CMD_GET_PESLEAGUE_COMPE_INFO")
def compe_info(svc, sess, req):
    c = get(svc, req.get("compe_id"))
    if c is None:
        return pesreplay.peslg_compe_info(svc, sess, req)
    f = _fields(svc, c, _pid(sess, req))
    f["is_exist_overview_info"] = "YES"          # "Competition Outline" in the details: GET_…_OVERVIEW_INFO
    # the fields must also be inside compe_data, or the game takes the default competition type (live 2026-11-06)
    out = {"result": NOERR, **f, "eula_url": "", "entry_compe": f["is_entry"],
           "compe_data": _pack({**f, "eula_url": "", "entry_compe": f["is_entry"]})}
    out["compe_regulation"] = _pack(regulation(c))
    return out


@cmd("CMD_GET_PESLEAGUE_COMPE_OVERVIEW_INFO")
def compe_overview(svc, sess, req):
    """The host's own text about the competition (rules, prizes, dates), set with the admin API (info)."""
    c = get(svc, req.get("compe_id"))
    return {"result": NOERR, "overview_info": outline(c) if c else ""}


def outline(c):
    """The host's text, or one made from the competition's rules."""
    if c.get("info"):
        return c["info"]
    minutes = int((c.get("rules") or {}).get("match_minutes") or MATCH_MINUTES)
    end = time.strftime("%d %B %Y, %H:%M", time.localtime(c["end"]))
    return (f"{c['name']}: play as many {minutes}-minute matches against your friends as you like before {end}.\n"
            "A win gives 3 points, a draw 1. The table is ordered by points, then goal difference, then goals "
            "scored.\nTeams play at their real strength. Leaving a match early counts as a 0-3 loss.")


@cmd("CMD_GET_PESLEAGUE_COMPE_TEAM_LIST")
def compe_team_list(svc, sess, req):
    c = get(svc, req.get("compe_id"))
    if c is None:
        return pesreplay.peslg_team_list(svc, sess, req)
    return {"result": NOERR, "team_list": c["teams"], "team_list_num": len(c["teams"])}


@cmd("CMD_JOIN_PESLEAGUE_COMPE")
def join_compe(svc, sess, req):
    c = get(svc, req.get("compe_id"))
    pid = _pid(sess, req)
    if c is None or pid is None:
        return {"result": NOERR}
    try:
        team = int(req.get("team_id") or 0)
    except (TypeError, ValueError):
        team = 0
    if team not in c["teams"]:
        team = c["teams"][0]
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        new = not db.execute("SELECT 1 FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (c["compe_id"], pid)).fetchone()
        # the game sends this again after every match (same team): only the first one is an entry
        db.execute("INSERT INTO pes_tourney_entry (compe_id, pid, team_id, joined) VALUES (?,?,?,?) "
                   "ON CONFLICT (compe_id, pid) DO UPDATE SET team_id=excluded.team_id", (c["compe_id"], pid, team, time.time()))
    sess.pes_compe = c["compe_id"]
    if new:
        log.info("TOURNEY pid %s joined %d with team %d", pid, c["compe_id"], team)
        from . import events
        events.feed(svc, f"{events.name_of(svc, pid)} entered {c['name']}", "info")
    return {"result": NOERR}


@cmd("CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO")
def mainmenu_1vs1(svc, sess, req):
    c = get(svc, req.get("compe_id"))
    if c is None:
        return pesreplay.peslg_1vs1(svc, sess, req)
    pid = _pid(sess, req)
    sess.pes_compe = c["compe_id"]
    e = entry(svc, c["compe_id"], pid) or {"team_id": c["teams"][0], "points": 0, "win": 0, "draw": 0, "lose": 0}
    return {"result": NOERR, "compe_end_time": c["end"], "draw_count": e["draw"], "lose_count": e["lose"],
            "match_num": e["win"] + e["draw"] + e["lose"], "matching_first_date": c["start"],
            # the "Matchmaking window closes in" clock is matching_first_date + matching_time (live 2026-11-08)
            "matching_interval": 0, "matching_time": c["end"] - c["start"], "point": e["points"],
            "rank_type": "NONE", "regulation_match_num": 0, "team_id": e["team_id"], "team_name": "",
            "win_count": e["win"]}


# ---- results ------------------------------------------------------------------------------------------------
def record(svc, room):
    """Called once per finished Online Tournament match (pesmatch, at the verdict or a forfeit).
    Returns {pid: (points before, points after)} (the same again on a repeat call), or {} for no competition."""
    cid = room.get("compe")
    c = get(svc, cid)
    if c is None:
        return {}
    out = {}
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home = room["owner"]
    away = next(p for p in room["members"] if p != home)
    teams = room.get("teams") or {}
    mid = room.get("match_id") or f"{int(room.get('t', time.time()))}-{home}-{away}"
    now = time.time()
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for me, opp, side in ((home, away, "HOME"), (away, home, "AWAY")):
            mine, theirs = (score["HOME"], score["AWAY"]) if side == "HOME" else (score["AWAY"], score["HOME"])
            res = "WIN" if mine > theirs else "LOSE" if mine < theirs else "DRAW"
            if not db.execute("SELECT 1 FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (cid, me)).fetchone():
                db.execute("INSERT INTO pes_tourney_entry (compe_id, pid, team_id, joined) VALUES (?,?,?,?)",
                           (cid, me, teams.get(me) or c["teams"][0], now))
            before = db.execute("SELECT points FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (cid, me)).fetchone()[0]
            cur = db.execute("INSERT OR IGNORE INTO pes_tourney_match (compe_id, match_id, pid, opp, my_team, opp_team, "
                             "my_goals, opp_goals, result, t) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (cid, mid, me, opp, teams.get(me) or 0, teams.get(opp) or 0, mine, theirs, res, now))
            if cur.rowcount:
                col = {"WIN": "win", "DRAW": "draw", "LOSE": "lose"}[res]
                db.execute(f"UPDATE pes_tourney_entry SET points=points+?, {col}={col}+1, goals_for=goals_for+?, "
                           "goals_against=goals_against+? WHERE compe_id=? AND pid=?",
                           (POINTS[res], mine, theirs, cid, me))
                log.info("TOURNEY %d: pid %s %s %d-%d (+%d)", cid, me, res, mine, theirs, POINTS[res])
                out[me] = (before, before + POINTS[res])
            else:
                out[me] = (before - POINTS[res], before)
    room["tourney_points"] = out
    return out


def standings(svc, cid):
    """[(rank, pid, name, team_id, points, win, draw, lose, gf, ga)] by points, goal difference, goals, fewer games."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT e.pid, p.name, e.team_id, e.points, e.win, e.draw, e.lose, e.goals_for, "
                          "e.goals_against FROM pes_tourney_entry e JOIN pes_players p ON p.pid=e.pid WHERE e.compe_id=? "
                          "ORDER BY e.points DESC, (e.goals_for-e.goals_against) DESC, e.goals_for DESC, "
                          "(e.win+e.draw+e.lose) ASC, e.joined ASC", (cid,)).fetchall()
    return [(i, *r) for i, r in enumerate(rows, 1)]


def forget(svc, pid):
    """A deleted player leaves every competition (their opponents keep their points)."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_tourney_entry WHERE pid=?", (pid,))
        db.execute("DELETE FROM pes_tourney_match WHERE pid=?", (pid,))


def _pname(name, pid):
    return _pack({"name": name or "", "xuid_one": "", "is_deleted": "NO",
                  "psn_user_id": _pack({"account_id": pid or 0, "online_id": "", "is_valid_cache": "YES"})})


def _streak(svc, cid, pid):
    """Current run of wins in this competition."""
    db = _db(svc)
    with svc.accounts.lock, db:
        n = 0
        for (res,) in db.execute("SELECT result FROM pes_tourney_match WHERE compe_id=? AND pid=? ORDER BY t DESC",
                                 (cid, pid)):
            if res != "WIN":
                break
            n += 1
    return n


def _rank_entry(svc, cid, row, me):
    rank, pid, name, team, pts, w, d, l, gf, ga = row
    # layout from the parser 0x141c1b2e0 (2026-11-08): every entry is a blob, the player's name in a nested blob
    return _pack({"pid": pid, "entry_name": name or "", "team_id": team, "clan_owner_name": _pname(name, pid),
                  "point": pts, "rank": rank, "win_count": w, "draw_count": d, "lose_count": l,
                  "match_num": w + d + l, "get_score": gf, "lose_score": ga, "diff_score": gf - ga,
                  "recode_date": int(time.time()), "continuous_win": _streak(svc, cid, pid),
                  "is_get_participation_right": "NO", "is_mydata": "YES" if pid == me else "NO", "rank_type": "NONE"})


@cmd("CMD_GET_PESLEAGUE_COMPE_RANKING")
def compe_ranking(svc, sess, req):
    """Rankings: every entered player by points (then goal difference, goals, fewer matches)."""
    c = get(svc, req.get("compe_id"))
    if c is None:
        return {"result": NOERR, "rank_list": [], "list_num": 0, "list_max": 0, "update_date": int(time.time())}
    try:
        start = max(0, int(req.get("start_index") or 0))
        num = max(1, min(100, int(req.get("view_num") or 100)))
    except (TypeError, ValueError):
        start, num = 0, 100
    rows = standings(svc, c["compe_id"])
    me = _pid(sess, req)
    page = [_rank_entry(svc, c["compe_id"], r, me) for r in rows[start:start + num]]
    return {"result": NOERR, "rank_list": page, "list_num": len(page), "list_max": len(rows),
            "update_date": int(time.time())}


@cmd("CMD_GET_PESLEAGUE_COMPE_RANKING_BYID")
def compe_ranking_byid(svc, sess, req):
    """The player's own line (the game asks for it with their pid)."""
    c = get(svc, req.get("compe_id"))
    rows = standings(svc, c["compe_id"]) if c else []
    me = _pid(sess, req)
    want = req.get("target_pid") or req.get("pid") or me
    page = [_rank_entry(svc, c["compe_id"], r, me) for r in rows if str(r[1]) == str(want)] if c else []
    return {"result": NOERR, "rank_list": page, "list_num": len(page), "list_max": len(rows),
            "update_date": int(time.time())}


def results(svc, pid, compe_id):
    """The player's matches in one competition, newest first (CMD_GET_GAMERESULTS game_res_type PESLEAGUE)."""
    from .pesresults import HISTORY, _entry
    c = get(svc, compe_id)
    if c is None:
        return []
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT m.t, m.pid, m.opp, a.name, b.name, m.my_goals, m.opp_goals, m.my_team, m.opp_team "
                          "FROM pes_tourney_match m LEFT JOIN pes_players a ON a.pid=m.pid "
                          "LEFT JOIN pes_players b ON b.pid=m.opp WHERE m.compe_id=? AND m.pid=? ORDER BY m.t DESC LIMIT ?",
                          (c["compe_id"], pid, HISTORY)).fetchall()
    # the player is listed as HOME: the list shows the score from that side. PESLEAGUE_MATCH = "eFootball League"
    return [_entry(*r, "PESLEAGUE_MATCH") for r in rows]


# ---- the host's admin API (/admin/api/tourneys…, signed in) --------------------------------------------------
def _when(v, default):
    """Unix seconds, or a 'YYYY-MM-DD[ HH:MM]' local time."""
    if v in (None, ""):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return int(time.mktime(time.strptime(str(v).strip(), fmt)))
        except ValueError:
            continue
    raise ValueError(f"bad date {v!r} (use YYYY-MM-DD or YYYY-MM-DD HH:MM)")


def save(svc, d):
    """Create (no compe_id) or change a competition. Keys: name, start, end, teams [ids], info (overview text),
    match_minutes (5-30, default 10)."""
    now = time.time()
    cur = get(svc, d.get("compe_id")) if d.get("compe_id") not in (None, "") else None
    if d.get("compe_id") not in (None, "") and cur is None:
        raise KeyError("no such competition")
    name = str(d.get("name") if d.get("name") is not None else (cur or {}).get("name") or "").strip()[:60]
    if not name:
        raise ValueError("a competition needs a name")
    m_start, m_end, _ = _month(now)
    start = _when(d.get("start"), cur["start"] if cur else int(now))
    end = _when(d.get("end"), cur["end"] if cur else m_end)
    if end <= start:
        raise ValueError("the end must be after the start")
    teams = d.get("teams", cur["teams"] if cur else TEAMS)
    try:
        teams = [int(t) for t in teams][:200]
    except (TypeError, ValueError):
        raise ValueError("teams must be a list of team ids")
    if not teams:
        raise ValueError("a competition needs at least one team")
    info = str(d.get("info") if d.get("info") is not None else (cur or {}).get("info") or "")[:2000]
    rules = dict((cur or {}).get("rules") or {})
    if d.get("match_minutes") not in (None, ""):
        try:
            m = int(d["match_minutes"])
        except (TypeError, ValueError):
            raise ValueError("match_minutes must be a number")
        if not 5 <= m <= 30:
            raise ValueError("match_minutes must be 5 to 30")
        rules["match_minutes"] = m
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if cur:
            db.execute("UPDATE pes_tourney SET name=?, start_date=?, end_date=?, teams=?, info=?, rules=? WHERE compe_id=?",
                       (name, start, end, json.dumps(teams), info, json.dumps(rules), cur["compe_id"]))
            cid = cur["compe_id"]
        else:
            cid = max(FIRST_ID, (db.execute("SELECT MAX(compe_id) FROM pes_tourney").fetchone()[0] or 0) + 1)
            db.execute("INSERT INTO pes_tourney (compe_id, name, match_type, start_date, end_date, teams, info, rules, "
                       "auto, created) VALUES (?,?,?,?,?,?,?,?,0,?)",
                       (cid, name, MATCH_1VS1, start, end, json.dumps(teams), info, json.dumps(rules), now))
    log.info("TOURNEY saved competition %d: %s", cid, name)
    return cid


def end_now(svc, cid):
    c = get(svc, cid)
    if c is None:
        raise KeyError("no such competition")
    now = int(time.time())
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_tourney SET end_date=? WHERE compe_id=?", (min(c["end"], now), c["compe_id"]))
        if c["start"] > now:
            db.execute("UPDATE pes_tourney SET start_date=? WHERE compe_id=?", (now - 1, c["compe_id"]))


def delete(svc, cid):
    c = get(svc, cid)
    if c is None:
        raise KeyError("no such competition")
    db = _db(svc)
    with svc.accounts.lock, db:
        auto = db.execute("SELECT auto FROM pes_tourney WHERE compe_id=?", (c["compe_id"],)).fetchone()[0]
        for t in ("pes_tourney_match", "pes_tourney_entry", "pes_tourney"):
            db.execute(f"DELETE FROM {t} WHERE compe_id=?", (c["compe_id"],))
        if auto:                                  # a deleted monthly cup does not come back the same month
            old = (db.execute("SELECT value FROM pes_tourney_cfg WHERE key='skipped'").fetchone() or ("",))[0]
            db.execute("INSERT OR REPLACE INTO pes_tourney_cfg (key, value) VALUES ('skipped', ?)",
                       (" ".join([*old.split()[-11:], str(auto)]),))


def monthly_cup(svc, on=None):
    """Whether a Friends Cup is made each month when nothing else runs (default on); on=True/False sets it."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if on is not None:
            db.execute("INSERT OR REPLACE INTO pes_tourney_cfg (key, value) VALUES ('monthly_cup', ?)",
                       ("on" if on else "off",))
        r = db.execute("SELECT value FROM pes_tourney_cfg WHERE key='monthly_cup'").fetchone()
    return (r[0] if r else "on") == "on"


def snapshot(svc):
    """Every competition with its table, for the admin page."""
    now = time.time()
    out = []
    for c in competitions(svc):
        state = "running" if c["start"] <= now < c["end"] else "ended" if c["end"] <= now else "upcoming"
        table = [{"rank": r[0], "pid": r[1], "name": r[2], "team_id": r[3], "points": r[4], "win": r[5], "draw": r[6],
                  "lose": r[7], "goals_for": r[8], "goals_against": r[9]} for r in standings(svc, c["compe_id"])]
        out.append({**c, "state": state, "table": table})
    return out


def admin_api(svc, route, method, body):
    def ok(obj):
        return "200 OK", json.dumps(obj).encode(), "application/json"

    def bad(code, msg):
        return code, json.dumps({"error": msg}).encode(), "application/json"

    if route == "/admin/api/tourneys" and method == "GET":
        return ok({"tourneys": snapshot(svc), "monthly_cup": monthly_cup(svc)})
    if method != "POST":
        return bad("404 Not Found", "unknown")
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError("bad request")
        if route == "/admin/api/tourneys/save":
            return ok({"compe_id": save(svc, req)})
        if route == "/admin/api/tourneys/end":
            end_now(svc, req.get("compe_id"))
            return ok({"ok": True})
        if route == "/admin/api/tourneys/monthly":
            return ok({"monthly_cup": monthly_cup(svc, bool(req.get("on")))})
        if route == "/admin/api/tourneys/delete":
            delete(svc, req.get("compe_id"))
            return ok({"ok": True})
    except KeyError as e:
        return bad("404 Not Found", str(e.args[0]) if e.args else "not found")
    except ValueError as e:
        return bad("400 Bad Request", str(e))
    return bad("404 Not Found", "unknown")


# ---- the end of a competition: the results notice ------------------------------------------------------------
def _unseen(svc, pid):
    """Ended competitions the player took part in and has not seen the final result of, newest first."""
    now = time.time()
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT t.compe_id FROM pes_tourney t JOIN pes_tourney_entry e ON e.compe_id=t.compe_id "
                          "WHERE e.pid=? AND t.end_date<=? AND NOT EXISTS (SELECT 1 FROM pes_tourney_seen s "
                          "WHERE s.pid=e.pid AND s.compe_id=t.compe_id) ORDER BY t.end_date DESC", (pid, now)).fetchall()
    return [r[0] for r in rows]


@cmd("CMD_GET_PESLEAGUE_MAINMENU_INFO")
def peslg_mainmenu(svc, sess, req):
    """new_result YES takes the game to the results notice of a competition that ended since the player last
    looked (flow PesLgProcGetMainMenuInfo -> 01_result)."""
    pid = _pid(sess, req)
    return {"result": NOERR, "division": "D1", "new_result": "YES" if pid is not None and _unseen(svc, pid) else "NO"}


def _result_entry(svc, c, pid):
    rows = standings(svc, c["compe_id"])
    me = next((r for r in rows if r[1] == pid), None)
    name = me[2] if me else ""
    # layout from the parser 0x141c11b00 (2026-11-08)
    return _pack({"compe_id": c["compe_id"], "compe_db_id": c["compe_id"], "compe_name": c["name"],
                  "banner_file_info": _pack({"filename": "", "hash_str": ""}), "start_date": c["start"],
                  "end_date": c["end"], "player_rank": me[0] if me else 0, "entry_user_num": len(rows),
                  "clan_name": "", "clan_owner_pid": pid or 0, "clan_owner_name": _pname(name, pid),
                  "is_participation_get": "NO", "update_division": 0, "before_division": 0, "is_confirm": "YES",
                  "is_get_reward": "NO"})


@cmd("CMD_GET_PESLEAGUE_COMPE_RESULT_LIST")
def compe_result_list(svc, sess, req):
    """is_unconfirm_list YES: the new final results (each is then marked seen); otherwise every ended competition
    the player took part in (or just compe_id)."""
    pid = _pid(sess, req)
    if pid is None:
        return {"result": NOERR, "result_list": [], "result_list_num": 0}
    by_id = {c["compe_id"]: c for c in competitions(svc)}
    if str(req.get("is_unconfirm_list") or "").upper() == "YES":
        ids = _unseen(svc, pid)
        db = _db(svc)
        with svc.accounts.lock, db:
            db.executemany("INSERT OR IGNORE INTO pes_tourney_seen (pid, compe_id) VALUES (?,?)", [(pid, i) for i in ids])
    else:
        now = time.time()
        ids = [cid for cid, c in by_id.items() if c["end"] <= now and entry(svc, cid, pid) is not None]
        try:
            want = int(req.get("compe_id") or 0)
        except (TypeError, ValueError):
            want = 0
        if want:
            ids = [i for i in ids if i == want]
    out = [_result_entry(svc, by_id[i], pid) for i in ids if i in by_id]
    return {"result": NOERR, "result_list": out, "result_list_num": len(out)}
