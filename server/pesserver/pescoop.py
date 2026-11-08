"""Online Co-op, 1 vs 1: each player alone on a side (the rest of each team is CPU).

The game's own co-op never connects in 1.01.00: it doesn't hand the opponent to its P2P layer. Mod 0.5.0 adds
that step (server/mod, "Online Co-op") and says "coop1" on its id line, so only such players are paired here.
The P2P layer takes exactly one peer, so bigger co-op matches (2v2, 3v3) can't run; every player is matched
alone (force_alone), which skips the game's group matching.

Flow (notes/re/functions/misc.md §3, coop2.md, livetests.md):
  menu:  GET_COOP_QUICKMATCHOPTION -> force_alone YES
  each:  SEND_NOTICE SYNC_PROGRESS to itself (pesmatch echoes it back) -> START_COOP_MATCHING
  two queued: WATCH_COOP_MATCHING FINISH to both (two groups of one: owner HOME, the other AWAY) and a room,
  so GET_GAMERELAY ("P2P" for co-op rooms), GET_MATCHID, the notice relays and the result handling of the
  1v1 code carry the session from there.
"""
import logging, random, time

import msgpack

from .pescmds import NOERR, cmd
from . import pescmds, pesmatch
from .pesmatch import _compatible, _member, _pid, _state

log = logging.getLogger("pesserver.coop")
NO_TEAM = 262143          # use_team_id when the player brought no team of its own


def _teams():
    """Two different real teams for HOME and AWAY. Co-op has no team-select screen: each side plays the
    team in its group's use_team_id, and with "no team" (262143) on both sides the match load crashes both
    games (null team, seen live). Players who did bring a team keep it."""
    ids = [t for t, _ in pescmds.TEAM_LIST]
    return random.sample(ids, 2)


def _fixed(sess):
    """The mod puts the co-op connection fix in (mod 0.5.0+: "coop1" on its id line, gate.py)."""
    return "coop1" in getattr(sess, "mod_features", ())


@cmd("CMD_GET_COOP_QUICKMATCHOPTION")
def get_coop_quickmatchoption(svc, sess, req):
    # force_alone: no group matching (two humans on one side would need 2 P2P peers; see the module doc)
    return {"result": NOERR, "is_vscom": "NO", "force_alone": "YES", "cpu_level_min": "NORMAL",
            "cpu_level_max": "NORMAL"}


@cmd("CMD_GET_COOP_MAINMENU_INFO")
def get_coop_mainmenu_info(svc, sess, req):
    return {"result": NOERR, "is_coop_banned": "NO", "is_season_end": "NO", "division": "", "pre_season_rank": 0,
            "pre_season_rank_all_clan_num": 0, "division_change_type": "", "is_exist_owner_clan": "NO",
            "is_exist_new_request": "NO"}


@cmd("CMD_GET_IS_JOIN_COOP_ROOM")
def get_is_join_coop_room(svc, sess, req):
    return {"result": NOERR, "is_join_coop_room": "NO"}


# ---- profile and block list -------------------------------------------------------------------------
def _setup(db):
    db.execute("CREATE TABLE IF NOT EXISTS pes_coop_reject (pid INTEGER, target INTEGER, UNIQUE (pid, target))")
    db.execute("""CREATE TABLE IF NOT EXISTS pes_coop_level (pid INTEGER PRIMARY KEY, points INTEGER NOT NULL DEFAULT 0,
                  matches INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0)""")


# ---- co-op level: points per finished co-op match, a level every LEVEL_POINTS -------------------------------
LEVEL_POINTS = 100
MAX_LEVEL = 99
AWARD = {"WIN": 30, "DRAW": 20, "LOSE": 10}


