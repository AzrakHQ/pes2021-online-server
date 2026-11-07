"""Player profiles: favourite team, favourite player and comment (Settings -> Online Settings -> Edit Profile).

How the game uses it (live 2026-11-07, notes/re/functions/menus.md "Profile"):
- Leaving Edit Profile after a change sends CMD_SET_PLAYERPROFILE {profile: blob{date, favorite_team,
  favorite_player, intro, is_enable_myPES}}. All three texts are free text typed by the player.
- Opening a profile (your own in Edit Profile, other players' from user lists) sends CMD_GET_PLAYERINFO
  {user_id: blob{pid, pname}}. The reply's top-level `pname` blob, `favoriteTeam`, `favoritePlayer` and `intro`
  are what the screen shows (checked on the game's parser with tools/parsebench.py); missing ones show as
  "NotImplement". Other players' profiles (e.g. from the Match Block List) also show Courtesy Rating from
  manner: blob{manner: "C".."AAA"} and Number of Disconnections from manner.disconnect_rate.
"""
import logging, time

import msgpack

from .pescmds import NOERR, cmd, _db, _pname, _player
from . import pesmatch

log = logging.getLogger("pesserver.profile")
LIMIT = 200


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_profile (pid INTEGER PRIMARY KEY, team TEXT, player TEXT, intro TEXT,
                  t REAL)""")


def _blob(v):
    """A nested map the gate layer handed over as bytes or as a surrogate-escaped str."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        v = v.encode("utf-8", "surrogateescape")
    if isinstance(v, (bytes, bytearray)):
        try:
            out = msgpack.unpackb(bytes(v), raw=False, strict_map_key=False, unicode_errors="replace")
            return out if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _text(v):
    if isinstance(v, (bytes, bytearray)):
        v = bytes(v).decode("utf-8", "replace")
    v = "".join(c for c in str(v or "") if c.isprintable() or c == "\n").strip()
    return "" if v == "NotImplement" else v[:LIMIT]


@cmd("CMD_SET_PLAYERPROFILE")
def set_playerprofile(svc, sess, req):
    pid = getattr(sess, "pes_pid", None)
    if not isinstance(pid, int):
        return {"result": NOERR}
    prof = _blob(req.get("profile"))
    vals = (_text(prof.get("favorite_team")), _text(prof.get("favorite_player")), _text(prof.get("intro")))
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("INSERT OR REPLACE INTO pes_profile (pid, team, player, intro, t) VALUES (?,?,?,?,?)",
                   (pid, *vals, time.time()))
    log.info("profile of pid %s saved: team %r, player %r", pid, vals[0], vals[1])
    return {"result": NOERR}


def profile(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT team, player, intro FROM pes_profile WHERE pid=?", (pid,)).fetchone()
    return dict(zip(("team", "player", "intro"), r)) if r else {}


@cmd("CMD_GET_PLAYERINFO")
def get_playerinfo(svc, sess, req):
    who = _blob(req.get("user_id")).get("pid")
    if not isinstance(who, int):
        who = getattr(sess, "pes_pid", None)
    if not isinstance(who, int):
        return {"result": NOERR}
    p = _player(svc, pesmatch._username(svc, who))
    if not p:
        return {"result": NOERR}
    pr = profile(svc, who)
    from . import pesmanner
    # "Courtesy Rating" / "Number of Disconnections" on the profile: manner is a blob{manner, disconnect_rate}; a
    # plain string here crashes the game when the profile opens (live). Without it every player shows "C".
    rt = pesmanner.rating(svc, who)
    # disconnect_rate names (table 0x1437f33f0): LOW_RATE = Low, SEMI_HIGH_RATE = Moderately High, HIGH_RATE = High;
    # anything else shows Low. Same early-exit counts as the courtesy grade.
    rate = "LOW_RATE" if rt["quits"] <= 1 else "SEMI_HIGH_RATE" if rt["quits"] <= 3 else "HIGH_RATE"
    manner = msgpack.packb({"manner": rt["grade"], "disconnect_rate": rate}, use_bin_type=True)
    out = {"result": NOERR, "pid": who, "pname": _pname(p), "favoriteTeam": pr.get("team") or "",
           "favoritePlayer": pr.get("player") or "", "intro": pr.get("intro") or "", "manner": manner}
    rs = _rankmatch_stats(svc, who, p)
    if rs:
        out["rankmatch_stats"] = msgpack.packb(rs, use_bin_type=True)
    md = _matchday_stats(svc, who)
    if md:
        out["matchday_stats_single"] = msgpack.packb(md, use_bin_type=True)
    return out


def _matchday_stats(svc, pid):
    """The profile's Matchday page: events entered and the record of their matches (pesmatchday tables)."""
    db = _db(svc)
    try:
        with svc.accounts.lock, db:
            n = db.execute("SELECT COUNT(*) FROM pes_matchday WHERE pid=?", (pid,)).fetchone()[0]
            pts = [r[0] for r in db.execute("SELECT points FROM pes_matchday_match WHERE pid=?", (pid,))]
    except Exception:  # noqa: BLE001 - no Matchday yet
        return None
    if not n:
        return None
    return {"entry_count": n, "group_win_count": 0, "pickup_count": 0, "best_rank": 0,
            "win_count": sum(1 for x in pts if x >= 3), "draw_count": sum(1 for x in pts if 0 < x < 3),
            "lose_count": sum(1 for x in pts if x <= 0)}


def _rankmatch_stats(svc, pid, p):
    """The profile's Online Divisions page: career record in Online Divisions (pesdivisions tables)."""
    db = _db(svc)
    try:
        with svc.accounts.lock, db:
            d = db.execute("SELECT division, wins, draws, losses, best_streak, streak FROM pes_division WHERE pid=?",
                           (pid,)).fetchone()
            if not d:
                return None
            rt = db.execute("SELECT rating FROM pes_players WHERE pid=?", (pid,)).fetchone()
            g = db.execute("SELECT COALESCE(SUM(CASE WHEN my_side='HOME' THEN home_score ELSE away_score END), 0),"
                           " COALESCE(SUM(CASE WHEN my_side='HOME' THEN away_score ELSE home_score END), 0)"
                           " FROM pes_division_match WHERE pid=?", (pid,)).fetchone()
    except Exception:  # noqa: BLE001 - nobody has played Online Divisions yet
        return None
    div, w, dr, l, best, cur = d
    return {"division": f"D{div}", "rating": int((rt or [1000])[0] or 1000), "max_weekly_point": 0,
            "current_weekly_point": 0, "match_num": w + dr + l, "win_num": w, "lose_num": l, "draw_num": dr,
            "contWin_num": cur, "contWinMax_num": best, "goal": g[0], "lostGoal": g[1]}


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_profile WHERE pid=?", (pid,))
