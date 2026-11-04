"""PES 2021 lobby + quick-matchmaking over the native NCL protocol.

Model (from the client's own response parsers, server/pesserver/schemas.json):
  CMD_SEND_IPANDPORT_NTL   client reports its host / reflexive / relayed addresses (+ NAT type)
  CMD_GET_IPANDPORT_NTL    -> {pid, ntl_nat_type, host_addr, reflexive_addr, relayed_addr}
  CMD_GET_LOBBYLIST        -> lobby_list[ blob{lobby_id, name, player_num, player_num_max, gamerelay_index} ]
  CMD_JOIN_LOBBY           -> {gamerelay_web_url}
  CMD_START_MATCHING       enqueue
  CMD_WATCH_MATCHING       LONG-POLL: answered when a match exists ->
                           {player_num, member[ blob{pid, pname, is_owner, matching_order, host_addr, ...} ]}
  CMD_CANCEL_MATCHING      dequeue
Address blobs are passed through exactly as the peer reported them ({address_family, sa_data} where
sa_data is a raw Windows sockaddr payload carried in a msgpack str).
"""
import logging, time

import msgpack

from .pescmds import NOERR, cmd, _pname, _player

log = logging.getLogger("pesserver.match")

LOBBIES = [{"lobby_id": 1, "name": "Local Lobby", "player_num_max": 1000, "gamerelay_index": 0}]
_ADDR_KEYS = ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type", "address_family", "sa_data")


def _state(svc):
    if not hasattr(svc, "pes_mm"):
        svc.pes_mm = {"queue": [], "ntl": {}, "watch": {}, "matched": {}, "rooms": {}, "next_room": 1}
    return svc.pes_mm


def _pid(sess):
    return getattr(sess, "pes_pid", None)


@cmd("CMD_SEND_IPANDPORT_NTL")
def send_ipandport(svc, sess, req):
    st = _state(svc)
    info = {k: v for k, v in req.items() if k in _ADDR_KEYS}
    st["ntl"][_pid(sess)] = info
    log.info("NTL addresses pid=%s %s", _pid(sess), {k: (v.hex() if isinstance(v, bytes) else v) for k, v in info.items()})
    return {"result": NOERR}


@cmd("CMD_GET_IPANDPORT_NTL")
def get_ipandport(svc, sess, req):
    pid = int(req.get("pid") or _pid(sess) or 0)
    info = _state(svc)["ntl"].get(pid, {})
    out = {"result": NOERR, "pid": pid, "ntl_nat_type": int(info.get("ntl_nat_type", 0) or 0)}
    for k in ("host_addr", "reflexive_addr", "relayed_addr"):
        if k in info:
            out[k] = info[k]
    return out


@cmd("CMD_GET_LOBBYLIST")
def get_lobbylist(svc, sess, req):
    n = len(svc.online)
    lst = [msgpack.packb({**lb, "player_num": n}, use_bin_type=True, unicode_errors="surrogateescape") for lb in LOBBIES]
    return {"result": NOERR, "lobby_list_num": len(lst), "lobby_list": lst}


@cmd("CMD_JOIN_LOBBY")
def join_lobby(svc, sess, req):
    sess.pes_lobby = int(req.get("lobby_id", 1) or 1)
    return {"result": NOERR, "gamerelay_web_url": ""}


@cmd("CMD_LEAVE_LOBBY")
def leave_lobby(svc, sess, req):
    sess.pes_lobby = None
    return {"result": NOERR}


@cmd("CMD_START_MATCHING")
def start_matching(svc, sess, req):
    st = _state(svc)
    pid = _pid(sess)
    st["queue"] = [(p, s) for p, s in st["queue"] if p != pid] + [(pid, sess)]
    sess.pes_match_req = {k: v for k, v in req.items() if not k.startswith("_")}
    log.info("MATCH queue += pid %s  (queue=%s)", pid, [p for p, _ in st["queue"]])
    _try_pair(svc)
    return {"result": NOERR}


@cmd("CMD_CANCEL_MATCHING")
def cancel_matching(svc, sess, req):
    st = _state(svc)
    st["queue"] = [(p, s) for p, s in st["queue"] if p != _pid(sess)]
    st["watch"].pop(_pid(sess), None)
    log.info("MATCH cancel pid %s", _pid(sess))
    return {"result": NOERR}


