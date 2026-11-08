"""Invites: every friend gets their own copy of the mod with a handle ("Nutmeg Ninja") and a secret code in it.

The host makes an invite (admin API, later the dashboard), sends the friend the download link
/mod/dinput8.dll?invite=<code>, and the server knows that friend by the code from then on: on any PC, in any
game folder, after a reinstall. The mod sends the code on its id line (gate.py, "inv=<32 hex>"), and only to
the server it was made for (mod 0.6.0, /mod/hello version 4).

- Invite only (a host setting, off by default): a newcomer without an invite gets "Unable to login" and the
  host sees a line in the activity feed. Players the server already knew keep playing as before.
- Revoking an invite is the ban: that copy of the mod can't sign in any more ("account suspended").
- The handle identifies the player for the host; in the game players still choose their own name
  (CREATE_PLAYER), and two players can't take the same one.
- An invite can be made for a player the server already has: their first sign-in with it moves their
  account (name, rating, results) onto the invite.
"""
import hmac, logging, os, random, re, time

from . import pescmds

log = logging.getLogger("pesserver.invite")

KEY_PREFIX = "i:"                 # pes_players.username of an invited player: "i:<invite id>"
HANDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.'-]{1,22}[A-Za-z0-9.']")

_FIRST = ["Nutmeg", "Panenka", "Rabona", "Offside", "Volley", "Bicycle", "Header", "Tiki-Taka", "Sweeper",
          "Libero", "Trivela", "Elastico", "Scorpion", "Chip", "Toe-Poke", "Back-Heel", "Stoppage", "Golden",
          "Hat-Trick", "Corner", "Sliding", "Dummy", "Rainbow", "Long-Ball", "Park-the-Bus", "Gegen", "Wonder",
          "Screamer", "Top-Bin", "Own-Goal"]
_SECOND = ["Ninja", "Panda", "Wizard", "Owl", "Llama", "Pirate", "Badger", "Falcon", "Goblin", "Penguin",
           "Viking", "Walrus", "Yeti", "Dragon", "Hedgehog", "Moose", "Otter", "Gecko", "Raccoon", "Koala",
           "Camel", "Lobster", "Pigeon", "Wombat", "Ferret", "Narwhal", "Hamster", "Toucan", "Bison", "Sloth"]


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_invites (id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL,
                  handle TEXT UNIQUE NOT NULL COLLATE NOCASE, pid INTEGER, created REAL NOT NULL,
                  revoked REAL, last_seen REAL)""")
    db.execute("CREATE TABLE IF NOT EXISTS pes_invite_cfg (k TEXT PRIMARY KEY, v TEXT)")


def _db(svc):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        _setup(db)
    return db


_COLS = ("id", "code", "handle", "pid", "created", "revoked", "last_seen")


def _row(r):
    return dict(zip(_COLS, r)) if r else None


def _one(svc, where, args):
    db = _db(svc)
    with svc.accounts.lock:
        return _row(db.execute(f"SELECT {', '.join(_COLS)} FROM pes_invites WHERE {where}", args).fetchone())


def by_code(svc, code):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9a-f]{32}", code):
        return None
    inv = _one(svc, "code=?", (code,))
    return inv if inv and hmac.compare_digest(inv["code"], code) else None


def by_pid(svc, pid):
    return _one(svc, "pid=? ORDER BY id DESC", (pid,))


def invites_only(svc):
    db = _db(svc)
    with svc.accounts.lock:
        r = db.execute("SELECT v FROM pes_invite_cfg WHERE k='invites_only'").fetchone()
    return r is not None and r[0] == "1"     # off until the host turns it on (no invites panel on the dashboard yet)


def set_invites_only(svc, on):
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("INSERT OR REPLACE INTO pes_invite_cfg (k, v) VALUES ('invites_only', ?)", ("1" if on else "0",))


def _taken(db, handle, but=None):
    r = db.execute("SELECT id FROM pes_invites WHERE handle=?", (handle,)).fetchone()
    return r is not None and r[0] != but


def funny_handle(svc, rng=random):
    db = _db(svc)
    with svc.accounts.lock:
        for _ in range(200):
            h = f"{rng.choice(_FIRST)} {rng.choice(_SECOND)}"
            if not _taken(db, h):
                return h
        n = db.execute("SELECT COUNT(*) FROM pes_invites").fetchone()[0]
    return f"{rng.choice(_FIRST)} {rng.choice(_SECOND)} {n + 1}"


def _clean_handle(h):
    h = re.sub(r"\s+", " ", str(h or "")).strip()
    if not HANDLE_RE.fullmatch(h):
        raise ValueError("A handle is 3–24 letters, digits, spaces or _ . ' -")
    return h


def create(svc, handle=None, pid=None):
    """A new invite. handle: the host's choice (else a funny one); pid: an existing player it is for."""
    handle = _clean_handle(handle) if handle else funny_handle(svc)
    db = _db(svc)
    with svc.accounts.lock, db:
        if _taken(db, handle):
            raise ValueError(f"“{handle}” is already someone's handle")
        if pid is not None:
            if not db.execute("SELECT 1 FROM pes_players WHERE pid=?", (pid,)).fetchone():
                raise LookupError("That player is gone.")
            if db.execute("SELECT 1 FROM pes_invites WHERE pid=? AND revoked IS NULL", (pid,)).fetchone():
                raise ValueError("That player already has an invite.")
        code = os.urandom(16).hex()
        cur = db.execute("INSERT INTO pes_invites (code, handle, pid, created) VALUES (?,?,?,?)",
                         (code, handle, pid, time.time()))
    log.info("INVITE %d made: %s%s", cur.lastrowid, handle, f" (for pid {pid})" if pid else "")
    return _one(svc, "id=?", (cur.lastrowid,))


