"""Match Block List (Settings -> Online Settings -> Match Block List): players you never want to be paired with.

The game (static, kb.py): CMD_SET_MATCHREJECTPID {pid, reject_pid, is_reject YES/NO} adds or removes one player;
CMD_GET_MATCHREJECTLIST {pid} lists them as reject_list [blob{pid, pname}] + reject_list_num (the same entry
layout as the co-op block list, checked on the game's parser for that one). Quick Match, Online Divisions and
Matchday searches (pesmatch) never pair two players when either has blocked the other.
"""
import logging

import msgpack

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.block")


def _setup(db):
    db.execute("CREATE TABLE IF NOT EXISTS pes_match_reject (pid INTEGER, target INTEGER, UNIQUE (pid, target))")


def rejects(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        return [r[0] for r in db.execute("SELECT target FROM pes_match_reject WHERE pid=? ORDER BY rowid", (pid,))]


def blocked(svc, a, b):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        return db.execute("SELECT 1 FROM pes_match_reject WHERE (pid=? AND target=?) OR (pid=? AND target=?)",
                          (a, b, b, a)).fetchone() is not None


def _reply(svc, pid):
    from .pesrooms import room_pname
    lst = [msgpack.packb({"pid": t, "pname": room_pname(svc, t)}, use_bin_type=True) for t in rejects(svc, pid)]
    return {"result": NOERR, "reject_list": lst, "reject_list_num": len(lst)}


@cmd("CMD_GET_MATCHREJECTLIST")
def get_matchrejectlist(svc, sess, req):
    pid = getattr(sess, "pes_pid", None)
    return _reply(svc, pid) if isinstance(pid, int) else {"result": NOERR}


@cmd("CMD_SET_MATCHREJECTPID")
def set_matchrejectpid(svc, sess, req):
    pid, target = getattr(sess, "pes_pid", None), req.get("reject_pid")
    if not isinstance(pid, int) or not isinstance(target, int) or target == pid:
        return {"result": NOERR}
    on = str(req.get("is_reject", "YES")).upper() != "NO"
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if on:
            db.execute("INSERT OR IGNORE INTO pes_match_reject (pid, target) VALUES (?,?)", (pid, target))
        else:
            db.execute("DELETE FROM pes_match_reject WHERE pid=? AND target=?", (pid, target))
    log.info("pid %s %s pid %s", pid, "blocked" if on else "unblocked", target)
    return {"result": NOERR}


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_match_reject WHERE pid=? OR target=?", (pid, pid))
