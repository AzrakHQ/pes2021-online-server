"""Rankings: Online Stats -> Overall Ranked Match Rankings, a leaderboard of everyone on this server.

The game (notes/re/functions/results.md "CMD_GET_RANKING", live request 2026-11-07): HTTP form
{urltype RANK, start_index, view_num 100, sort RATING|WIN_NUM|MATCH_NUM|WIN_STRAIGHT|WIN_RATE, order ASC};
_BYID adds pid and asks for the window around that player. The reply uses inline maps (HTTP), not blobs:
  {result, date, total_rank_num, rank_list: [{user: {pid, pname: {name, xuid_one, psn_user_id: {...}, is_deleted},
   division "D1".."D10", manner "C".."AAA" (a plain string here), favoriteTeam, favoritePlayer, intro,
   match_record: {win_count, draw_count, lose_count, win_s_max, rating}}, rank, point}]}
Shown live (2026-11-07): the list (rank, name, the sort's value) and a User Information panel (avatar, name,
division badge, courtesy rating, matches, W/D/L, win rate, favourite team and player, comment).
A bare reply shows "Unable to find any ranked users". Win Rate leaves out players with fewer than 10 matches,
as the game's own note says.
"""
import logging, time

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.ranking")
WIN_RATE_MIN = 10


def table(svc):
    """Every player with their rating and record from the stored matches."""
    db = _db(svc)
    with svc.accounts.lock, db:
        players = db.execute("SELECT pid, name, rating FROM pes_players WHERE name IS NOT NULL AND name != ''").fetchall()
        try:
            cols = {r[1] for r in db.execute("PRAGMA table_info(pes_matches)")}
            q = "SELECT home_pid, away_pid, home_score, away_score FROM pes_matches"
            if "voided" in cols:
                q += " WHERE voided=0"
            matches = db.execute(q + " ORDER BY id").fetchall()
        except Exception:  # noqa: BLE001 - no matches yet
            matches = []
    rec = {pid: {"pid": pid, "name": name or f"Player {pid}", "rating": int(rating or 1000), "w": 0, "d": 0, "l": 0,
                 "streak": 0, "best": 0} for pid, name, rating in players}
    for hp, ap, hs, as_ in matches:
        if hs is None or as_ is None:
            continue
        for me, mine, theirs in ((hp, hs, as_), (ap, as_, hs)):
            r = rec.get(me)
            if r is None:
                continue
            if mine > theirs:
                r["w"] += 1; r["streak"] += 1; r["best"] = max(r["best"], r["streak"])
            elif mine == theirs:
                r["d"] += 1; r["streak"] = 0
            else:
                r["l"] += 1; r["streak"] = 0
    return list(rec.values())


SORTS = {
    "RATING": lambda r: r["rating"],
    "WIN_NUM": lambda r: r["w"],
    "MATCH_NUM": lambda r: r["w"] + r["d"] + r["l"],
    "WIN_STRAIGHT": lambda r: r["best"],
    "WIN_RATE": lambda r: round(100 * r["w"] / max(1, r["w"] + r["d"] + r["l"])),
}


def ranked(svc, sort):
    key = SORTS.get(sort, SORTS["RATING"])
    rows = table(svc)
    if sort == "WIN_RATE":
        rows = [r for r in rows if r["w"] + r["d"] + r["l"] >= WIN_RATE_MIN]
    rows.sort(key=lambda r: (-key(r), r["pid"]))
    return [(i + 1, key(r), r) for i, r in enumerate(rows)]


def _division(svc, pid):
    """"D1".."D10" for players who have played Online Divisions, else "" (the game shows "?")."""
    db = _db(svc)
    try:
        with svc.accounts.lock, db:
            r = db.execute("SELECT division FROM pes_division WHERE pid=?", (pid,)).fetchone()
    except Exception:  # noqa: BLE001 - no Divisions table yet
        r = None
    return f"D{r[0]}" if r else ""


def _entry(svc, rank, point, r):
    from . import pesmanner, pesprofile
    pr = pesprofile.profile(svc, r["pid"])
    user = {"pid": r["pid"],
            "pname": {"name": r["name"], "xuid_one": "", "is_deleted": "NO",
                      "psn_user_id": {"account_id": r["pid"], "online_id": "", "is_valid_cache": "YES"}},
            # (here manner is a plain string, unlike GET_PLAYERINFO's blob: live 2026-11-07)
            "division": _division(svc, r["pid"]), "manner": pesmanner.rating(svc, r["pid"])["grade"],
            "favoriteTeam": pr.get("team") or "", "favoritePlayer": pr.get("player") or "",
            "intro": pr.get("intro") or "",
            "match_record": {"win_count": r["w"], "draw_count": r["d"], "lose_count": r["l"], "win_s_max": r["best"],
                             "rating": float(r["rating"])}}
    return {"user": user, "rank": rank, "point": int(point)}


def _int(v, default):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


@cmd("CMD_GET_RANKING")
def get_ranking(svc, sess, req):
    rows = ranked(svc, str(req.get("sort") or "RATING").upper())
    start, n = _int(req.get("start_index"), 0), _int(req.get("view_num"), 100) or 100
    page = rows[start:start + min(n, 100)]
    return {"result": NOERR, "date": int(time.time()), "total_rank_num": len(rows),
            "rank_list": [_entry(svc, *x) for x in page]}


@cmd("CMD_GET_RANKING_BYID")
def get_ranking_byid(svc, sess, req):
    rows = ranked(svc, str(req.get("sort") or "RATING").upper())
    pid = _int(req.get("pid"), 0) or getattr(sess, "pes_pid", None)
    at = next((i for i, (_, _, r) in enumerate(rows) if r["pid"] == pid), 0)
    n = _int(req.get("view_num"), 100) or 100
    start = max(0, at - n // 2)
    page = rows[start:start + min(n, 100)]
    return {"result": NOERR, "date": int(time.time()), "total_rank_num": len(rows),
            "rank_list": [_entry(svc, *x) for x in page]}
