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
import logging, os, time

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


def _remote(sess):
    """True when this game reaches the server from outside its home network (internet or Tailscale)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(getattr(sess, "peer_ip", "") or "").split("%")[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return ip.is_global or ip in ipaddress.ip_network("100.64.0.0/10")


def _for_viewer(svc, addrs, pid, viewer):
    """A player's NTL addresses as another player should get them. reflexive_addr stays exactly as reported:
    the game picks which side opens the match link by comparing its own reflexive label with the one it got
    for the peer (only the "lower" side opens it), so a rewritten reflexive address can leave both sides
    waiting (MXNV_0013; notes/re/functions/gncx_labels.md, turnlinkup.md). The address a remote friend can
    actually reach goes into host_addr instead (a connection candidate, never a label); if even that fails,
    the TURN relay carries the match."""
    out = dict(addrs)
    if viewer is not None and pid != _pid(viewer) and "reflexive_addr" in out:
        seen = _visible_addr(svc, out["reflexive_addr"], viewer)
        if seen is not out["reflexive_addr"] and seen != out["reflexive_addr"]:
            out["host_addr"] = seen
    return out


def _visible_addr(svc, blob, viewer):
    """A game on the server's own network gets its "public" (reflexive) address from our STUN server
    over the LAN, so it reports its LAN IP (seen live: 192.168.1.100, or 127.0.0.1 on the server PC).
    A friend in another home can't reach that. For such a viewer, put in the address the viewer itself
    uses to reach the server: the host's public IP (or Tailscale IP), which shares the host's router.
    The port is kept; home routers usually keep the source port for UDP."""
    import base64, ipaddress
    from .netsetup import advertised_addr
    if viewer is None or not _remote(viewer):
        return blob
    try:
        raw = blob.encode("utf-8", "surrogateescape") if isinstance(blob, str) else bytes(blob)
        obj = msgpack.unpackb(raw, raw=False)
        sa = base64.b64decode(obj["sa_data"])
        if obj.get("address_family") != 1 or len(sa) < 8 or ipaddress.IPv4Address(sa[4:8]).is_global:
            return blob
        new_ip = ipaddress.IPv4Address(advertised_addr(svc, viewer))
    except (ValueError, TypeError, KeyError, msgpack.ExtraData, msgpack.FormatError, msgpack.StackError):
        return blob
    if not new_ip.is_global and new_ip not in ipaddress.ip_network("100.64.0.0/10"):
        return blob
    obj["sa_data"] = base64.b64encode(sa[:4] + new_ip.packed + sa[8:]).decode()
    out = msgpack.packb(obj, use_bin_type=True)
    log.info("reflexive address %s -> %s for a remote opponent", ipaddress.IPv4Address(sa[4:8]), new_ip)
    return out.decode("utf-8", "surrogateescape") if isinstance(blob, str) else out


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
    return {**out, **_for_viewer(svc, {k: out[k] for k in ("host_addr", "reflexive_addr", "relayed_addr") if k in out},
                                 pid, sess)}


@cmd("CMD_GET_LOBBYLIST")
def get_lobbylist(svc, sess, req):
    if sess is not None:
        sess.pes_lobby_type = str(req.get("lobby_type") or "FREE")     # FREE (Friendly Match Lobby), PESLEAGUE, ...
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
    if pid not in {p for p, _ in st["queue"]}:
        st.setdefault("qsince", {})[pid] = time.time()      # the admin page flags a friend waiting alone
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
    _forget_mismatch(st, _pid(sess))
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


def _member(svc, pid, owner, order, viewer=None):
    p = _player(svc, _username(svc, pid)) or {"pid": pid, "name": f"Player{pid}"}
    # side is what each client uses to decide which team data it owns and sends (HOME/AWAY);
    # without it both stay "unset" and the load fails with MMGC_0002 (no HOME team).
    m = {"pid": pid, "pname": _pname(p), "is_owner": "YES" if owner else "NO", "matching_order": order,
         "side": "HOME" if owner else "AWAY"}
    m.update({k: v for k, v in _state(svc)["ntl"].get(pid, {}).items()
              if k in ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type")})
    m.update(_for_viewer(svc, {k: m[k] for k in ("host_addr", "reflexive_addr") if k in m}, pid, viewer))
    return msgpack.packb(m, use_bin_type=True, unicode_errors="surrogateescape")


def _username(svc, pid):
    r = svc.accounts.db.execute("SELECT username FROM pes_players WHERE pid=?", (pid,)).fetchone()
    return r[0] if r else ""


# ---- game-data check: only pair games that will pass the P2P revision check ----------------------
# After connecting, the two games compare revision and param_check_sum (MultiplayCmdRevisionCheck,
# verdict 0x141001190); a mismatch ends the match with "DATAPACK error" (MXNV533). param_check_sum is
# the CHECK_CHEAT sum over the player database: stat edits, transfers and live-update data change it,
# cosmetic option-file content doesn't (notes/re/functions/menus.md). So keep such players apart.
@cmd("CMD_JOIN_ONLINE_MODE")
def join_online_mode(svc, sess, req):
    if sess is not None:
        sess.pes_versions = {k: str(req.get(k, "")) for k in
                             ("patch_version", "dlc_version", "livedata_version", "extradata_version")}
    return {"result": NOERR}


@cmd("CMD_CHECK_CHEAT")
def check_cheat(svc, sess, req):
    # Only `result` is read; any error blocks online entry (CCWR450/455), so always NOERR.
    if sess is not None:
        sess.pes_sums = {"param": str(req.get("param_check_sum", "")), "entry": str(req.get("entry_check_sum", ""))}
    return {"result": NOERR}


@cmd("CMD_SEND_TAMPER_DATA")
def send_tamper_data(svc, sess, req):
    # The game's own report that player stats changed in memory after login (e.g. a trainer).
    from . import events
    pid = _pid(sess)
    log.warning("TAMPER report pid %s: %s", pid, {k: v for k, v in req.items() if not k.startswith("_")})
    if pid is not None:
        events.feed(svc, f"{events.name_of(svc, pid)}'s game reported edited player data during play", "warn")
    return {"result": NOERR}


def _game_data(sess):
    """What must be equal for two games to play: both CHECK_CHEAT sums (the games compare param_check_sum,
    then entry_check_sum, before kick-off; datacheck.md) and the game versions.
    None for a game that never sent them (it is paired with anyone, as before)."""
    sums, ver = getattr(sess, "pes_sums", None), getattr(sess, "pes_versions", None)
    if not sums or not sums.get("param"):
        return None
    ver = ver or {}
    return (sums["param"], sums.get("entry", ""), ver.get("patch_version", ""), ver.get("dlc_version", ""))


def _same_math(sess):
    """The game uses the mod's own math functions (mod 0.4.0+: "math1" on its id line, gate.py).
    Such a game and one using the C runtime's (Microsoft's on Windows, Wine's under Proton) compute
    different numbers and fall out of sync at kick-off (SYNC_FAILED, RMYN393), so they aren't paired."""
    return "math1" in getattr(sess, "mod_features", ())