def level_of(points):
    return min(MAX_LEVEL, 1 + int(points) // LEVEL_POINTS)


def level(svc, pid):
    """(level, points, matches) of a player's co-op card."""
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT points, matches FROM pes_coop_level WHERE pid=?", (pid,)).fetchone()
    pts, n = r or (0, 0)
    return level_of(pts), pts, n


def award(svc, room):
    """At the final whistle of a co-op match (both games at END): points for both players, once per room.
    The before/after values are kept for GET_COOP_LEVEL_RESULT, which comes after the room is gone."""
    if room.get("coop_awarded") or len(room.get("members", [])) != 2:
        return
    room["coop_awarded"] = True
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home = room["owner"]
    away = next(p for p in room["members"] if p != home)
    mid = room.get("match_id") or ""
    db = pescmds._db(svc)
    done = {}
    with svc.accounts.lock, db:
        _setup(db)
        for me, side in ((home, "HOME"), (away, "AWAY")):
            mine, theirs = (score["HOME"], score["AWAY"]) if side == "HOME" else (score["AWAY"], score["HOME"])
            res = "WIN" if mine > theirs else "LOSE" if mine < theirs else "DRAW"
            before = (db.execute("SELECT points FROM pes_coop_level WHERE pid=?", (me,)).fetchone() or (0,))[0]
            db.execute("INSERT INTO pes_coop_level (pid, points, matches, wins) VALUES (?,?,1,?) ON CONFLICT (pid) DO "
                       "UPDATE SET points=points+?, matches=matches+1, wins=wins+?",
                       (me, AWARD[res], int(res == "WIN"), AWARD[res], int(res == "WIN")))
            done[me] = {"match_id": mid, "point": AWARD[res], "before_point": before, "after_point": before + AWARD[res],
                        "before_level": level_of(before), "after_level": level_of(before + AWARD[res])}
    st = _state(svc).setdefault("coop_last", {})
    for pid in done:
        st[pid] = {"members": [home, away], "rows": done}
    log.info("COOP level: %s", {p: f"{d['before_point']}->{d['after_point']} (L{d['after_level']})" for p, d in done.items()})


@cmd("CMD_GET_COOP_LEVEL_RESULT")
def get_coop_level_result(svc, sess, req):
    """The co-op level screen after a co-op match: both players' points and levels, before and after (layout from
    the parser 0x141ba8ec0; status values unknown, notes/re/functions/coop.md)."""
    pid = _pid(sess) or req.get("pid")
    last = _state(svc).get("coop_last", {}).get(pid)
    out = []
    for p in (last or {}).get("members", []):
        d = last["rows"][p]
        up = d["after_level"] > d["before_level"]
        out.append(msgpack.packb({"status": "LEVEL_UP" if up else "NONE",
                                  "user_id": msgpack.packb({"pid": p, "pname": _pname_of(svc, p)}, use_bin_type=True),
                                  "is_clan_owner": "NO", "is_follow": "NO", "evalution": 0, "reference": 0,
                                  "point": d["point"], "before_point": d["before_point"], "after_point": d["after_point"],
                                  "before_level": d["before_level"], "after_level": d["after_level"]}, use_bin_type=True))
    return {"result": NOERR, "coop_level_user_list": out}


def _pname_of(svc, pid):
    from .pesrooms import room_pname
    return room_pname(svc, pid)


def rejects(svc, pid):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        return [r[0] for r in db.execute("SELECT target FROM pes_coop_reject WHERE pid=? ORDER BY rowid", (pid,))]


def blocked(svc, a, b):
    """Either player put the other on their co-op block list."""
    return b in rejects(svc, a) or a in rejects(svc, b)


def forget(svc, pid):
    """A deleted player: drop their block list, their place on other players' lists and their co-op level."""
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_coop_reject WHERE pid=? OR target=?", (pid, pid))
        db.execute("DELETE FROM pes_coop_level WHERE pid=?", (pid,))
    _state(svc).get("coop_last", {}).pop(pid, None)


def _reject_reply(svc, pid):
    lst = [msgpack.packb({"pid": t, "pname": _pname_of(svc, t)}, use_bin_type=True)
           for t in rejects(svc, pid)]
    return {"result": NOERR, "reject_list": lst, "reject_list_num": len(lst)}


@cmd("CMD_GET_COOP_REJECT_LIST")
def get_coop_reject_list(svc, sess, req):
    return _reject_reply(svc, _pid(sess))


@cmd("CMD_SET_COOP_REJECT")
def set_coop_reject(svc, sess, req):
    """{set_reject_pid_list, unset_reject_pid_list}: add / remove players; the reply is the new list."""
    pid = _pid(sess)
    add = [t for t in req.get("set_reject_pid_list") or [] if isinstance(t, int) and t != pid]
    drop = [t for t in req.get("unset_reject_pid_list") or [] if isinstance(t, int)]
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for t in add:
            n = db.execute("SELECT COUNT(*) FROM pes_coop_reject WHERE pid=?", (pid,)).fetchone()[0]
            if n >= 400 or not db.execute("SELECT 1 FROM pes_players WHERE pid=?", (t,)).fetchone():
                continue
            db.execute("INSERT OR IGNORE INTO pes_coop_reject (pid, target) VALUES (?,?)", (pid, t))
        for t in drop:
            db.execute("DELETE FROM pes_coop_reject WHERE pid=? AND target=?", (pid, t))
    if add or drop:
        log.info("COOP block list pid %s: +%s -%s", pid, add, drop)
    return _reject_reply(svc, pid)


@cmd("CMD_GET_COOP_PROFILE")
def get_coop_profile(svc, sess, req):
    """A player's co-op card: the courtesy rating (pesmanner), the co-op level and points, co-op matches played."""
    from . import pesmanner
    pid = req.get("pid") if isinstance(req.get("pid"), int) else _pid(sess)
    grade = pesmanner.rating(svc, pid)["grade"] if isinstance(pid, int) else "A"
    lv, pts, n = level(svc, pid) if isinstance(pid, int) else (1, 0, 0)
    data = {"is_follow": "NO", "coop_level": lv, "coop_level_point": pts, "follower": 0, "coop_manner": grade,
            "online_match": n, "vscom_match": 0, "match_data_size": 0, "match_data": ""}
    # two top-level blobs, side by side (checked on the game's own parser with tools/parsebench.py)
    info = {"pid": pid, "pname": _pname_of(svc, pid)}
    return {"result": NOERR, "user_info": msgpack.packb(info, use_bin_type=True),
            "coop_profile_data": msgpack.packb(data, use_bin_type=True)}


def _manner(svc, pid):
    from . import pesmanner
    try:
        return pesmanner.rating(svc, pid)["grade"]
    except Exception:  # noqa: BLE001
        return "A"


@cmd("CMD_GET_COOP_MATCH_DATA")
def get_coop_match_data(svc, sess, req):
    # 480 bytes of score tables the game keeps between co-op matches; empty is accepted (it zeroes its copy)
    return {"result": NOERR, "match_data_size": 0, "match_data": ""}


def _queue(svc):
    return _state(svc).setdefault("coop_queue", [])


@cmd("CMD_START_COOP_MATCHING")
def start_coop_matching(svc, sess, req):
    pid = _pid(sess)
    q = _queue(svc)
    if pid not in {x[0] for x in q}:
        _state(svc).setdefault("qsince", {})[pid] = time.time()        # the admin page's "searching for N min"
    q[:] = [x for x in q if x[0] != pid] + [(pid, sess, int(req.get("use_team_id") or NO_TEAM))]
    log.info("COOP queue += pid %s%s (queue=%s)", pid, "" if _fixed(sess) else " (mod without the co-op fix)",
             [x[0] for x in q])
    if not _fixed(sess):
        _tell_old_mod(svc, pid)
    _try_pair(svc)
    return {"result": NOERR}


@cmd("CMD_CANCEL_COOP_MATCHING")
def cancel_coop_matching(svc, sess, req):
    q = _queue(svc)
    q[:] = [x for x in q if x[0] != _pid(sess)]
    log.info("COOP cancel pid %s", _pid(sess))
    return {"result": NOERR}


def _tell_old_mod(svc, pid):
    from . import events
    told = _state(svc).setdefault("coop_old_mod_told", set())
    if pid not in told:
        told.add(pid)
        events.feed(svc, f"{events.name_of(svc, pid)} is searching for an Online Co-op match, but their mod can't "
                         "connect co-op matches. Send them the mod again (Download the mod for friends)", "warn")


def _try_pair(svc):
    q = _queue(svc)
    ready = [x for x in q if _fixed(x[1])]
    for i in range(len(ready)):
        for j in range(i + 1, len(ready)):
            (a, sa, ta), (b, sb, tb) = ready[i], ready[j]
            if blocked(svc, a, b):
                continue
            if not _compatible(sa, sb):
                pesmatch._note_mismatch(svc, a, b, sa, sb)
                continue
            q[:] = [x for x in q if x[0] not in (a, b)]
            _start(svc, (a, sa, ta), (b, sb, tb))
            return


def _group(svc, pid, owner, order, gid, team, viewer):
    """One group of one human. Blob nesting as the parser 0x141dc9a70 reads it: group_list[] -> blob{
    member_list[] -> blob{member: blob{...}}}. member is the 1v1 member blob (pid, pname, is_owner, side,
    addresses rewritten for the viewer); exactly one is_owner YES in the push, on the HOME owner."""
    entry = msgpack.packb({"member": _member(svc, pid, owner, order, viewer), "guest_kind": "GUEST_NONE",
                           "is_follow": "NO", "coop_level": level(svc, pid)[0], "coop_manner": _manner(svc, pid)},
                          use_bin_type=True)
    return msgpack.packb({"member_list": [entry], "member_list_num": 1, "use_team_id": team, "owner_pid": pid,
                          "group_id": gid}, use_bin_type=True)


def _start(svc, home, away):
    (a, sa, ta), (b, sb, tb) = home, away
    pick = _teams()
    ta = ta if ta != NO_TEAM else pick[0]
    tb = tb if tb != NO_TEAM else pick[1]
    st = _state(svc)
    room = st["next_room"]; st["next_room"] += 1
    st["rooms"][room] = {"owner": a, "members": [a, b], "t": time.time(), "coop": True}
    pesmatch._forget_mismatch(st, a, b)
    for viewer in (sa, sb):
        body = {"result": NOERR, "matching_state": "FINISH", "group_list_num": 2,
                "group_list": [_group(svc, a, True, 0, 1, ta, viewer), _group(svc, b, False, 1, 2, tb, viewer)]}
        pesmatch._send_later(viewer, "CMD_WATCH_COOP_MATCHING", 0, 0x60, 1, body)
    log.info("COOP FOUND room %d: %s (HOME, owner, team %s) vs %s (AWAY, team %s)", room, a, ta, b, tb)


def in_coop_room(svc, pid):
    _, room = pesmatch._room_of(svc, pid)
    return bool(room and room.get("coop"))
