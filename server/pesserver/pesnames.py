"""Player names, blocking, and moving a player to a new copy of the mod.

Each copy of the mod is one player (its install id, gate.py / pescmds.account_key). In the game the player
types a name the first time (CMD_CREATE_PLAYER). The server keeps it as typed ("Mohamed Rashad": the game and
the admin page show that) and also stores its handle, lowercased with spaces as "_": mohamed_rashad. Letters
of any language are kept. A name whose handle is the same as another player's, apart from "_" (so apart from
case, spaces, "_", "-" and "."), is refused with the game's own "There is already a User with that name."
(ERR_ALREADYEXISTS), so two accounts never share a name.

- A friend on a new PC or in a fresh game folder is a new copy: their old name is taken by their old self.
  The server remembers that try (a "claim"), the activity feed and the player's panel say so, and the host
  moves the old player (name, rating, results) onto the new copy with one click (move()).
- Block: that copy of the mod can't sign in any more (the game says the account is suspended).

Invites (2026-11-08, removed the same day): an account an invite made ("i:<id>") moves to the mod's install
id the next time that copy signs in.
"""
import logging, re, time, unicodedata

from .accounts import once
from . import pescmds

log = logging.getLogger("pesserver.names")
NAME_MAX = 24        # the handle
SHOWN_MAX = 32       # the name as typed


