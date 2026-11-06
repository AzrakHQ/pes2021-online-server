"""Courtesy (fair-play) rating: the game's own warnings for players who keep leaving matches early.

How the game uses it (notes/re/functions/menus.md, messages.md, match.md):
- CMD_GET_MANNER_WARN is asked at the online login and when entering an online mode. Its reply's `warn_kind` opens one
  of four built-in dialogs: DOWN_B (leaving matches early is increasing), A_TO_B (the rating fell to "B"), DOWN_C
  (it fell to "C"), STILL_C (it is still "C"). Anything else, or no `warn_kind`, shows nothing.
- The game reports a self-caused early end itself, with CMD_SEND_ABNORMALEND {reason}: GIVEUP / EXIT (quit from the
  pause menu) or NO_OPERATION (no input for about a minute). The other player's game then reports a connection
  problem (DISCONN_P2P / DISCONN_SC), which is not that player's fault.
- Every complete match has one DISCONN_P2P report too (the first player to leave the full-time screen cuts the
  link), so reports after full time are ignored.

Our rule (the game leaves it to the server): over each player's last RECENT matches that kicked off,
  a match left early = the player's game reported GIVEUP / EXIT / NO_OPERATION, or the player's connection to the
  server dropped while the match was running (game closed);
  rating A with at most 1 such match, B with 2-3, C with 4 or more.
A connection problem between the two players, or the other player leaving, never counts against anyone.
Each warning is shown once: DOWN_B after each new early exit while still A, A_TO_B / DOWN_C when the rating
falls, STILL_C at most once a day while it stays C. The rating is also the co-op "courtesy rating" (pescoop).
"""
import logging, time

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.manner")

RECENT = 10                       # matches looked at
SELF_REASONS = {"GIVEUP", "EXIT", "NO_OPERATION"}
STILL_C_EVERY = 24 * 3600


def grade_for(quits):
    return "A" if quits <= 1 else "B" if quits <= 3 else "C"


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_manner (id INTEGER PRIMARY KEY, pid INTEGER, t REAL,
                  left_early INTEGER, reason TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pes_manner_state (pid INTEGER PRIMARY KEY, grade TEXT,
                  warned_quits INTEGER DEFAULT 0, still_c_t REAL DEFAULT 0)""")


def _add(svc, pid, left_early, reason=""):
    if not isinstance(pid, int):
        return
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("INSERT INTO pes_manner (pid, t, left_early, reason) VALUES (?,?,?,?)",
                   (pid, time.time(), int(left_early), reason))
    if left_early:
        log.info("pid %s left a match early (%s): %s", pid, reason, rating(svc, pid))


def rating(svc, pid):
    """{"grade", "quits", "matches"} over the player's last RECENT matches."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT left_early FROM pes_manner WHERE pid=? ORDER BY id DESC LIMIT ?",
                          (pid, RECENT)).fetchall()
    quits = sum(r[0] for r in rows)
    return {"grade": grade_for(quits), "quits": quits, "matches": len(rows)}


# ---- fed by events.py (it already follows every running match) -------------------------------------
def match_end(svc, lv):
    """Both games reached full time (CHANGE_GAMEPHASE END): a complete match for both."""
    if lv.get("manner_done") or not lv.get("kickoff"):
        return
    lv["manner_done"] = True
    for pid in (lv.get("home"), lv.get("away")):
        _add(svc, pid, False)


def abnormal_end(svc, lv, pid, reason):
    """CMD_SEND_ABNORMALEND from pid's own game while the match was running."""
    if lv.get("manner_done") or not lv.get("kickoff") or lv.get("end_seen"):
        return
    if str(reason).upper() in SELF_REASONS:
        lv["manner_done"] = True
        _add(svc, pid, True, str(reason).upper())


def dropped(svc, lv, pid):
    """pid's connection to the server closed while the match was running."""
    if lv.get("manner_done") or not lv.get("kickoff") or lv.get("end_seen"):
        return
    lv["manner_done"] = True
    _add(svc, pid, True, "DISCONNECTED")


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_manner WHERE pid=?", (pid,))
        db.execute("DELETE FROM pes_manner_state WHERE pid=?", (pid,))


# ---- the warning ------------------------------------------------------------------------------------
def warning(svc, pid, now=None):
    """The warn_kind to show now (or None), and remember that it was shown."""
    now = now or time.time()
    r = rating(svc, pid)
    g, q = r["grade"], r["quits"]
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        st = db.execute("SELECT grade, warned_quits, still_c_t FROM pes_manner_state WHERE pid=?", (pid,)).fetchone()
        old, warned, still_t = st if st else ("A", 0, 0)
        kind = None
        if g != old:
            if g == "C":
                kind = "DOWN_C"
            elif g == "B" and old == "A":
                kind = "A_TO_B"
            # a better rating: no dialog
        elif g == "C":
            if now - still_t >= STILL_C_EVERY:
                kind = "STILL_C"
        elif q > warned:
            kind = "DOWN_B"
        if kind in ("DOWN_C", "STILL_C"):
            still_t = now
        db.execute("INSERT OR REPLACE INTO pes_manner_state (pid, grade, warned_quits, still_c_t) VALUES (?,?,?,?)",
                   (pid, g, q, still_t))
    return kind


@cmd("CMD_GET_MANNER_WARN")
def get_manner_warn(svc, sess, req):
    pid = req.get("pid") or getattr(sess, "pes_pid", None)
    if not isinstance(pid, int):
        return {"result": NOERR}
    kind = warning(svc, pid)
    if not kind:
        return {"result": NOERR}
    log.info("courtesy warning %s for pid %s", kind, pid)
    return {"result": NOERR, "warn_kind": kind}


@cmd("CMD_SEND_ABNORMALEND")
def send_abnormalend(svc, sess, req):
    return {"result": NOERR}             # events.observe looks at the reason