def _matching_type(sess):
    return str((getattr(sess, "pes_match_req", None) or {}).get("matching_type") or "OC_QUICK")


def _compatible(sa, sb):
    da, db = _game_data(sa), _game_data(sb)
    return (da is None or db is None or da == db) and _same_math(sa) == _same_math(sb)


def _note_mismatch(svc, a, b, sa=None, sb=None):
    """Tell the host once per waiting pair why two searching players aren't matched."""
    from . import events
    st = _state(svc)
    key = frozenset((a, b))
    if key in st.setdefault("mismatch_told", set()):
        return
    st["mismatch_told"].add(key)
    if sa is not None and sb is not None and _same_math(sa) != _same_math(sb):
        old = a if _same_math(sb) else b
        log.info("MATCH skip %s vs %s: older mod on %s", a, b, old)
        events.feed(svc, f"{events.name_of(svc, a)} and {events.name_of(svc, b)} can't play each other: "
                         f"{events.name_of(svc, old)} has an older mod (or none). Send them the mod again "
                         "(Download the mod for friends): it keeps both games in sync during the match", "warn")
        return
    log.info("MATCH skip %s vs %s: different game data", a, b)
    events.feed(svc, f"{events.name_of(svc, a)} and {events.name_of(svc, b)} can't play each other: their games "
                     "have different player data (patch, live update or edited stats)", "warn")


