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
    # "Courtesy Rating" on the profile: manner is a blob{manner, disconnect_rate}; a plain string here crashes the
    # game when the profile opens (live). Without it every player shows "C". disconnect_rate ("Number of
    # Disconnections") is left out: Low is what it shows then, and the values that change it are not known yet.
    manner = msgpack.packb({"manner": pesmanner.rating(svc, who)["grade"]}, use_bin_type=True)
    return {"result": NOERR, "pid": who, "pname": _pname(p), "favoriteTeam": pr.get("team") or "",
            "favoritePlayer": pr.get("player") or "", "intro": pr.get("intro") or "", "manner": manner}


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_profile WHERE pid=?", (pid,))
