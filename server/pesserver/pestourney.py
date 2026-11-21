"""Online Tournament: friends' competitions in the game's own PES League screens (eFootball -> Online Tournament).

How the game runs a competition (flow files Online/PesLg/*, notes/re/functions/tourney.md):
- Competition list -> details -> team select -> JOIN_PESLEAGUE_COMPE {compe_id, team_id} ->
  GET_PESLEAGUE_1VS1_MAINMENU_INFO -> the competition's 1v1 menu (match_type PES_LEAGUE_MATCH_1VS1).
- "Match" in that menu goes to the Quick Match matchmaking flow (Online/Quick/QuickProcessInit); pesmatch pairs
  two players of the same competition. Results count 3 points for a win, 1 for a draw.
- The "Match Replays" competition (pesreplay, compe_id 1) is listed next to these and keeps its lobby path.

Competitions live in the server database (pes_tourney). With none running, a monthly "Friends Cup" is made.
A competition with rules format "cup" is a knockout cup: pescup.py keeps its draw and bracket.
"""
import json, logging, time

import msgpack

from .accounts import once
from .pescmds import NOERR, cmd, _db, player_of
from . import pescup, pesreplay

log = logging.getLogger("pesserver.tourney")
POINTS = {"WIN": 3, "DRAW": 1, "LOSE": 0}
FIRST_ID = 100                      # compe_id 1 is the Match Replays competition
MATCH_1VS1 = "PES_LEAGUE_MATCH_1VS1"
TEAMS = [8, 9, 45, 14, 50, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13]   # national teams (pesdb.py), editable per competition


def _pack(o):
    return msgpack.packb(o, use_bin_type=True)


@once
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
    if "my_pk" not in {r[1] for r in db.execute("PRAGMA table_info(pes_tourney_match)")}:      # a shootout's score
        db.execute("ALTER TABLE pes_tourney_match ADD COLUMN my_pk INTEGER")
        db.execute("ALTER TABLE pes_tourney_match ADD COLUMN opp_pk INTEGER")


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
def regulation(c, svc=None):
    r = c.get("rules") or {}
    # is_equalization NO: with YES the game tells players their ratings are evened out and asks the server for
    # the evened-out stats (GET_PLAYER_PARAMETERLIST is_equalization YES), which this server does not make; teams
    # play at their real strength, as in Quick Match, and the game says so (no notice; live 2026-11-08).
    reg = {"match_time": int(r.get("match_minutes") or MATCH_MINUTES), "is_equalization": "NO"}
    if pescup.is_cup(c):
        # extra_time / pk "YES": a match level after 90 minutes goes to extra time, then to penalties (live
        # 2026-11-21: BREAK2 -> EX1ST -> EX2ND -> BREAK4 -> PK). Every match of a cup has them, the group matches
        # too: a game reads the rules only when the player opens the competition (not after a match), so rules
        # that changed between the groups and the knockout would leave friends who stayed in its menu playing
        # knockout ties without extra time.
        reg.update(extra_time="YES", pk="YES")
    return reg


def host_first(svc, cid, x, y):
    """(pid, session) x and y are about to play a cup match: the one whose game has the cup's rules goes first
    and becomes the room's owner. Both games play by the owner's rules (live 2026-11-21: an owner with 5-minute
    matches and a guest with 10 played 5), and a game only reads the rules when it opens the competition, so a
    friend who opened it while it was still a league (the host changed the format later) has no extra time."""
    c = get(svc, cid)
    if not pescup.is_cup(c):
        return x, y
    want = (c["compe_id"], regulation(c, svc)["extra_time"])
    has = lambda s: getattr(s[1], "pes_reg", None) == want       # noqa: E731
    return (y, x) if has(y) and not has(x) else (x, y)


def get(svc, compe_id):
    try:
        cid = int(compe_id)
    except (TypeError, ValueError, OverflowError):
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
            "match_end_date": c["end"], "entry_limit_date": c["end"], "entry_user_limit": "YES" if one_team(c) else "NO",
            "entry_user_max": max(len(set(c["teams"])), _entry_num(svc, c["compe_id"])) if one_team(c) else 1000,
            "entry_user_num": _entry_num(svc, c["compe_id"]), "entry_limit_division": "NO",
            "entry_limit_division_min": 1, "entry_limit_division_max": 10, "is_end": "YES" if ended else "NO",
            "is_entry": "YES" if joined else "NO", "is_gm_account": "NO", "is_no_check": "NO", "mode": "PESLEAGUE"}