@cmd("CMD_WATCH_MATCHING")
def watch_matching(svc, sess, req):
    st = _state(svc)
    pid = _pid(sess)
    if pid in st["matched"]:
        return st["matched"].pop(pid)
    st["watch"][pid] = (sess, int(req.get("rqid", 0) or 0), req.get("_kind", 0x60), req.get("_seq", 1))
    log.info("MATCH watch parked for pid %s", pid)
    return {"_defer": True}


def _member(svc, pid, owner, order):
    p = _player(svc, _username(svc, pid)) or {"pid": pid, "name": f"Player{pid}"}
    # side is what each client uses to decide which team data it owns and sends (HOME/AWAY);
    # without it both stay "unset" and the load fails with MMGC_0002 (no HOME team).
    m = {"pid": pid, "pname": _pname(p), "is_owner": "YES" if owner else "NO", "matching_order": order,
         "side": "HOME" if owner else "AWAY"}
    m.update({k: v for k, v in _state(svc)["ntl"].get(pid, {}).items()
              if k in ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type")})
    return msgpack.packb(m, use_bin_type=True, unicode_errors="surrogateescape")


def _username(svc, pid):
    r = svc.accounts.db.execute("SELECT username FROM pes_players WHERE pid=?", (pid,)).fetchone()
    return r[0] if r else ""


def _try_pair(svc):
    st = _state(svc)
    while len(st["queue"]) >= 2:
        (a, sa), (b, sb) = st["queue"][0], st["queue"][1]
        st["queue"] = st["queue"][2:]
        room = st["next_room"]; st["next_room"] += 1
        st["rooms"][room] = {"owner": a, "members": [a, b], "t": time.time()}
        members = [_member(svc, a, True, 0), _member(svc, b, False, 1)]
        log.info("MATCH FOUND room %d: owner %s vs %s", room, a, b)
        for pid in (a, b):
            # matching_state is what OnReceive (0x141100d00) switches on: FINISH -> status 2 -> the
            # quick-match task (0x14111bea0) proceeds to gamerelay + P2P. Without it: 300 s timeout (EYRH763).
            reply = {"result": NOERR, "matching_state": "FINISH", "player_num": 2, "ping": 30,
                     "is_special_match": "NO", "is_first_rookie_match": "NO", "is_same_group_match": "NO",
                     "member": members}
            # WATCH_* messages are server pushes: the client sits on "Searching for an opponent" and
            # never polls. Push to the parked request if any, else unsolicited (rqid 0).
            parked = st["watch"].pop(pid, None)
            if parked:
                sess, rqid, kind, seq = parked
            else:
                sess = next((s for p, s in ((a, sa), (b, sb)) if p == pid), None)
                rqid, kind, seq = 0, 0x60, 1
            if sess is not None:
                _send_later(sess, "CMD_WATCH_MATCHING", rqid, kind, seq, reply)
                log.info("MATCH push CMD_WATCH_MATCHING -> pid %s", pid)


def _send_later(sess, msgid, rqid, kind, seq, body):
    import asyncio
    from .protocol import Message
    if getattr(sess, "send", None) is None:          # offline replay / disconnected session
        log.info("push %s skipped: session has no live connection", msgid)
        return
    msg = Message(msgid, rqid, {**body, "_kind": kind, "_seq": seq})
    asyncio.get_event_loop().create_task(sess.send(msg))


# ---- dev hook: re-push the last match notification in a chosen variant (no restart needed) -------
def admin_push(svc, params):
    """params: kind (int, default 0x60), seq (int, default 1), rqid ('start' = each player's
    START_MATCHING rqid, or an int), msgid (default CMD_WATCH_MATCHING), extra (JSON merged into body),
    pids (comma list, default both of the last room)."""
    import json
    st = _state(svc)
    if not st["rooms"]:
        return {"error": "no room yet"}
    room_id = max(st["rooms"]); room = st["rooms"][room_id]
    members = [_member(svc, p, p == room["owner"], i) for i, p in enumerate(room["members"])]
    kind = int(params.get("kind", "0x60"), 0); seq = int(params.get("seq", "1"), 0)
    msgid = params.get("msgid", "CMD_WATCH_MATCHING")
    extra = json.loads(params.get("extra", "{}"))
    pids = [int(x) for x in params.get("pids", ",".join(map(str, room["members"]))).split(",")]
    sent = []
    for pid in pids:
        sess = svc.online.get(pid)
        if sess is None:
            continue
        rq = params.get("rqid", "0")
        rqid = int(getattr(sess, "pes_match_req", {}).get("rqid", 0)) if rq == "start" else int(rq, 0)
        body = {"result": NOERR, "matching_state": "FINISH", "player_num": len(members), "ping": 30,
                "member": members, **extra}
        _send_later(sess, msgid, rqid, kind, seq, body)
        sent.append({"pid": pid, "rqid": rqid})
    log.info("ADMIN push %s kind=%#x seq=%d -> %s extra=%s", msgid, kind, seq, sent, extra)
    return {"room": room_id, "sent": sent}


# ---- after the match: game relay + relay check -----------------------------------------------------
def _room_of(svc, pid):
    for rid, r in sorted(_state(svc)["rooms"].items(), reverse=True):
        if pid in r["members"]:
            return rid, r
    return None, None


@cmd("CMD_GET_GAMERELAY")
def get_gamerelay(svc, sess, req):
    import os
    from .netsetup import advertised_addr
    ip = advertised_addr(svc, sess)
    out = {"result": NOERR, "gamerelay_index": 0, "grid": 1, "ip_addr": ip,
           "port": int(os.environ.get("PESSERVER_RELAY_PORT", "7600")), "game_sycom": ""}
    log.info("GAMERELAY pid %s -> %s", _pid(sess), out)
    return out


@cmd("CMD_SET_GAMERELAY_CHECK_RES")
def set_gamerelay_check_res(svc, sess, req):
    import os
    pid = _pid(sess); rid, room = _room_of(svc, pid)
    log.info("GAMERELAY_CHECK_RES pid %s room %s req=%s", pid, rid, {k: v for k, v in req.items() if not k.startswith("_")})
    if room is not None:
        room.setdefault("relay_checked", set()).add(pid)
        if room["relay_checked"] >= set(room["members"]):
            code = os.environ.get("PESSERVER_RELAY_CHECK_CODE", "NOERR")
            for p in room["members"]:
                s = svc.online.get(p)
                if s is not None:
                    _send_later(s, "CMD_WATCH_GAMERELAY_CHECK_RES", 0, 0x60, 1, {"result": NOERR, "error_code": code})
            log.info("GAMERELAY check complete room %s -> push error_code=%r", rid, code)
    return {"result": NOERR}




# ---- peer signalling: CMD_SEND_*NOTICE {pid_list, data} -> push CMD_WATCH_*NOTICE {data} to each pid --
def _relay_notice(watch_msgid):
    def handler(svc, sess, req):
        pids = req.get("pid_list") or []
        if isinstance(pids, int):
            pids = [pids]
        data = req.get("data", "")
        delivered = []
        for p in pids:
            s = svc.online.get(int(p))
            if s is not None:
                _send_later(s, watch_msgid, 0, 0x60, 1, {"result": NOERR, "data": data})
                delivered.append(int(p))
        log.info("NOTICE %s from pid %s -> %s data=%r (delivered %s)", watch_msgid, _pid(sess), pids, data, delivered)
        return {"result": NOERR}
    return handler


cmd("CMD_SEND_SYNC_NOTICE")(_relay_notice("CMD_WATCH_SYNC_NOTICE"))
cmd("CMD_SEND_NOTICE")(_relay_notice("CMD_WATCH_NOTICE"))
cmd("CMD_SEND_SYSTEM_NOTICE")(_relay_notice("CMD_WATCH_SYSTEM_NOTICE"))


@cmd("CMD_GET_MATCHID")
def get_matchid(svc, sess, req):
    rid, room = _room_of(svc, _pid(sess))
    if room is not None and "match_id" not in room:
        room["match_id"] = f"{int(room['t'])}{rid:06d}"
    return {"result": NOERR, "match_id": room["match_id"] if room else "", "upload": "NO"}
