"""Test-only add-on: Online Co-op, "1 vs 1 co-op" (two solo groups), variant A of notes/re/functions/misc.md §3:
force_alone YES (no group matching), START_COOP_MATCHING from each copy, one WATCH_COOP_MATCHING FINISH push to
both when two are queued, then a 1v1 room so the existing GET_MATCHID / relay check / notice handlers carry the
session. Every co-op command is logged with its request keys. Loaded by run_server.py when COOP=1."""
import logging, time
import msgpack
from pesserver import pescmds, pesmatch
from pesserver.pescmds import NOERR

log = logging.getLogger("pesserver.cooptest")
Q = []          # [(pid, sess, req)]


def pack(d):
    return msgpack.packb(d, use_bin_type=True, unicode_errors="surrogateescape")


def h(name, fn=None):
    def deco(f):
        def wrapper(svc, sess, req):
            log.info("COOP RX %s pid=%s %s", name, pesmatch._pid(sess),
                     {k: v for k, v in req.items() if not k.startswith("_") and k not in ("msgid", "svrtype", "client")})
            r = f(svc, sess, req)
            log.info("COOP TX %s %s", name, {k: v for k, v in r.items() if k != "group_list"} if isinstance(r, dict) else r)
            return r
        pescmds.HANDLERS[name] = wrapper
        return f
    return deco(fn) if fn else deco


h("CMD_GET_IS_JOIN_COOP_ROOM", lambda svc, sess, req: {"result": NOERR, "is_join_coop_room": "NO"})
h("CMD_GET_COOP_MAINMENU_INFO", lambda svc, sess, req: {
    "result": NOERR, "is_coop_banned": "NO", "is_season_end": "NO", "division": "", "pre_season_rank": 0,
    "pre_season_rank_all_clan_num": 0, "division_change_type": "", "is_exist_owner_clan": "NO",
    "is_exist_new_request": "NO"})
h("CMD_GET_COOP_QUICKMATCHOPTION", lambda svc, sess, req: {
    "result": NOERR, "is_vscom": "NO", "force_alone": "YES", "cpu_level_min": "NORMAL", "cpu_level_max": "NORMAL"})
h("CMD_SET_COOP_QUICKMATCHOPTION", lambda svc, sess, req: {"result": NOERR})
h("CMD_GET_COOP_MATCH_DATA", lambda svc, sess, req: {"result": NOERR, "match_data_size": 0, "match_data": ""})
for _n in ("CMD_SET_COOP_MATCH_DATA", "CMD_SET_COOP_PLAYER_GRADE", "CMD_SEND_MATCHING_RESULT", "CMD_SET_GAMETEAM",
           "CMD_CANCEL_COOP_MATCHING"):
    h(_n, lambda svc, sess, req: {"result": NOERR})


def _group(svc, pid, viewer, side, owner, order, gid, use_team_id):
    m = msgpack.unpackb(pesmatch._member(svc, pid, owner, order, viewer), raw=False, unicode_errors="surrogateescape")
    m["side"] = side
    member = pack(m)
    entry = pack({"member": member, "guest_kind": "GUEST_NONE", "is_follow": "NO", "coop_level": 1, "coop_manner": "A"})
    return pack({"member_list": [entry], "member_list_num": 1, "use_team_id": use_team_id, "owner_pid": pid,
                 "group_id": gid})


@h("CMD_START_COOP_MATCHING")
def start_coop_matching(svc, sess, req):
    pid = pesmatch._pid(sess)
    Q[:] = [q for q in Q if q[0] != pid] + [(pid, sess, req)]
    if len(Q) >= 2:
        (a, sa, ra), (b, sb, rb) = Q[0], Q[1]
        del Q[:2]
        st = pesmatch._state(svc)
        rid = st["next_room"]; st["next_room"] += 1
        st["rooms"][rid] = {"owner": a, "members": [a, b], "t": time.time()}
        for viewer in (sa, sb):
            body = {"result": NOERR, "matching_state": "FINISH", "group_list_num": 2, "group_list": [
                _group(svc, a, viewer, "HOME", True, 0, 1, int(ra.get("use_team_id") or 262143)),
                _group(svc, b, viewer, "AWAY", False, 1, 2, int(rb.get("use_team_id") or 262143))]}
            pesmatch._send_later(viewer, "CMD_WATCH_COOP_MATCHING", 0, 0x60, 1, body)
        log.info("COOP push WATCH_COOP_MATCHING FINISH room %d: HOME %s (owner) vs AWAY %s", rid, a, b)
    return {"result": NOERR}


log.info("COOP test handlers installed")


# ---- experiment switches via ctl.json (read on every call) -------------------------------------------
#  "coop_echo": true   -> a CMD_SEND_NOTICE whose pid_list is only the sender is also delivered back to it
#                         (the co-op matching task sends SYNC_PROGRESS to itself and then waits)
import json as _json, os as _os
_CTL = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ctl.json")


def _ctl():
    try:
        return _json.load(open(_CTL))
    except Exception:  # noqa: BLE001
        return {}


_orig_notice = pescmds.HANDLERS["CMD_SEND_NOTICE"]


def _notice(svc, sess, req):
    r = _orig_notice(svc, sess, req)
    me = pesmatch._pid(sess)
    pids = req.get("pid_list") or []
    pids = [pids] if isinstance(pids, int) else pids
    if _ctl().get("coop_echo") and me in [int(p) for p in pids]:
        pesmatch._send_later(sess, "CMD_WATCH_NOTICE", 0, 0x60, 1, {"result": NOERR, "data": req.get("data", "")})
        log.info("COOP echo WATCH_NOTICE back to pid %s: %r", me, req.get("data", ""))
    return r


pescmds.HANDLERS["CMD_SEND_NOTICE"] = _notice


# "gamerelay_p2p": true -> GET_GAMERELAY game_sycom "P2P" (co-op sets its network type from this; "" = 0 = no
# P2P session, MXNV_0010; Quick Match forces type 1 itself) — coop2.md
_orig_gr = pescmds.HANDLERS["CMD_GET_GAMERELAY"]


def _gamerelay(svc, sess, req):
    r = _orig_gr(svc, sess, req)
    if _ctl().get("gamerelay_p2p"):
        r = {**r, "game_sycom": "P2P"}
        log.info("COOP GET_GAMERELAY -> game_sycom P2P")
    return r


pescmds.HANDLERS["CMD_GET_GAMERELAY"] = _gamerelay