def _pid(sess, req):
    return player_of(sess, req)


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
    reg = regulation(c, svc)
    out["compe_regulation"] = _pack(reg)
    if sess is not None:
        sess.pes_reg = (c["compe_id"], reg.get("extra_time"))       # (host_first: which rules this game holds)
    return out


@cmd("CMD_GET_PESLEAGUE_COMPE_OVERVIEW_INFO")
def compe_overview(svc, sess, req):
    """The host's own text about the competition (rules, prizes, dates), set with the admin API (info)."""
    c = get(svc, req.get("compe_id"))
    return {"result": NOERR, "overview_info": outline(c, svc) if c else ""}


def outline(c, svc=None):
    """The host's text, or one made from the competition's rules. A cup: its draw and results, then the host's text."""
    if svc is not None and pescup.is_cup(c):
        text = pescup.outline(svc, c)
        return (text + "\n\n" + c["info"])[:2000] if c.get("info") else text
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
    teams = free_teams(svc, c, _pid(sess, req)) or c["teams"]       # (all taken: JOIN says the cup is full)
    return {"result": NOERR, "team_list": teams, "team_list_num": len(teams)}


def one_team(c):
    return bool((c.get("rules") or {}).get("one_team"))


def free_teams(svc, c, pid):
    """The teams `pid` can enter with: with one_team, not those another friend entered with."""
    if not one_team(c):
        return list(c["teams"])
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT pid, team_id FROM pes_tourney_entry WHERE compe_id=?", (c["compe_id"],)).fetchall()
    mine = {t for p, t in rows if p == pid}              # (their own team is always theirs, even if shared)
    taken = {t for p, t in rows if p != pid} - mine
    return [t for t in c["teams"] if t not in taken]


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
    closed = pescup.is_cup(c) and pescup.stage(svc, c["compe_id"]) != "entry"
    mine = entry(svc, c["compe_id"], pid)
    free = free_teams(svc, c, pid)
    if team not in c["teams"]:
        team = (free or c["teams"])[0]
    if team not in free and not (mine is None and closed):
        if mine is not None:
            # a friend already in keeps their team (never locked out): another friend has the one they picked
            log.info("TOURNEY pid %s keeps team %d in %d (%d is taken)", pid, mine["team_id"], c["compe_id"], team)
            team = mine["team_id"]
        else:
            # one team per friend and another friend has it (they entered meanwhile), or every team is taken: the
            # game says "Unable to enter Competition as maximum capacity has been reached." and goes back to the list
            log.info("TOURNEY pid %s can't enter %d with team %d (taken)", pid, c["compe_id"], team)
            return {"result": "ERR_COMPE_FULL"}
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        new = not db.execute("SELECT 1 FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (c["compe_id"], pid)).fetchone()
        if new and closed:                        # a cup after its draw: nobody new comes in (they are never paired)
            log.info("TOURNEY pid %s is too late for cup %d (the draw is made)", pid, c["compe_id"])
            # the game says "Entries for this Competition are currently closed." (server_errors.tsv)
            return {"result": "ERR_COMPE_AFTER_ENTRYTIME"}
        # the game sends this again after every match (same team): only the first one is an entry
        db.execute("INSERT INTO pes_tourney_entry (compe_id, pid, team_id, joined) VALUES (?,?,?,?) "
                   "ON CONFLICT (compe_id, pid) DO UPDATE SET team_id=excluded.team_id", (c["compe_id"], pid, team, time.time()))
    sess.pes_compe = c["compe_id"]
    if new:
        log.info("TOURNEY pid %s joined %d with team %d", pid, c["compe_id"], team)
        from . import events
        events.feed(svc, f"{events.name_of(svc, pid)} entered {c['name']}", "info", [pid])
    return {"result": NOERR}


@cmd("CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO")
def mainmenu_1vs1(svc, sess, req):
    c = get(svc, req.get("compe_id"))
    if c is None:
        return pesreplay.peslg_1vs1(svc, sess, req)
    pid = _pid(sess, req)
    sess.pes_compe = c["compe_id"]
    e = entry(svc, c["compe_id"], pid) or {"team_id": c["teams"][0], "points": 0, "win": 0, "draw": 0, "lose": 0}
    played = e["win"] + e["draw"] + e["lose"]
    # regulation_match_num is the "Best N consecutive match result" header and match_num "Matches to achieve
    # record" (live 2026-11-08); every match counts here, so N = the matches played (at least 1)
    return {"result": NOERR, "compe_end_time": c["end"], "draw_count": e["draw"], "lose_count": e["lose"],
            "match_num": played, "matching_first_date": c["start"],
            # the "Matchmaking window closes in" clock is matching_first_date + matching_time (live 2026-11-08)
            "matching_interval": 0, "matching_time": c["end"] - c["start"], "point": e["points"],
            "rank_type": "NONE", "regulation_match_num": max(1, played), "team_id": e["team_id"], "team_name": "",
            "win_count": e["win"]}


# ---- results ------------------------------------------------------------------------------------------------
def record(svc, room):
    """Called once per finished Online Tournament match (pesmatch, at the verdict or a forfeit).
    Returns {pid: (points before, points after)} (the same again on a repeat call), or {} for no competition."""
    cid = room.get("compe")
    c = get(svc, cid)
    if c is None or not c["start"] <= room.get("t", time.time()) < c["end"]:
        return {}                         # (a match paired before the start or after the end does not count)
    out = {}
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home = room["owner"]
    away = next(p for p in room["members"] if p != home)
    teams = room.get("teams") or {}
    mid = room.get("match_id") or f"{int(room.get('t', time.time()))}-{home}-{away}"
    now = time.time()
    cup = pescup.is_cup(c)
    fresh = False
    pk = room.get("pk") if score["HOME"] == score["AWAY"] else None      # (pesmatch.shootout: only with a winner)
    db = _db(svc)
    if cup:                               # a cup counts only the ties of its draw
        with svc.accounts.lock, db:
            _setup(db)
            known = db.execute("SELECT 1 FROM pes_tourney_match WHERE compe_id=? AND match_id=?", (cid, mid)).fetchone()
        if not known and not pescup.can_pair(svc, cid, home, away):
            return {}
    with svc.accounts.lock, db:
        _setup(db)
        for me, opp, side in ((home, away, "HOME"), (away, home, "AWAY")):
            mine, theirs = (score["HOME"], score["AWAY"]) if side == "HOME" else (score["AWAY"], score["HOME"])
            res = "WIN" if mine > theirs else "LOSE" if mine < theirs else "DRAW"
            other = "AWAY" if side == "HOME" else "HOME"
            if cup and pk:                    # a cup match decided on penalties is a win (the goals stay level)
                res = "WIN" if pk[side] > pk[other] else "LOSE"
            if not db.execute("SELECT 1 FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (cid, me)).fetchone():
                db.execute("INSERT INTO pes_tourney_entry (compe_id, pid, team_id, joined) VALUES (?,?,?,?)",
                           (cid, me, teams.get(me) or c["teams"][0], now))
            before = db.execute("SELECT points FROM pes_tourney_entry WHERE compe_id=? AND pid=?", (cid, me)).fetchone()[0]
            cur = db.execute("INSERT OR IGNORE INTO pes_tourney_match (compe_id, match_id, pid, opp, my_team, opp_team, "
                             "my_goals, opp_goals, result, t, my_pk, opp_pk) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (cid, mid, me, opp, teams.get(me) or 0, teams.get(opp) or 0, mine, theirs, res, now,
                              pk and pk.get(side), pk and pk.get(other)))
            if cur.rowcount:
                col = {"WIN": "win", "DRAW": "draw", "LOSE": "lose"}[res]
                db.execute(f"UPDATE pes_tourney_entry SET points=points+?, {col}={col}+1, goals_for=goals_for+?, "
                           "goals_against=goals_against+? WHERE compe_id=? AND pid=?",
                           (POINTS[res], mine, theirs, cid, me))
                log.info("TOURNEY %d: pid %s %s %d-%d (+%d)", cid, me, res, mine, theirs, POINTS[res])
                out[me] = (before, before + POINTS[res])
                fresh = True
            else:
                out[me] = (before - POINTS[res], before)
    room["tourney_points"] = out
    if cup and fresh:
        pescup.result(svc, c, home, away, score["HOME"], score["AWAY"], mid, pk)
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
        cup = (json.loads((db.execute("SELECT rules FROM pes_tourney WHERE compe_id=?", (cid,)).fetchone() or ("{}",))[0]
                          or "{}").get("format") == "cup")
    rows = [(i, *r) for i, r in enumerate(rows, 1)]
    return pescup.order(svc, cid, rows) if cup else rows     # a cup: how far each got in the knockout comes first


def forget(svc, pid):
    """A deleted player leaves every competition (their opponents keep their points; open cup ties go to them)."""
    pescup.forget(svc, pid)
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for t in ("pes_tourney_entry", "pes_tourney_match", "pes_tourney_seen"):      # seen: the number is reused
            db.execute(f"DELETE FROM {t} WHERE pid=?", (pid,))


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
        rows = db.execute("SELECT m.t, m.pid, m.opp, a.name, b.name, m.my_goals, m.opp_goals, m.my_team, m.opp_team, "
                          "m.my_pk, m.opp_pk FROM pes_tourney_match m LEFT JOIN pes_players a ON a.pid=m.pid "
                          "LEFT JOIN pes_players b ON b.pid=m.opp WHERE m.compe_id=? AND m.pid=? ORDER BY m.t DESC LIMIT ?",
                          (c["compe_id"], pid, HISTORY)).fetchall()
    # the player is listed as HOME: the list shows the score from that side. PESLEAGUE_MATCH = "eFootball League"
    return [_entry(*r[:9], "PESLEAGUE_MATCH", pk=r[9:11]) for r in rows]


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
    match_minutes (5-30, default 10), format ("league", the default: a points table; "cup": a knockout, pescup),
    third_place (a cup: the beaten semi-finalists play a third-place match; fixed once the draw is made),
    groups / qualify / fill / legs (a cup's group stage, pescup.group_rules; fixed once the draw is made),
    one_team (each team can be entered by one friend only)."""
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
        teams = list(dict.fromkeys(int(t) for t in teams))[:200]
    except (TypeError, ValueError, OverflowError):
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
    if d.get("format") not in (None, ""):
        fmt = str(d["format"]).lower()
        if fmt not in ("league", "cup"):
            raise ValueError("format must be league or cup")
        if fmt != (rules.get("format") or "league"):
            if cur and (pescup.fixtures(svc, cur["compe_id"]) or standings(svc, cur["compe_id"]) and any(
                    r[5] + r[6] + r[7] for r in standings(svc, cur["compe_id"]))):
                raise ValueError("matches have been played: the format stays")
            rules.pop("format", None)
            if fmt == "cup":
                rules["format"] = "cup"
    if rules.get("format") != "cup":
        rules.pop("third_place", None)
    elif d.get("third_place") is not None and not isinstance(d["third_place"], bool):
        raise ValueError("third_place must be true or false")
    elif d.get("third_place") is not None and d["third_place"] != bool(rules.get("third_place")):
        if cur and pescup.fixtures(svc, cur["compe_id"]):
            raise ValueError("the draw is made: the third-place match stays as it is")
        rules.pop("third_place", None)
        if d["third_place"]:
            rules["third_place"] = True
    _group_rules(svc, cur, d, rules)
    if d.get("one_team") is not None:
        if not isinstance(d["one_team"], bool):
            raise ValueError("one_team must be true or false")
        rules.pop("one_team", None)
        if d["one_team"]:
            rules["one_team"] = True
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


GROUP_KEYS = {"groups": (0, *pescup.GROUP_SIZES), "qualify": (1, 2), "fill": (False, True), "legs": (1, 2)}
GROUP_DEFAULT = {"groups": 0, "qualify": pescup.QUALIFY, "fill": False, "legs": 1}


def _group_rules(svc, cur, d, rules):
    """A cup's group stage settings into rules (dropped for a league; fixed once the draw is made)."""
    if rules.get("format") != "cup":
        for k in GROUP_KEYS:
            rules.pop(k, None)
        return
    new = dict(rules)
    for k, allowed in GROUP_KEYS.items():
        v = d.get(k)
        if v is None:
            continue
        if isinstance(allowed[0], bool):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        elif isinstance(v, bool) or not isinstance(v, int) or v not in allowed:
            raise ValueError(f"{k} must be one of {', '.join(map(str, allowed))}")
        new.pop(k, None)
        if v != GROUP_DEFAULT[k]:
            new[k] = v
    if not new.get("groups"):
        for k in ("qualify", "fill", "legs"):
            new.pop(k, None)
    if new != rules and cur and pescup.fixtures(svc, cur["compe_id"]):
        raise ValueError("the draw is made: the group stage stays as it is")
    rules.clear()
    rules.update(new)


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
        # (seen too: the next competition gets this number, and its final-results notice must still show)
        for t in ("pes_tourney_match", "pes_tourney_entry", "pes_tourney_seen", "pes_tourney"):
            db.execute(f"DELETE FROM {t} WHERE compe_id=?", (c["compe_id"],))
        pescup.delete(db, c["compe_id"])
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
        cup = pescup.snapshot(svc, c) if pescup.is_cup(c) else None
        size, qualify, fill, legs = pescup.group_rules(c)
        plan = None
        if cup and cup["stage"] == "entry":
            try:
                plan = pescup.plan(len(table), size or 4, qualify, fill, legs)
            except ValueError as e:
                plan = {"error": str(e)}
        out.append({**c, "state": state, "table": table, "format": "cup" if cup else "league", "cup": cup,
                    "third_place": pescup.third_place(c), "groups": size, "qualify": qualify, "fill": fill,
                    "legs": legs, "one_team": one_team(c), "plan": plan})
    return out


# ---- ready-made cups ----------------------------------------------------------------------------------------
# Real tournaments with the teams PES 2021 has for them, in their own format scaled to the friends who enter
# (groups of four; the best third-placed fill the knockout where the real one does that). One team per friend.
# World Cup 2022, EURO 2024 and Copa América 2024: the real entrants the game has (all but Canada); the others
# are the game's own entry lists (web/teams.json "tournaments", from its CompetitionEntry.bin, mkclublist.py).
def _cup(key, name, teams, third=False, fill=False, legs=1):
    return {"key": key, "name": name, "format": "cup", "third_place": third, "groups": 4, "qualify": 2,
            "fill": fill, "legs": legs, "one_team": True, "teams": teams}


def _game(key):
    from . import adminextra
    return list((adminextra.teams().get("tournaments") or {}).get(key) or [])


def builtin_presets():
    out = [
        _cup("world", "World Cup", [1026, 51, 38, 10, 5, 55, 39, 4, 50, 56, 40, 19, 8, 57, 15, 33, 7, 42, 14, 52,
                                    9, 32, 24, 45, 1550, 11, 36, 6, 1058, 49, 53], third=True),
        _cup("euro", "European Championship", [14, 3, 22, 11, 7, 24, 12, 1165, 23, 15, 1550, 5, 19, 10, 21, 8, 9,
                                                20, 26, 30, 29, 1174, 6, 13], fill=True),
        _cup("copa", "Copa América", [50, 46, 47, 40, 51, 1129, 41, 39, 49, 1113, 1128, 45, 44, 48, 42], third=True),
        _cup("africa", "Africa Cup of Nations", _game("africa"), third=True, fill=True),
        _cup("asia", "Asian Cup", _game("asia"), fill=True),
        _cup("ucl", "Champions League", _game("ucl"), legs=2),
        _cup("uel", "Europa League", _game("uel"), legs=2),
        _cup("libertadores", "Copa Libertadores", _game("libertadores"), legs=2),
        _cup("afccl", "AFC Champions League", _game("afccl"), legs=2),
    ]
    return [p for p in out if p["teams"]]


PRESET_KEYS = ("name", "format", "third_place", "groups", "qualify", "fill", "legs", "one_team", "teams",
               "match_minutes")
MAX_PRESETS = 20


def presets(svc):
    """The ready-made cups, then the host's own (saved from the admin page's form)."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT value FROM pes_tourney_cfg WHERE key='presets'").fetchone()
    try:
        own = json.loads(r[0]) if r else []
    except ValueError:
        own = []
    return [dict(p, own=False) for p in builtin_presets()] + [dict(p, own=True) for p in own if isinstance(p, dict)]


def _own_presets(svc, change):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT value FROM pes_tourney_cfg WHERE key='presets'").fetchone()
        try:
            own = [p for p in (json.loads(r[0]) if r else []) if isinstance(p, dict)]
        except ValueError:
            own = []
        out = change(own)
        db.execute("INSERT OR REPLACE INTO pes_tourney_cfg (key, value) VALUES ('presets', ?)", (json.dumps(own),))
    return out


def save_preset(svc, d):
    """The host's own ready-made cup: the form's settings under a name (the same name replaces it)."""
    name = str(d.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("a ready-made cup needs a name")
    fmt = str(d.get("format") or "league").lower()
    if fmt not in ("league", "cup"):
        raise ValueError("format must be league or cup")
    p = {"name": name, "format": fmt, "teams": [], "one_team": d.get("one_team") is True}
    try:
        p["teams"] = [int(t) for t in d.get("teams") or []][:200]
    except (TypeError, ValueError, OverflowError):
        raise ValueError("teams must be a list of team ids")
    if not p["teams"]:
        raise ValueError("a ready-made cup needs at least one team")
    if d.get("match_minutes") not in (None, ""):
        if not isinstance(d["match_minutes"], int) or isinstance(d["match_minutes"], bool) or not 5 <= d["match_minutes"] <= 30:
            raise ValueError("match_minutes must be 5 to 30")
        p["match_minutes"] = d["match_minutes"]
    if fmt == "cup":
        p["third_place"] = d.get("third_place") is True
        rules = {"format": "cup"}
        _group_rules(svc, None, d, rules)
        p.update({k: rules.get(k, GROUP_DEFAULT[k]) for k in GROUP_KEYS})

    def put(own):
        key = next((x["key"] for x in own if x.get("name", "").lower() == name.lower()), None)
        if key is None:
            if len(own) >= MAX_PRESETS:
                raise ValueError(f"there are already {MAX_PRESETS} of your own: delete one first")
            n = max([int(str(x.get("key", "own-0"))[4:] or 0) for x in own if str(x.get("key", "")).startswith("own-")
                     and str(x["key"])[4:].isdigit()] + [0]) + 1
            key = f"own-{n}"
        own[:] = [x for x in own if x.get("key") != key] + [{"key": key, **p}]
        return key
    return _own_presets(svc, put)


def delete_preset(svc, key):
    def drop(own):
        if not any(x.get("key") == key for x in own):
            raise KeyError("no such ready-made cup")
        own[:] = [x for x in own if x.get("key") != key]
    _own_presets(svc, drop)


def admin_api(svc, route, method, body):
    def ok(obj):
        return "200 OK", json.dumps(obj).encode(), "application/json"

    def bad(code, msg):
        return code, json.dumps({"error": msg}).encode(), "application/json"

    if route == "/admin/api/tourneys" and method == "GET":
        return ok({"tourneys": snapshot(svc), "monthly_cup": monthly_cup(svc), "presets": presets(svc)})
    if method != "POST":
        return bad("404 Not Found", "unknown")
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError("bad request")
        if route == "/admin/api/tourneys/save":
            return ok({"compe_id": save(svc, req)})
        if route == "/admin/api/tourneys/preset":
            return ok({"key": save_preset(svc, req)})
        if route == "/admin/api/tourneys/preset/delete":
            delete_preset(svc, str(req.get("key") or ""))
            return ok({"ok": True})
        if route == "/admin/api/tourneys/end":
            end_now(svc, req.get("compe_id"))
            return ok({"ok": True})
        if route == "/admin/api/tourneys/monthly":
            return ok({"monthly_cup": monthly_cup(svc, bool(req.get("on")))})
        if route == "/admin/api/tourneys/delete":
            delete(svc, req.get("compe_id"))
            return ok({"ok": True})
        if route in ("/admin/api/tourneys/draw", "/admin/api/tourneys/undraw", "/admin/api/tourneys/award"):
            c = get(svc, req.get("compe_id"))
            if c is None:
                raise KeyError("no such competition")
            if not pescup.is_cup(c):
                raise ValueError("that competition is not a cup")
            if route.endswith("/undraw"):
                pescup.undraw(svc, c)
            elif time.time() >= c["end"]:
                raise ValueError("that cup has ended")
            elif route.endswith("/draw"):
                pescup.draw(svc, c, groups=None if req.get("groups") is None else bool(req.get("groups")))
            else:
                pescup.walkover(svc, c, req.get("fid"), req.get("winner"))
            return ok({"ok": True})
    except KeyError as e:
        return bad("404 Not Found", str(e.args[0]) if e.args else "not found")
    except ValueError as e:
        return bad("400 Bad Request", str(e))
    except OverflowError:                         # (a number no competition or tie can have)
        return bad("400 Bad Request", "bad number")
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
