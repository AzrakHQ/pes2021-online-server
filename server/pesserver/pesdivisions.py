"""Online Divisions (eFootball -> Online Divisions): ten divisions, a season of ten matches, promotion and relegation.

How the game uses the server (notes/re/functions/modes2.md, live 2026-11-06):
- GET_DIVISION_INFO is answered with a `division_info` blob: the division ("D1".."D10"), the promotion,
  double-promotion and relegation thresholds, `other_match_num` (matches left in the season) and the season's
  `match_history_list`; the game adds up the history's `division_point` values to show the current points.
  `other_match_num` 0 makes the game run its season-end screens and send FINISH_SEASON, where the server moves the
  player up or down and starts a new season. The season has no dates: it is ten matches.
- The search is Quick Match with `matching_type "OC_RANK"` (pesmatch pairs only players of the same type).
- After the final whistle both games get the usual rating verdict (WATCH_GAMEADDPOINT), and the finished match
  is added to both players' histories here.
Points: win 3, draw 1, loss 0 (the thresholds are our own choice; Konami's are not in the game).
"""
import logging, time

import msgpack

from .pescmds import NOERR, cmd, _db, _player
from . import pesmatch

log = logging.getLogger("pesserver.divisions")

DIVISIONS = 10                    # D10 (bottom, where everyone starts) .. D1 (top)
SEASON = 10                       # matches per season (the game's history holds ten)
POINTS = {"WIN": 3, "DRAW": 1, "LOSE": 0}


