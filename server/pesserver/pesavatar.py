"""Player avatars: what a player draws in the game's avatar editor, shown to the others.

How the game uses it (notes/re/functions/menus.md "CMD_GET_AVATAR_HTTP"; static reading 2026-11-07, see the KB):
- The editor is Settings -> Online Settings -> User Profile (main-menu item 0x21). When the player leaves it after
  changing something, the game sends CMD_SET_AVATAR {avatar_data_size, avatar_data, avatar_md5} once over the gate.
  avatar_data is base64 of a 128x128 picture the game renders itself, 3 bytes a pixel (0xC000 = 49152 bytes);
  avatar_md5 is the lowercase hex MD5 of those bytes.
- About 24 online screens (lobby, rooms, matching, profile, ratings, co-op) show other players' avatars. The game
  asks CMD_GET_AVATAR_HTTP {pid} (HTTP form, pid as text) on a cache miss and accepts the picture only if the size
  is 1..0xC000 and the MD5 matches; otherwise it shows the default silhouette.
So the server stores each upload as sent and hands it back unchanged.
"""
import base64, binascii, hashlib, logging

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.avatar")
MAX = 0xC000


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_avatar (pid INTEGER PRIMARY KEY, data TEXT NOT NULL,
                  size INTEGER NOT NULL, md5 TEXT NOT NULL)""")


def _pid(sess, req):
    for v in (getattr(sess, "pes_pid", None), req.get("pid")):
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


@cmd("CMD_SET_AVATAR")
def set_avatar(svc, sess, req):
    pid = getattr(sess, "pes_pid", None)          # only your own avatar, never someone else's pid
    if not isinstance(pid, int):
        return {"result": NOERR}
    data = req.get("avatar_data") or ""
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data).decode("ascii", "replace")
    try:
        raw = base64.b64decode(data, validate=True) if data else b""
    except (binascii.Error, ValueError):
        log.warning("avatar from pid %s is not base64; ignored", pid)
        return {"result": NOERR}
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if not raw:
            db.execute("DELETE FROM pes_avatar WHERE pid=?", (pid,))
        elif len(raw) <= MAX:
            # keep what the game sent: it checks its own md5 against the bytes it gets back
            md5 = str(req.get("avatar_md5") or hashlib.md5(raw).hexdigest()).lower()
            size = int(req.get("avatar_data_size") or len(raw))
            db.execute("INSERT OR REPLACE INTO pes_avatar (pid, data, size, md5) VALUES (?,?,?,?)", (pid, data, size, md5))
    log.info("avatar of pid %s %s (%d bytes)", pid, "saved" if raw else "cleared", len(raw))
    return {"result": NOERR}


@cmd("CMD_GET_AVATAR_HTTP")
def get_avatar(svc, sess, req):
    try:
        pid = int(req.get("pid"))
    except (TypeError, ValueError):
        return {"result": NOERR}
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT data, size, md5 FROM pes_avatar WHERE pid=?", (pid,)).fetchone()
    if not r:
        return {"result": NOERR}                  # the game shows its default silhouette
    return {"result": NOERR, "avatar_data": r[0], "avatar_data_size": r[1], "avatar_md5": r[2]}


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_avatar WHERE pid=?", (pid,))