def _forget_mismatch(st, *pids):
    st["mismatch_told"] = {k for k in st.get("mismatch_told", set()) if not k & set(pids)}


def _next_pair(svc):
    """The longest-waiting pair whose games can play each other, as queue indexes; None if none."""
    q = _state(svc)["queue"]
    for i in range(len(q)):
        for j in range(i + 1, len(q)):
            if _matching_type(q[i][1]) != _matching_type(q[j][1]):
                continue                                  # Quick Match vs Online Divisions: different queues
            from . import pesblock
            if pesblock.blocked(svc, q[i][0], q[j][0]):
                continue                                  # one of them put the other on the Match Block List
            if _compatible(q[i][1], q[j][1]):
                return i, j
            _note_mismatch(svc, q[i][0], q[j][0], q[i][1], q[j][1])
    return None


def _try_pair(svc):
    st = _state(svc)
    while (ij := _next_pair(svc)) is not None:
        i, j = ij
        (a, sa), (b, sb) = st["queue"][i], st["queue"][j]
        st["queue"] = [x for k, x in enumerate(st["queue"]) if k not in (i, j)]
        _forget_mismatch(st, a, b)
        room = st["next_room"]; st["next_room"] += 1
        st["rooms"][room] = {"owner": a, "members": [a, b], "t": time.time(), "mode": _matching_type(sa)}
        log.info("MATCH FOUND room %d: owner %s vs %s", room, a, b)
        for pid in (a, b):
            viewer = sa if pid == a else sb
            members = [_member(svc, a, True, 0, viewer), _member(svc, b, False, 1, viewer)]
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
    try:
        asyncio.get_running_loop().create_task(sess.send(msg))
    except RuntimeError:                              # no running loop (offline tools / unit tests): drop it
        log.info("push %s skipped: no event loop", msgid)


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
        members = [_member(svc, p, p == room["owner"], i, sess) for i, p in enumerate(room["members"])]
        body = {"result": NOERR, "matching_state": "FINISH", "player_num": len(members), "ping": 30,
                "member": members, **extra}
        _send_later(sess, msgid, rqid, kind, seq, body)
        sent.append({"pid": pid, "rqid": rqid})
    log.info("ADMIN push %s kind=%#x seq=%d -> %s extra=%s", msgid, kind, seq, sent, extra)
    return {"room": room_id, "sent": sent}


# ---- after the match: game relay + relay check -----------------------------------------------------
def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _room_of(svc, pid):
    for rid, r in sorted(_state(svc)["rooms"].items(), reverse=True):
        if pid in r["members"]:
            return rid, r
    return None, None


@cmd("CMD_GET_GAMERELAY")
def get_gamerelay(svc, sess, req):
    from .netsetup import advertised_addr
    ip = advertised_addr(svc, sess)
    out = {"result": NOERR, "gamerelay_index": 0, "grid": 1, "ip_addr": ip,
           "port": int(os.environ.get("PESSERVER_RELAY_PORT", "7600")), "game_sycom": ""}
    # Co-op takes its network mode from game_sycom ("" = none: CreateMultiplaySession fails, MXNV_0010);
    # Quick Match and the lobby set P2P themselves. This request comes over HTTP, so the pid is in it.
    from . import pescoop
    if pescoop.in_coop_room(svc, _pid(sess) or _int(req.get("pid"))):
        out["game_sycom"] = "P2P"
    log.info("GAMERELAY pid %s -> %s", _pid(sess), out)
    return out


