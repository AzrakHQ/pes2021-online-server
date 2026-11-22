"""Which mod version each player's game runs, for the admin page (pes_players.mod_version).

The mod's id line (gate.py) says what it can do, not its version; its web requests say the version in the
user agent ("PES2021Online/0.8.1", every mod). /mod/final and /mod/bracket carry the install id (mod 0.7.0+);
/mod/hello, which every mod sends when the game starts, carries none, so it is remembered by address until that
game's id line arrives on the game server (from the same address, seconds later).
"""
import os
import re
import time

UA = re.compile(r"PES2021Online/(\d{1,3}\.\d{1,3}\.\d{1,3})")
HELLO_FRESH = 900          # seconds a /mod/hello stays matched to its address
_BY_IP = {}                # address -> (version, when) from /mod/hello
_SERVER = {}               # mod file path -> (mtime, version)


def from_headers(headers):
    m = UA.fullmatch((headers or {}).get("user-agent", "").strip())
    return m.group(1) if m else None


def hello(ip, ver):
    if not ip or not ver:
        return
    now = time.time()
    if len(_BY_IP) > 512:
        for a in [a for a, (_, t) in _BY_IP.items() if now - t > HELLO_FRESH]:
            _BY_IP.pop(a, None)
    _BY_IP[ip] = (ver, now)


def of_ip(ip):
    v = _BY_IP.get(ip or "")
    return v[0] if v and time.time() - v[1] < HELLO_FRESH else None


def save(svc, mod_id, ver):
    """Remember that install's version (its account is "m:<install id>", pescmds.account_key). Writes only when
    it changed; a player not created yet gets it at login (pescmds.login)."""
    if not mod_id or not ver:
        return
    saved = svc.__dict__.setdefault("_mod_versions", {})
    if saved.get(mod_id) == ver:
        return
    from . import pescmds
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        n = db.execute("UPDATE pes_players SET mod_version=? WHERE username=?", (ver, "m:" + mod_id)).rowcount
    if n:
        saved[mod_id] = ver


def seen(svc, sess, ver):
    """A version from a request that named this game's session (or its address)."""
    if ver and sess is not None:
        sess.mod_version = ver
        save(svc, getattr(sess, "mod_id", None), ver)


def server_version(path):
    """The version of the mod file this server hands out (its user agent string, UTF-16 in the DLL)."""
    try:
        mt = os.path.getmtime(path)
        if _SERVER.get(path, (None,))[0] != mt:
            with open(path, "rb") as f:
                data = f.read()
            m = re.search(rb"P\x00E\x00S\x002\x000\x002\x001\x00O\x00n\x00l\x00i\x00n\x00e\x00/\x00((?:[0-9.]\x00){5,11})", data)
            _SERVER[path] = (mt, m.group(1).decode("utf-16-le") if m else None)
        return _SERVER[path][1]
    except OSError:
        return None