def thresholds(div):
    """(promotion, double promotion, relegation) points for a division number (10 = bottom, 1 = top)."""
    if div >= DIVISIONS:
        return 12, 21, 0                      # the bottom division can't be relegated
    if div <= 1:
        return 31, 31, 9                      # the top: nothing to be promoted to (31 > 10 x 3)
    return 15, 24, 7


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_division (pid INTEGER PRIMARY KEY, division INTEGER NOT NULL,
                  season INTEGER NOT NULL DEFAULT 1, wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0,
                  losses INTEGER DEFAULT 0, best_streak INTEGER DEFAULT 0, streak INTEGER DEFAULT 0)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pes_division_match (pid INTEGER, season INTEGER, match_id TEXT,
                  points INTEGER, result TEXT, enemy_name TEXT, enemy_team_id INTEGER, my_side TEXT,
                  home_score INTEGER, away_score INTEGER, t REAL, UNIQUE (pid, match_id))""")
    if "my_team_id" not in {r[1] for r in db.execute("PRAGMA table_info(pes_division_match)")}:
        db.execute("ALTER TABLE pes_division_match ADD COLUMN my_team_id INTEGER")   # the profile's team log


def _state(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT division, season, wins, draws, losses, best_streak FROM pes_division WHERE pid=?",
                       (pid,)).fetchone()
        if r is None:
            db.execute("INSERT INTO pes_division (pid, division) VALUES (?, ?)", (pid, DIVISIONS))
            r = (DIVISIONS, 1, 0, 0, 0, 0)
        hist = db.execute("SELECT points, result, enemy_name, enemy_team_id, my_side, home_score, away_score "
                          "FROM pes_division_match WHERE pid=? AND season=? ORDER BY t", (pid, r[1])).fetchall()
    return {"division": r[0], "season": r[1], "wins": r[2], "draws": r[3], "losses": r[4], "best": r[5],
            "history": hist}


def _pack(d):
    return msgpack.packb(d, use_bin_type=True, unicode_errors="surrogateescape")


def division_info(svc, pid):
    st = _state(svc, pid)
    up, up2, down = thresholds(st["division"])
    hist = [_pack({"division_point": pts, "enemy_team_id": int(team or 0), "home_score": hs, "away_score": as_,
                   "enemy_side": "AWAY" if side == "HOME" else "HOME", "enemy_name": name or "",
                   "is_finished_match": "YES", "win_lose": res, "enemy_cpu_level": ""})
            for pts, res, name, team, side, hs, as_ in st["history"][:SEASON]]
    p = _player(svc, pesmatch._username(svc, pid)) or {"rating": 0}
    # This season's record: the Season Results screen shows it as W/D/L under "Matches 10" (live 2026-11-07).
    season = [h[1] for h in st["history"][:SEASON]]
    best = run = 0
    for r in season:
        run = run + 1 if r == "WIN" else 0
        best = max(best, run)
    rec = {"win_count": season.count("WIN"), "draw_count": season.count("DRAW"), "lose_count": season.count("LOSE"),
           "win_s_max": best, "rating": float(p.get("rating") or 0)}
    return {"division": f"D{st['division']}", "demotion_point": down, "promotion_point": up,
            "doublerank_promotion_point": up2, "other_match_num": max(0, SEASON - len(st["history"])),
            "match_record": _pack(rec), "match_history_list": hist}


@cmd("CMD_GET_DIVISION_INFO")
def get_division_info(svc, sess, req):
    pid = pesmatch._pid(sess)
    if pid is None:
        return {"result": "ERR_DATABASE"}
    p = _player(svc, pesmatch._username(svc, pid)) or {"name": ""}
    pname = _pack({"name": p.get("name") or "", "xuid_one": "", "is_deleted": "NO",
                   "psn_user_id": _pack({"account_id": 0, "online_id": "", "is_valid_cache": "NO"})})
    return {"result": NOERR, "pname": pname, "division_info": _pack(division_info(svc, pid))}


@cmd("CMD_FINISH_SEASON")
def finish_season(svc, sess, req):
    """The game finished its ten matches and showed the season results: move up or down, start a new season."""
    pid = pesmatch._pid(sess)
    st = _state(svc, pid)
    if len(st["history"]) < SEASON:
        return {"result": NOERR}                  # not over yet (or asked twice): nothing to do
    pts = sum(h[0] for h in st["history"][:SEASON])
    up, up2, down = thresholds(st["division"])
    div = st["division"]
    new = max(1, div - 2) if pts >= up2 else max(1, div - 1) if pts >= up else min(DIVISIONS, div + 1) if pts < down else div
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_division SET division=?, season=season+1 WHERE pid=?", (new, pid))
    log.info("DIVISION %s: season %d over with %d points, D%d -> D%d", pid, st["season"], pts, div, new)
    return {"result": NOERR}


@cmd("CMD_GET_OFTEN_USED_TEAM_LIST")
def get_often_used_team_list(svc, sess, req):
    # Shown as "Frequently Selected Teams". We don't learn which team a player picked, so the list is empty.
    return {"result": NOERR, "used_team_id_list_num": 0, "last_used_team_id": 0}    # (list left out: none to show)


def record(svc, room):
    """Called once per finished OC_RANK match (pesmatch, at the verdict): add it to both players' histories."""
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home = room["owner"]
    away = next(p for p in room["members"] if p != home)
    mid = room.get("match_id") or f"{int(room.get('t', time.time()))}-{home}-{away}"
    teams = room.get("teams", {})
    seasons = {p: _state(svc, p)["season"] for p in (home, away)}     # (takes the lock itself: not inside it)
    names = {p: (_player(svc, pesmatch._username(svc, p)) or {}).get("name") or "" for p in (home, away)}
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for me, other, side in ((home, away, "HOME"), (away, home, "AWAY")):
            st = {"season": seasons[me]}
            mine, theirs = (score["HOME"], score["AWAY"]) if side == "HOME" else (score["AWAY"], score["HOME"])
            res = "WIN" if mine > theirs else "LOSE" if mine < theirs else "DRAW"
            cur = db.execute("INSERT OR IGNORE INTO pes_division_match (pid, season, match_id, points, result, "
                             "enemy_name, enemy_team_id, my_side, home_score, away_score, t) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (me, st["season"], mid, POINTS[res], res, names[other], int(teams.get(other) or 0),
                              side, score["HOME"], score["AWAY"], time.time()))
            if cur.rowcount:
                db.execute("UPDATE pes_division_match SET my_team_id=? WHERE pid=? AND match_id=?",
                           (int(teams.get(me) or 0), me, mid))
                col = {"WIN": "wins", "DRAW": "draws", "LOSE": "losses"}[res]
                db.execute(f"UPDATE pes_division SET {col}={col}+1, streak=CASE WHEN ?='WIN' THEN streak+1 ELSE 0 END, "
                           "best_streak=MAX(best_streak, CASE WHEN ?='WIN' THEN streak+1 ELSE 0 END) WHERE pid=?",
                           (res, res, me))
                log.info("DIVISION %s: %s %d-%d vs %s (+%d)", me, res, score["HOME"], score["AWAY"], other, POINTS[res])