@cmd("CMD_SET_GAMERELAY_CHECK_RES")
def set_gamerelay_check_res(svc, sess, req):
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
        # A co-op player waits for its own SYNC_PROGRESS notice (TaskSyncProgress expects every member of its
        # group, itself included, to show up in a received notice), so that one also goes back to the sender.
        echo = watch_msgid == "CMD_WATCH_NOTICE" and '"SYNC_PROGRESS"' in str(data)
        # The game's pid_list grows by one copy per match ([1], [1, 1], [1, 1, 1]... seen live);
        # deliver once per player, in order, and never back to the sender.
        targets = []
        for p in pids:
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            if p not in targets and (p != _pid(sess) or echo):
                targets.append(p)
        for p in targets:
            s = svc.online.get(p)
            if s is not None:
                _send_later(s, watch_msgid, 0, 0x60, 1, {"result": NOERR, "data": data})
                delivered.append(p)
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
    from . import pesreplay
    mid = room["match_id"] if room else ""
    # upload YES: the players' games send the match stream, which pesreplay keeps as a replay
    return {"result": NOERR, "match_id": mid, "upload": "YES" if mid and pesreplay.enabled() else "NO"}


# ---- match record and the rating verdict (CMD_WATCH_GAMEADDPOINT) ---------------------------------
# After the final whistle each game's TaskWatchGame (0x141120730) waits for the verdict push; without it
# the game sends CMD_REQ_GAMEADDPOINT after 10 s, gives up after 20 s and skips the rating screen
# (seen live: both games stall). Layout from parser 0x141d7f540, notes/re/functions/results.md.
# A gameend_reason other than NORMAL_END/INJURY shows error OLXH186, so only finished matches get one.
ELO_K, ELO_START = 32, 1000


@cmd("CMD_CHANGE_GAMEPHASE")
def change_gamephase(svc, sess, req):
    pid = _pid(sess)
    rid, room = _room_of(svc, pid)
    if room is not None:
        room.setdefault("phase", {})[pid] = str(req.get("phase", ""))
        if (os.environ.get("PESSERVER_VERDICT_AT_END", "1") != "0" and not room.get("coop")
                and "pushed_at_end" not in room
                and all(room["phase"].get(p) == "END" for p in room["members"])):
            # At the final whistle, to every member (every one-on-one match; PESSERVER_VERDICT_AT_END=0 turns it
            # off). The Friendly Match Lobby's "N Match(es)" / "Wins" counters only count this push (lobby2.md),
            # and with it neither game reports the link as lost when the first player leaves the result screens.
            room["pushed_at_end"] = True
            v = _verdict(svc, rid, room)
            for p in room["members"]:
                if (s := svc.online.get(p)) is not None and v is not None:
                    _send_later(s, "CMD_WATCH_GAMEADDPOINT", 0, 0x60, 1, v)
    return {"result": NOERR}


@cmd("CMD_SET_GAMETEAM")
def set_gameteam(svc, sess, req):
    """Both games send the two team ids at the match menu, before kick-off: keep them with the match
    (Online Divisions shows the opponent's crest in its history)."""
    rid, room = _room_of(svc, _pid(sess))
    if room is not None:
        try:
            room["teams"] = {room["owner"]: int(req.get("home_team_id") or 0),
                             next(p for p in room["members"] if p != room["owner"]): int(req.get("away_team_id") or 0)}
        except (StopIteration, TypeError, ValueError):
            pass
    return {"result": NOERR}


@cmd("CMD_ADD_SCORE")
def add_score(svc, sess, req):
    # Both games report every goal with the same gtimer; a set keeps one copy.
    rid, room = _room_of(svc, _pid(sess))
    side = str(req.get("goal_side", "")).upper()
    if room is not None and side in ("HOME", "AWAY"):
        room.setdefault("goals", set()).add((req.get("gtimer"), side))
    return {"result": NOERR}