def rename(svc, iid, handle):
    handle = _clean_handle(handle)
    db = _db(svc)
    with svc.accounts.lock, db:
        if _taken(db, handle, but=iid):
            raise ValueError(f"“{handle}” is already someone's handle")
        if db.execute("UPDATE pes_invites SET handle=? WHERE id=?", (handle, iid)).rowcount == 0:
            raise LookupError("That invite is gone.")
    return _one(svc, "id=?", (iid,))


def revoke(svc, iid, back=False):
    """Revoke (ban) or, with back=True, let the invite in again. Returns the invite."""
    db = _db(svc)
    with svc.accounts.lock, db:
        n = db.execute("UPDATE pes_invites SET revoked=? WHERE id=?", (None if back else time.time(), iid)).rowcount
    if n == 0:
        raise LookupError("That invite is gone.")
    inv = _one(svc, "id=?", (iid,))
    sess = svc.online.get(inv["pid"]) if inv["pid"] is not None and not back else None
    if sess is not None and getattr(sess, "mod_invite", None) == inv["code"]:
        sess.kicked = True                      # not an early exit (no forfeit, no courtesy mark)
        if hasattr(sess, "close"):
            sess.close()
    return inv


def delete(svc, iid):
    db = _db(svc)
    with svc.accounts.lock, db:
        if db.execute("DELETE FROM pes_invites WHERE id=?", (iid,)).rowcount == 0:
            raise LookupError("That invite is gone.")


def forget(svc, pid):
    """A deleted player: their invite stays (they can come back and are asked for a name again)."""
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_invites SET pid=NULL WHERE pid=?", (pid,))


def handle_of(svc, pid):
    inv = by_pid(svc, pid) if pid is not None else None
    return inv["handle"] if inv else None


def _feed_once(svc, key, text):
    from . import events
    seen = svc.__dict__.setdefault("_invite_notes", {})
    if time.time() - seen.get(key, 0) < 600:
        return
    seen[key] = time.time()
    events.feed(svc, text, "warn")


