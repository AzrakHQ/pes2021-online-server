"""Courtesy (fair-play) rating: the game's own warnings for players who keep leaving matches early.

How the game uses it (notes/re/functions/menus.md, messages.md, match.md):
- CMD_GET_MANNER_WARN is asked at the online login and when entering an online mode. Its reply's `warn_kind` opens one
  of four built-in dialogs. The names don't say what the texts say (all four seen live, 2026-11-07):
    DOWN_B   "The number of times you have failed to properly complete matches is increasing. ..."
    A_TO_B   "Your courtesy rating has fallen to "B". ..."
    DOWN_C   "The number of times you have failed to properly complete matches remains high. ..."
    STILL_C  "Your courtesy rating has fallen to "C". ..."
  Anything else, or no `warn_kind`, shows nothing; the game then carries on into the mode.
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
Each warning is shown once, after the early exit that caused it: still A -> DOWN_B; falls to B -> A_TO_B;
at B -> DOWN_C; falls to C, or another early exit at C -> STILL_C. A better rating shows nothing.
The rating is also the co-op "courtesy rating" (pescoop).
"""
import logging, time

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.manner")

RECENT = 10                       # matches looked at
SELF_REASONS = {"GIVEUP", "EXIT", "NO_OPERATION"}


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
        row = db.execute("INSERT INTO pes_manner (pid, t, left_early, reason) VALUES (?,?,?,?)",
                         (pid, time.time(), int(left_early), reason)).lastrowid
    if left_early:
        log.info("pid %s left a match early (%s): %s", pid, reason, rating(svc, pid))
    return row


def _take_back(svc, row):
    if row is None:
        return
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("DELETE FROM pes_manner WHERE id=?", (row,))


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
def _marked(lv):
    return lv.setdefault("manner_marked", set())


def match_end(svc, lv):
    """Both games reached full time (CHANGE_GAMEPHASE END): a complete match for whoever wasn't marked."""
    if lv.get("manner_done") or not lv.get("kickoff"):
        return
    lv["manner_done"] = True
    for pid in (lv.get("home"), lv.get("away")):
        if pid not in _marked(lv):
            _add(svc, pid, False)


DECIDE_AFTER = 15        # s: both games may report an idle end a few ms apart; decide once all reports are in


def _schedule(svc, lv):
    """After the first early exit of a match: in DECIDE_AFTER s, if exactly one player left early, that player
    forfeits the match (pesmatch.forfeit)."""
    if lv.get("forfeit_scheduled"):
        return
    lv["forfeit_scheduled"] = True
    from . import pesmatch
    room = pesmatch._state(svc)["rooms"].get(lv.get("room"))
    if room is not None:
        room["quit_pending"] = True          # no normal verdict while the forfeit is being decided

    def decide():
        marked = _marked(lv)
        if room is not None:
            room.pop("quit_pending", None)
        back = lv.get("dropped_pid")
        if back in marked and getattr(svc.online.get(back), "pes_reconnected", False):
            marked.discard(back)                 # only the connection to the server blinked: the match goes on
            _take_back(svc, lv.pop("dropped_row", None))
            log.info("pid %s reconnected in time: no early exit, no forfeit", back)
        if len(marked) == 1:
            from . import pesmatch
            try:
                pesmatch.forfeit(svc, lv.get("room"), next(iter(marked)))
            except Exception:  # noqa: BLE001 - must never break the server
                log.exception("forfeit")
    import asyncio
    try:
        asyncio.get_running_loop().call_later(DECIDE_AFTER, decide)
    except RuntimeError:            # no loop (unit tests call this directly)
        lv["forfeit_decide"] = decide


def abnormal_end(svc, lv, pid, reason):
    """CMD_SEND_ABNORMALEND from pid's own game while the match was running. Every player whose own game reports a
    self-caused end is marked (live 2026-11-07: with both players idle, both games report NO_OPERATION within a
    few ms). After any such report the match is over, so a connection that drops later doesn't count."""
    if lv.get("manner_done") or not lv.get("kickoff") or lv.get("end_seen"):
        return
    lv["abnormal"] = True
    if str(reason).upper() in SELF_REASONS and pid not in _marked(lv):
        _marked(lv).add(pid)
        _add(svc, pid, True, str(reason).upper())
        _schedule(svc, lv)


def dropped(svc, lv, pid):
    """pid's connection to the server closed while the match was still running (no early-end report yet)."""
    if lv.get("manner_done") or not lv.get("kickoff") or lv.get("end_seen") or lv.get("abnormal"):
        return
    lv["manner_done"] = True
    _marked(lv).add(pid)
    lv["dropped_pid"], lv["dropped_row"] = pid, _add(svc, pid, True, "DISCONNECTED")
    _schedule(svc, lv)


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_manner WHERE pid=?", (pid,))
        db.execute("DELETE FROM pes_manner_state WHERE pid=?", (pid,))


# ---- the warning ------------------------------------------------------------------------------------
ORDER = "ABC"


def warning(svc, pid):
    """The warn_kind to show now (or None), and remember that it was shown."""
    r = rating(svc, pid)
    g, q = r["grade"], r["quits"]
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        st = db.execute("SELECT grade, warned_quits FROM pes_manner_state WHERE pid=?", (pid,)).fetchone()
        old, warned = st if st else ("A", 0)
        kind = None
        if ORDER.index(g) > ORDER.index(old):
            kind = "A_TO_B" if g == "B" else "STILL_C"
        elif g == old and q > warned:
            kind = {"A": "DOWN_B", "B": "DOWN_C", "C": "STILL_C"}[g]
        db.execute("INSERT OR REPLACE INTO pes_manner_state (pid, grade, warned_quits) VALUES (?,?,?)", (pid, g, q))
    return kind


@cmd("CMD_GET_MANNER_WARN")
def get_manner_warn(svc, sess, req):
    pid = getattr(sess, "pes_pid", None) or req.get("pid")      # the session's own player first
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