@cmd("CMD_REQ_GAMEADDPOINT")
def req_gameaddpoint(svc, sess, req):
    pid = _pid(sess)
    rid, room = _room_of(svc, pid)
    v = _verdict(svc, rid, room) if room is not None else None
    if v is not None:
        _send_later(sess, "CMD_WATCH_GAMEADDPOINT", 0, 0x60, 1, v)
        log.info("VERDICT push -> pid %s (room %s)", pid, rid)
    else:
        log.info("VERDICT none for pid %s (room %s): match did not reach END", pid, rid)
    return {"result": NOERR}


def _elo(ra, rb, score):
    return round(ra + ELO_K * (score - 1 / (1 + 10 ** ((rb - ra) / 400))))


def _forfeit_row(svc, rid, room, quitter, hs, as_, insert_if_missing=False):
    """Make the saved result (pes_matches: Last 20 results, rankings, the admin table) say what the forfeit says."""
    from . import events
    from .pescmds import _db
    db = _db(svc)
    home, away = room["owner"], next(p for p in room["members"] if p != room["owner"])
    try:
        events._st(svc)                      # (creates the table and its newer columns)
        with svc.accounts.lock, db:
            cur = db.execute("UPDATE pes_matches SET home_score=?, away_score=?, reason='FORFEIT', left_pid=? "
                             "WHERE id=(SELECT MAX(id) FROM pes_matches WHERE room=? AND home_pid=? AND away_pid=? "
                             "AND ended >= ?)", (hs, as_, quitter, rid, home, away, room.get("t", 0)))
            if cur.rowcount == 0 and insert_if_missing:
                db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name,"
                           " home_score, away_score, reason, left_pid) VALUES (?,?,?,?,?,?,?,?,?,'FORFEIT',?)",
                           (rid, room.get("t", time.time()), time.time(), home, away, events.name_of(svc, home),
                            events.name_of(svc, away), hs, as_, quitter))
    except Exception:  # noqa: BLE001 - the forfeit itself already counted
        log.exception("saving the forfeit result")


def forfeit(svc, rid, quitter):
    """A match after kick-off that one player left early (pesmanner decides who): the quitter loses and the other
    player wins, 3-0 unless the winner was already ahead. Ratings, Online Divisions and Matchday count it, as if it
    had been played out. No WATCH_GAMEADDPOINT push: the games have already left the match."""
    from . import events
    from .pescmds import _db
    room = _state(svc)["rooms"].get(rid)
    if not room or room.get("verdict") is not None or "forfeit" in room or len(room["members"]) != 2 \
            or quitter not in room["members"] or room.get("coop"):
        return False                          # (co-op: no rating to settle)
    room["forfeit"] = quitter
    room["verdict"] = None                                # (no push later either)
    home, away = room["owner"], next(p for p in room["members"] if p != room["owner"])
    winner = away if quitter == home else home
    wside = "HOME" if winner == home else "AWAY"
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    if score[wside] <= score["AWAY" if wside == "HOME" else "HOME"]:
        goals = {(-1 - n, wside) for n in range(3)}       # 3-0
    db = _db(svc)
    old = {p: (db.execute("SELECT rating, win_streak FROM pes_players WHERE pid=?", (p,)).fetchone()
               or (ELO_START, 0)) for p in (home, away)}
    res = {winner: 1, quitter: 0}
    new = {p: (_elo(old[p][0], old[q][0], res[p]), old[p][1] + 1 if res[p] == 1 else 0)
           for p, q in ((home, away), (away, home))}
    with svc.accounts.lock, db:
        for p, (r, streak) in new.items():
            db.execute("UPDATE pes_players SET rating=?, win_streak=? WHERE pid=?", (r, streak, p))
    scored = {**room, "goals": goals}
    if room.get("mode") == "OC_RANK":
        from . import pesdivisions
        pesdivisions.record(svc, scored)
    elif room.get("mode") == "MATCHDAY":
        from . import pesmatchday
        pesmatchday.record(svc, scored)
    hs = sum(1 for _, g in goals if g == "HOME")
    as_ = sum(1 for _, g in goals if g == "AWAY")
    room["forfeit_score"] = (hs, as_)
    _forfeit_row(svc, rid, room, quitter, hs, as_)
    try:                                       # the games' own SEND_GAME_RESULT may still arrive: fix it up after
        import asyncio
        asyncio.get_running_loop().call_later(45, _forfeit_row, svc, rid, room, quitter, hs, as_, True)
    except RuntimeError:
        pass
    log.info("FORFEIT room %s: pid %s left early, pid %s wins (%s)", rid, quitter, winner,
             {p: f"{old[p][0]}->{new[p][0]}" for p in (home, away)})
    events.feed(svc, f"{events.name_of(svc, quitter)} left the match early: {events.name_of(svc, winner)} wins", "result")
    return True