def account_for_login(svc, sess, default_key):
    """CMD_LOGIN: the pes_players username this sign-in belongs to, or an ERR_* result for the game.

    - With an invite: its account ("i:<id>"); a linked old account is moved onto it the first time.
    - Without: the install id / game user key as before, but under invites-only only if the server knows
      that player already."""
    code = getattr(sess, "mod_invite", None)
    db = _db(svc)
    if code:
        inv = by_code(svc, code)
        if inv is None:
            _feed_once(svc, "bad", "A mod with an unknown invite tried to sign in (an invite that was deleted?)")
            return None, "ERR_LOGIN_FAILED"
        if inv["revoked"] is not None:
            _feed_once(svc, f"rev{inv['id']}", f"{inv['handle']} tried to sign in, but their invite is revoked")
            return None, "ERR_INVALID_ACCOUNT"
        key = KEY_PREFIX + str(inv["id"])
        with svc.accounts.lock, db:
            if inv["pid"] is not None and not db.execute("SELECT 1 FROM pes_players WHERE username=?", (key,)).fetchone():
                # an invite made for a player the server already had: their account moves onto it
                db.execute("UPDATE pes_players SET username=? WHERE pid=?", (key, inv["pid"]))
                log.info("INVITE %d: player %s now signs in with it", inv["id"], inv["pid"])
            db.execute("UPDATE pes_invites SET last_seen=? WHERE id=?", (time.time(), inv["id"]))
        return key, None
    if invites_only(svc):
        with svc.accounts.lock:
            known = db.execute("SELECT 1 FROM pes_players WHERE username=?", (default_key,)).fetchone()
        if not known:
            where = getattr(sess, "peer_ip", None) or "?"
            _feed_once(svc, f"no:{where}", f"Someone without an invite tried to sign in (from {where}). "
                       "Make them an invite and send them their own mod link.")
            log.info("LOGIN refused: no invite (%s)", where)
            return None, "ERR_LOGIN_FAILED"
    return default_key, None


def after_login(svc, key, pid):
    """Remember which player an invite belongs to (first sign-in with a new invite)."""
    if key.startswith(KEY_PREFIX):
        db = _db(svc)
        with svc.accounts.lock, db:
            db.execute("UPDATE pes_invites SET pid=? WHERE id=? AND pid IS NULL", (pid, int(key[len(KEY_PREFIX):])))


# ---- admin API ---------------------------------------------------------------------------------------------
def _public(svc, inv, base=""):
    from .pescmds import _db as _pdb
    name = None
    if inv["pid"] is not None:
        r = _pdb(svc).execute("SELECT name FROM pes_players WHERE pid=?", (inv["pid"],)).fetchone()
        name = r[0] if r else None
    return {"id": inv["id"], "handle": inv["handle"], "pid": inv["pid"], "name": name, "created": inv["created"],
            "last_seen": inv["last_seen"], "revoked": inv["revoked"],
            "online": inv["pid"] is not None and inv["pid"] in svc.online,
            "link": f"{base}/mod/dinput8.dll?invite={inv['code']}"}


def snapshot(svc, base=""):
    db = _db(svc)
    with svc.accounts.lock:
        rows = [_row(r) for r in db.execute(f"SELECT {', '.join(_COLS)} FROM pes_invites ORDER BY id")]
        uninvited = [{"pid": p, "name": n} for p, n in db.execute(
            "SELECT pid, name FROM pes_players WHERE username NOT LIKE 'i:%' ORDER BY pid")]
    return {"invites_only": invites_only(svc), "invites": [_public(svc, r, base) for r in rows],
            "players_without_invite": uninvited}


def admin_api(svc, route, method, body, base=""):
    """GET /admin/api/invites; POST /admin/api/invites/new {handle?, pid?}, /rename {id, handle},
    /revoke {id, back?}, /delete {id}, /settings {invites_only}. Returns (status, json-able body)."""
    import json
    if route == "/admin/api/invites" and method == "GET":
        return "200 OK", snapshot(svc, base)
    if method != "POST":
        return "405 Method Not Allowed", {"error": "method"}
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        return "400 Bad Request", {"error": "bad request"}
    try:
        if route == "/admin/api/invites/new":
            pid = req.get("pid")
            inv = create(svc, req.get("handle") or None, int(pid) if pid not in (None, "") else None)
            return "200 OK", _public(svc, inv, base)
        if route == "/admin/api/invites/settings":
            set_invites_only(svc, bool(req.get("invites_only")))
            return "200 OK", snapshot(svc, base)
        iid = int(req.get("id"))
        if route == "/admin/api/invites/rename":
            return "200 OK", _public(svc, rename(svc, iid, req.get("handle")), base)
        if route == "/admin/api/invites/revoke":
            return "200 OK", _public(svc, revoke(svc, iid, bool(req.get("back"))), base)
        if route == "/admin/api/invites/delete":
            delete(svc, iid)
            return "200 OK", {"ok": True}
    except LookupError as e:
        return "404 Not Found", {"error": str(e).strip("'")}
    except (ValueError, TypeError) as e:
        return "400 Bad Request", {"error": str(e) or "bad request"}
    return "404 Not Found", {"error": "no such route"}