def shown(raw):
    """The name as the player typed it, tidied: no control characters, single spaces, trimmed."""
    s = unicodedata.normalize("NFKC", str(raw or ""))
    s = "".join(" " if ch.isspace() else ch for ch in s if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    return re.sub(r" +", " ", s).strip()[:SHOWN_MAX].strip()


def clean(raw):
    """The handle: lowercased, spaces / - / . as one "_", letters (any language), digits and _ only."""
    s = unicodedata.normalize("NFKC", str(raw or "")).strip().casefold()
    s = re.sub(r"[\s\-.]+", "_", s)
    s = "".join(ch for ch in s if ch == "_" or ch.isalnum())
    return re.sub(r"_+", "_", s).strip("_")[:NAME_MAX]


def key(name):
    """What "the same name" means: clean(), then without the underscores."""
    return clean(name).replace("_", "")


@once
def _setup(db):
    cols = {r[1] for r in db.execute("PRAGMA table_info(pes_players)")}
    if "blocked" not in cols:
        db.execute("ALTER TABLE pes_players ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    if "handle" not in cols:
        # players named before handles existed: their stored name already is the cleaned form
        db.execute("ALTER TABLE pes_players ADD COLUMN handle TEXT")
        for pid, name in db.execute("SELECT pid, name FROM pes_players WHERE name IS NOT NULL").fetchall():
            db.execute("UPDATE pes_players SET handle=? WHERE pid=?", (clean(name), pid))
    db.execute("""CREATE TABLE IF NOT EXISTS pes_name_claims (username TEXT PRIMARY KEY, wanted TEXT NOT NULL,
                  pid INTEGER NOT NULL, ip TEXT, t REAL NOT NULL)""")


def _db(svc):
    db = pescmds._db(svc)
    if not getattr(svc, "_pes_names_schema", False):     # once per database, like pescmds._db
        with svc.accounts.lock, db:
            _setup(db)
        svc._pes_names_schema = True
    return db


def holder(svc, name, but_username):
    """(pid, name) of the player who already has this name (or one the same apart from case, spaces, _ - .)."""
    k = key(name)
    db = _db(svc)
    with svc.accounts.lock:
        rows = db.execute("SELECT pid, name, handle FROM pes_players WHERE name IS NOT NULL AND username<>?",
                          (but_username,)).fetchall()
    return next(((p, n) for p, n, h in rows if key(h or n) == k), None)


def handle_of(svc, pid):
    db = _db(svc)
    with svc.accounts.lock:
        r = db.execute("SELECT handle, name FROM pes_players WHERE pid=?", (pid,)).fetchone()
    return (r[0] or clean(r[1])) if r and (r[0] or r[1]) else None


def claim(svc, sess, username, wanted, pid, owner):
    """A new copy tried a name another player has: remember it so the host can move that player here."""
    from . import events
    ip = getattr(sess, "peer_ip", None) or ""
    db = _db(svc)
    with svc.accounts.lock, db:
        seen = db.execute("SELECT 1 FROM pes_name_claims WHERE username=? AND wanted=? AND pid=?",
                          (username, wanted, pid)).fetchone()
        db.execute("INSERT OR REPLACE INTO pes_name_claims (username, wanted, pid, ip, t) VALUES (?,?,?,?,?)",
                   (username, wanted, pid, ip, time.time()))
    if seen:                                           # the same copy trying the same name again: one note is enough
        return
    events.feed(svc, f"A new copy of the mod{f' ({ip})' if ip else ''} asked for the name {wanted}, which {owner} has. "
                     f"If it's {owner} on a new PC or game folder, open {owner} on Players and move them there.", "warn")


def claims_for(svc, pid):
    db = _db(svc)
    with svc.accounts.lock:
        rows = db.execute("SELECT username, wanted, ip, t FROM pes_name_claims WHERE pid=? ORDER BY t DESC", (pid,)).fetchall()
    return [{"copy": u, "wanted": w, "ip": ip, "t": t} for u, w, ip, t in rows]


def move(svc, pid, copy):
    """The player pid now signs in with the new copy `copy` (a pes_players username that tried their name).
    That copy's own, nameless account goes; pid keeps its name, rating and results."""
    from . import events
    db = _db(svc)

    def look():
        c = db.execute("SELECT 1 FROM pes_name_claims WHERE username=? AND pid=?", (copy, pid)).fetchone()
        old = db.execute("SELECT name, username FROM pes_players WHERE pid=?", (pid,)).fetchone()
        new = db.execute("SELECT pid, name, steamid FROM pes_players WHERE username=?", (copy,)).fetchone()
        if not c or not old:
            raise LookupError("That request is gone.")
        if new and new[1]:
            raise ValueError(f"That copy already plays as {new[1]}.")
        return old, new

    with svc.accounts.lock:
        old, new = look()
    if new:                                            # the copy's own nameless account, and anything kept for it
        pescmds.purge_player(svc, new[0])
    with svc.accounts.lock, db:
        old, gone = look()                             # (again: the lock was let go for the purge)
        if gone:
            raise ValueError("That copy signed in again meanwhile. Try again.")
        db.execute("UPDATE pes_players SET username=?, steamid=COALESCE(?, steamid) WHERE pid=?",
                   (copy, new[2] if new else None, pid))
        db.execute("DELETE FROM pes_name_claims WHERE username=? OR pid=?", (copy, pid))
    for p in (pid, new[0] if new else None):           # their game signs in again with the moved account
        sess = svc.online.get(p) if p is not None else None
        if sess is not None:
            sess.kicked = True
            if hasattr(sess, "close"):
                sess.close()
    log.info("player %s moved to copy %s…", pid, copy[:10])
    events.feed(svc, f"{old[0]} now plays from their new copy of the mod", "info", [pid])
    return {"pid": pid, "name": old[0]}


def set_blocked(svc, pid, on):
    from . import events
    db = _db(svc)
    with svc.accounts.lock, db:
        r = db.execute("SELECT name FROM pes_players WHERE pid=?", (pid,)).fetchone()
        if not r:
            raise LookupError("That player is gone.")
        db.execute("UPDATE pes_players SET blocked=? WHERE pid=?", (1 if on else 0, pid))
    sess = svc.online.get(pid)
    if on and sess is not None:
        sess.kicked = True
        if hasattr(sess, "close"):
            sess.close()
    events.feed(svc, f"The admin blocked {r[0] or 'Player %d' % pid}" if on else f"The admin unblocked {r[0] or 'Player %d' % pid}", "info", [pid])
    return {"pid": pid, "blocked": bool(on)}


def blocked(svc, username):
    db = _db(svc)
    with svc.accounts.lock:
        r = db.execute("SELECT blocked FROM pes_players WHERE username=?", (username,)).fetchone()
    return bool(r and r[0])


def is_blocked(svc, pid):
    db = _db(svc)
    with svc.accounts.lock:
        r = db.execute("SELECT blocked FROM pes_players WHERE pid=?", (pid,)).fetchone()
    return bool(r and r[0])


def named(svc, username):
    """That copy picked a name of its own: its claims on other players' names are over."""
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("DELETE FROM pes_name_claims WHERE username=?", (username,))


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("DELETE FROM pes_name_claims WHERE pid=?", (pid,))


def from_invite(svc, sess, key_now):
    """A copy made with an invite (the removed invites): its account "i:<id>" becomes the install id's."""
    code = getattr(sess, "mod_invite", None)
    if not code:
        return
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        try:
            r = db.execute("SELECT id FROM pes_invites WHERE code=?", (code,)).fetchone()
        except Exception:  # noqa: BLE001  (no invites table: nothing to move)
            return
        if not r or db.execute("SELECT 1 FROM pes_players WHERE username=?", (key_now,)).fetchone():
            return
        if db.execute("UPDATE pes_players SET username=? WHERE username=?", (key_now, f"i:{r[0]}")).rowcount:
            log.info("invite %s: its player now signs in with the install id", r[0])