def _verdict(svc, rid, room):
    """The WATCH_GAMEADDPOINT body for a finished match; ratings are updated once per room."""
    if "verdict" in room:
        return room["verdict"]
    if room.get("quit_pending"):
        return None                          # a player left early: the forfeit decides this match (pesmanner)
    if "END" not in room.get("phase", {}).values() or len(room["members"]) != 2:
        return None
    from . import events
    from .pescmds import _db
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home, away = room["owner"], next(p for p in room["members"] if p != room["owner"])
    side = {home: "HOME", away: "AWAY"}
    db = _db(svc)
    old = {p: (db.execute("SELECT rating, win_streak FROM pes_players WHERE pid=?", (p,)).fetchone()
               or (ELO_START, 0)) for p in (home, away)}
    result = {home: 0.5 + 0.5 * ((score["HOME"] > score["AWAY"]) - (score["HOME"] < score["AWAY"]))}
    result[away] = 1 - result[home]
    new = {p: (_elo(old[p][0], old[q][0], result[p]), old[p][1] + 1 if result[p] == 1 else 0)
           for p, q in ((home, away), (away, home))}
    with svc.accounts.lock, db:
        for p, (r, streak) in new.items():
            db.execute("UPDATE pes_players SET rating=?, win_streak=? WHERE pid=?", (r, streak, p))
    players = []
    for p in (home, away):
        info = _player(svc, _username(svc, p)) or {"name": f"Player{p}"}
        pname = msgpack.packb({"name": info["name"] or "", "xuid_one": "", "is_deleted": "NO",
                               "psn_user_id": msgpack.packb({"account_id": 0, "online_id": "", "is_valid_cache": "NO"})},
                              use_bin_type=True)
        players.append({"pid": p, "pes_pname": pname, "rating_type": "RANKMATCH", "rating": new[p][0],
                        "oldrating": old[p][0], "rating_boost_type": "NONE", "rating_boost_rate": 0,
                        "rating_compe": 0, "side": side[p], "contWin_num": new[p][1],
                        "skill_rank": "SKILL_RANK_NONE", "is_rankup_skill_rank": "NO", "is_first_skillrank": "NO"})
    winner = "WINNER_HOME" if result[home] == 1 else "WINNER_AWAY" if result[away] == 1 else "WINNER_NONE"
    room["verdict"] = {"result": NOERR, "player_count": 2, "player_list": [msgpack.packb(x, use_bin_type=True) for x in players],
                       "winner_side": winner, "gameend_reason": "NORMAL_END", "is_pk": "NO"}
    if room.get("mode") == "OC_RANK":
        from . import pesdivisions
        pesdivisions.record(svc, room)
    elif room.get("mode") == "MATCHDAY":
        from . import pesmatchday
        pesmatchday.record(svc, room)
    log.info("VERDICT room %s %d-%d: %s", rid, score["HOME"], score["AWAY"],
             {p: f"{old[p][0]}->{new[p][0]}" for p in (home, away)})
    events.feed(svc, "Ratings: " + ", ".join(f"{events.name_of(svc, p)} {old[p][0]} → {new[p][0]}" for p in (home, away)),
                "result")
    return room["verdict"]


from . import turn  # noqa: E402,F401  (registers the TURN commands; imports pesmatch lazily)
