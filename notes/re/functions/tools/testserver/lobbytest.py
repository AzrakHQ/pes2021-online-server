"""Test-only Free-lobby handlers (spec: notes/re/functions/rooms.md), installed at runtime by run_server.py.
Rebuilt 2026-11-06 with the rules proven live/static (notes/re/functions/livetests.md, roomcrash.md, roomlist.md):
own room pushed into the room list right after CREATEJOIN_ROOM; player list sent only as the reply, never pushed
before the owner's ENTRY_GAME; pname.psn_user_id as a nested blob; game_env unpacked from SET_GAMEENV and echoed
as the room-list gameenv (limitTime NOSET hides a room). Answers blocks, room list, room create/join, player list, entry and the owner's start, so a live test can see
how far the game goes and in which order it sends things. Not production code."""
import logging, time
import msgpack
from pesserver import pescmds, pesmatch
from pesserver.pescmds import NOERR, _pname, _player

log = logging.getLogger("pesserver.lobbytest")
ST = {"rooms": {}, "next": 1, "subs": set(), "env": {}}
DEFAULT_ENV = {"cpuLevel": "NORMAL", "gametime": 10, "injury": "NO", "ball_type": 0, "exGame": "YES",
               "pkOnOff": "YES", "substitution": 3, "exSubstitution": "YES", "limitTime": "MIDDLE"}  # NOSET hides the room


def pack(d):
    return msgpack.packb(d, use_bin_type=True, unicode_errors="surrogateescape")


import base64 as _b64
# an unreachable P2P address (TEST-NET 192.0.2.1, port 9) in the same blob layout the game sends
def _addr(ip):
    return msgpack.packb({"address_family": 1, "sa_data": _b64.b64encode(bytes([0, 9, 0, 0, *ip]) + bytes(6)).decode()},
                         use_bin_type=True).decode("utf-8", "surrogateescape")


# Unreachable labels chosen so the kps node-id sort makes exactly one side active (turnlinkup.md): the owner is
# shown as 192.0.2.1:9 (sorts below the guest's own 192.168.x) and the guest as 203.0.113.1:9 (sorts above).
_BAD_ADDR = _addr((192, 0, 2, 1))
_BAD_ADDR_GUEST = _addr((203, 0, 113, 1))


def _ctl_bad_p2p():
    import json, os
    try:
        return bool(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctl.json"))).get("bad_p2p"))
    except Exception:  # noqa: BLE001
        return False


def pid_of(sess):
    return pesmatch._pid(sess)


def push(svc, pid, msgid, body):
    s = svc.online.get(pid)
    if s is None:
        log.info("LOBBY push %s -> pid %s skipped (offline)", msgid, pid); return
    log.info("LOBBY push %s -> pid %s %s", msgid, pid, {k: v for k, v in body.items() if k != "list_data"})
    pesmatch._send_later(s, msgid, 0, 0x60, 1, {"result": NOERR, **body})


def pname(svc, pid, nested=True):
    p = _player(svc, pesmatch._username(svc, pid)) or {"pid": pid, "name": f"Player{pid}"}
    if not nested:
        return _pname(p)
    # room parsers: psn_user_id is itself a blob; never an inline [] or {} (unchecked unpack -> crash)
    return pack({"name": p.get("name") or "", "xuid_one": "",
                 "psn_user_id": pack({"account_id": pid, "online_id": "", "is_valid_cache": "YES"}), "is_deleted": "NO"})


def room_player(svc, room, pid):
    i = room["members"].index(pid)
    e = room["entries"].get(pid)
    m = {"pid": pid, "room_player_id": i + 1, "room_entry_no": i + 1, "game_entry_no": e["entryNum"] if e else 0,
         "pname": pname(svc, pid), "lang": "en", "is_owner": "YES" if pid == room["owner"] else "NO",
         "side": "HOME" if pid == room["owner"] else "AWAY", "is_quality_bad": "NO", "has_guest": "NO"}
    m.update({k: v for k, v in pesmatch._state(svc)["ntl"].get(pid, {}).items()
              if k in ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type")})
    if _ctl_bad_p2p():
        bad = _BAD_ADDR if pid == room["owner"] else _BAD_ADDR_GUEST
        m.update(host_addr=bad, reflexive_addr=bad)
    return pack(m)


def room_entry(svc, room):
    gamer = [pack({"pid": p, "is_room_owner": "YES" if p == room["owner"] else "NO",
                   "side": "HOME" if p == room["owner"] else "AWAY", "enter_no": i + 1, "game_no": room["game_no"]})
             for i, p in enumerate(room["members"])]
    o = room["opts"]
    return pack({"room_id": room["id"], "status": room["state"], "game_phase": "", "name": o.get("name", "Room"),
                 "gameenv": pack(room["env"]), "gamer_num": len(gamer), "gamer": gamer,
                 "is_passwd": "YES" if o.get("password") else "NO", "max_players": 2,
                 "is_invite_limit": o.get("is_invite_limit", "NO"), "dlcontent": "NO", "is_original_player": "YES",
                 "vs_com": o.get("vs_com", "PL_VS_PL"), "owner_pname": pname(svc, room["owner"]), "owner_lang": "en",
                 "room_user_count": len(room["members"]), "is_anyplayers": "YES", "is_select_gk": "YES",
                 "auto_start_num": 0, "room_number": room["id"], "game_rating_limit": "NONE"})


def wl(key, typ, elems):
    return {"list_type": typ, "list_key_name": key, "list_num": len(elems), "list_data": elems}


def player_list_push(svc, room, typ="RELOAD", to=None):
    body = wl("pid", typ, [room_player(svc, room, p) for p in room["members"]])
    for p in (room["members"] if to is None else to):
        push(svc, p, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", body)


def roomlist_push(svc, room, typ):
    body = wl("room_id", typ, [room_entry(svc, room)] if typ != "DEL" else [pack({"room_id": room["id"]})])
    for p in list(ST["subs"]):
        push(svc, p, "CMD_REQWATCHL_ROOMLIST", body)


def later(delay, fn, *a):
    import asyncio
    asyncio.get_event_loop().call_later(delay, fn, *a)


def room_of(pid):
    rs = [r for r in ST["rooms"].values() if pid in r["members"]]
    return max(rs, key=lambda r: r["id"]) if rs else None


def leave_all(svc, pid):
    """A player is in at most one room: drop them from older rooms; delete rooms they owned."""
    for r in list(ST["rooms"].values()):
        if pid not in r["members"]:
            continue
        if r["owner"] == pid:
            ST["rooms"].pop(r["id"], None); roomlist_push(svc, r, "DEL")
            log.info("LOBBY room %d deleted (owner %d moved on)", r["id"], pid)
        else:
            r["members"].remove(pid); r["entries"].pop(pid, None); roomlist_push(svc, r, "UPDATE")


def h(name):
    def deco(fn):
        orig = pescmds.HANDLERS.get(name)
        def wrapper(svc, sess, req):
            log.info("LOBBY RX %s pid=%s %s", name, pid_of(sess), {k: v for k, v in req.items() if not k.startswith("_")})
            return fn(svc, sess, req, orig)
        pescmds.HANDLERS[name] = wrapper
        return fn
    return deco


@h("CMD_GET_BLOCKLIST")
def get_blocklist(svc, sess, req, orig):
    return {"result": NOERR, "count": 1, "bklist": pescmds.blobs([{"name": "Friendly Rooms", "player_num": len(svc.online),
                                                                    "max_player_num": 1000, "block_id": 1}])}


@h("CMD_JOIN_BLOCK")
def join_block(svc, sess, req, orig):
    return {"result": NOERR}


@h("CMD_LEAVE_BLOCK")
def leave_block(svc, sess, req, orig):
    ST["subs"].discard(pid_of(sess)); return {"result": NOERR}


@h("CMD_REQWATCHL_ROOMLIST")
def roomlist(svc, sess, req, orig):
    ST["subs"].add(pid_of(sess))
    return {"result": NOERR, **wl("room_id", "RELOAD", [room_entry(svc, r) for r in ST["rooms"].values()])}


@h("CMD_SET_GAMEENV")
def set_gameenv(svc, sess, req, orig):
    # the 9 settings come packed in the game_env blob (str with surrogateescape bytes), not as top-level keys
    env = {}
    raw = req.get("game_env")
    if raw:
        try:
            b = raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else bytes(raw)
            env = msgpack.unpackb(b, raw=False, unicode_errors="surrogateescape")
        except Exception as e:  # noqa: BLE001
            log.warning("LOBBY game_env unpack failed: %s", e)
    env = {k: env[k] for k in DEFAULT_ENV if k in env}
    log.info("LOBBY game_env %s", env)
    r = room_of(pid_of(sess))
    if r:
        r["env"].update(env)
        later(0.05, roomlist_push, svc, r, "UPDATE")   # the ADD went out before SET_GAMEENV arrived
    else:
        ST["env"][pid_of(sess)] = {**DEFAULT_ENV, **env}
    return {"result": NOERR}


@h("CMD_CREATEJOIN_ROOM")
def createjoin(svc, sess, req, orig):
    pid = pid_of(sess)
    leave_all(svc, pid)
    rid = ST["next"]; ST["next"] += 1
    room = {"id": rid, "owner": pid, "members": [pid], "state": "WAITING", "entries": {}, "game_no": 0,
            "env": ST["env"].pop(pid, dict(DEFAULT_ENV)), "opts": {k: req.get(k) for k in ("name", "password", "vs_com", "is_invite_limit")}}
    ST["rooms"][rid] = room
    ST["subs"].add(pid)
    later(0.05, roomlist_push, svc, room, "ADD")   # the owner looks for its own room in the room list
    # no player-list push here: the owner asks for it, and a push before its ENTRY_GAME ends the game
    return {"result": NOERR, "room_id": rid}


@h("CMD_REQWATCHL_ROOMPLAYERLIST_NTL")
def playerlist(svc, sess, req, orig):
    r = ST["rooms"].get(int(req.get("room_id") or 0)) or room_of(pid_of(sess))
    if not r:
        return {"result": "ERR_ROOMNOTFOUND"}
    return {"result": NOERR, **wl("pid", "RELOAD", [room_player(svc, r, p) for p in r["members"]])}


@h("CMD_JOIN_ROOM_NTL")
def join_room(svc, sess, req, orig):
    pid = pid_of(sess); r = ST["rooms"].get(int(req.get("room_id") or 0))
    if not r:
        return {"result": "ERR_ROOMNOTFOUND"}
    for o in list(ST["rooms"].values()):
        if o is not r and pid in o["members"]:
            leave_all(svc, pid); break
    if len(r["members"]) >= 2 and pid not in r["members"]:
        return {"result": "ERR_ROOMISFULL"}
    if r["opts"].get("password") and req.get("password") != r["opts"]["password"]:
        return {"result": "ERR_PASSWDINCORRECT"}
    if pid not in r["members"]:
        r["members"].append(pid)
    # tell the members already inside (the owner); the joiner asks for the list itself
    later(0.3, player_list_push, svc, r, "UPDATE", [p for p in r["members"] if p != pid])
    later(0.05, roomlist_push, svc, r, "UPDATE")
    owner = {"pid": r["owner"], "pname": pname(svc, r["owner"], nested=False)}
    owner.update({k: v for k, v in pesmatch._state(svc)["ntl"].get(r["owner"], {}).items()
                  if k in ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type")})
    if _ctl_bad_p2p():
        owner.update(host_addr=_BAD_ADDR, reflexive_addr=_BAD_ADDR)
    return {"result": NOERR, "room_owner": pack(owner), "room_stat": r["state"]}


@h("CMD_ENTRY_GAME")
def entry_game(svc, sess, req, orig):
    pid = pid_of(sess); r = room_of(pid)
    if not r:
        return {"result": "ERR_NOTINROOM"}
    if str(req.get("entry", "")).upper() == "TRUE":
        r["entries"][pid] = {"entryNum": len(r["entries"]) + 1, "side": req.get("side")}
    else:
        r["entries"].pop(pid, None)
    def pushes():
        eg = [pack({"pid": p, "entryNum": e["entryNum"], "gameNum": r["game_no"]}) for p, e in r["entries"].items()]
        for p in r["members"]:
            push(svc, p, "CMD_WATCH_ENTRY_GAME", {"entry_count": len(eg), "entrygame": eg})
        player_list_push(svc, r, "UPDATE")
    later(0.3, pushes)
    return {"result": NOERR, "entryflag": req.get("entry", "FALSE"), "gameNum": r["game_no"]}


@h("CMD_SET_GAMEMEMBER")
def set_gamemember(svc, sess, req, orig):
    pid = pid_of(sess); r = room_of(pid)
    if r and r["owner"] == pid and r["state"] != "WAITING":
        return {"result": NOERR}            # the owner repeats SET_GAMEMEMBER right after the start: just ack it
    if not r or r["owner"] != pid:
        return orig(svc, sess, req)
    if len(r["members"]) < 2 or set(r["entries"]) != set(r["members"]):
        log.info("LOBBY start refused: members %s entries %s", r["members"], list(r["entries"]))
        return {"result": NOERR}
    r["state"] = "SETENV"
    st = pesmatch._state(svc)
    st["rooms"][1000 + r["id"]] = {"owner": r["owner"], "members": list(r["members"]), "t": time.time(), "lobby_room": r["id"]}
    for p in r["members"]:
        push(svc, p, "CMD_WATCH_DECIDE_GAMEENV", {"game_env": pack(r["env"])})
        push(svc, p, "CMD_WATCH_ROOMSTATE", {"event": "StateChanged", "state": "SETENV", "owner_pid": r["owner"], "password": ""})
    return {"result": NOERR}


@h("CMD_LEAVE_GAME")
def leave_game(svc, sess, req, orig):
    r = room_of(pid_of(sess))
    if r:
        r.setdefault("left", set()).add(pid_of(sess))
        if r["left"] >= set(r["members"]):
            r.update(state="WAITING", entries={}, left=set()); r["game_no"] += 1
            for p in r["members"]:
                push(svc, p, "CMD_WATCH_ROOMSTATE", {"event": "StateChanged", "state": "WAITING", "owner_pid": r["owner"], "password": ""})
    return {"result": NOERR}


@h("CMD_SET_ROOM_OPTION")
def set_room_option(svc, sess, req, orig):
    return {"result": NOERR}


log.info("LOBBY test handlers installed")


# ---- runtime control (ctl.json next to this file), checked every second ------------------------------
#  {"push": {"id": "r1", "list": "player"|"room", "type": "RELOAD"|"UPDATE", "to": [pid, ...]}}  once per id
#  {"server_env": {"is_revision_check": "NO"}}   merged into every GET_SERVER_ENV reply
import json as _json, os as _os
_CTL = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ctl.json")
_done = set()


def _ctl():
    try:
        return _json.load(open(_CTL))
    except Exception:  # noqa: BLE001
        return {}


def _tick(svc):
    p = _ctl().get("push")
    if p and p.get("id") not in _done:
        _done.add(p.get("id"))
        rooms = list(ST["rooms"].values())
        if rooms:
            r = rooms[0]; to = p.get("to") or r["members"]
            if p.get("list") == "room":
                body = wl("room_id", p.get("type", "UPDATE"), [room_entry(svc, r)])
                for q in to:
                    push(svc, q, "CMD_REQWATCHL_ROOMLIST", body)
            else:
                player_list_push(svc, r, p.get("type", "UPDATE"), to)
            log.info("LOBBY ctl push %s done", p)
    later(1.0, _tick, svc)


_ticking = []
_orig_roomlist = pescmds.HANDLERS["CMD_REQWATCHL_ROOMLIST"]


def _roomlist_with_tick(svc, sess, req):
    if not _ticking:
        _ticking.append(1); later(1.0, _tick, svc)
    return _orig_roomlist(svc, sess, req)


pescmds.HANDLERS["CMD_REQWATCHL_ROOMLIST"] = _roomlist_with_tick
_orig_env = pescmds.HANDLERS.get("CMD_GET_SERVER_ENV")


def _server_env(svc, sess, req):
    r = _orig_env(svc, sess, req) if _orig_env else {"result": NOERR}
    extra = _ctl().get("server_env")
    if extra:
        r = {**r, **extra}
        log.info("LOBBY GET_SERVER_ENV + %s", extra)
    return r


pescmds.HANDLERS["CMD_GET_SERVER_ENV"] = _server_env


# ---- {"err": {"CMD_X": "ERR_Y"}} in ctl.json: reply that error instead (dialog photographs) ----------
#      {"manner_warn": "A_TO_B"}: GET_MANNER_WARN warn_kind
def install_err_overrides():
    def wrap(name, fn):
        def w(svc, sess, req):
            e = _ctl().get("err", {}).get(name)
            if e:
                log.info("LOBBY ctl err %s -> %s", name, e)
                return {"result": e}
            return fn(svc, sess, req)
        return w
    for name, fn in list(pescmds.HANDLERS.items()):
        pescmds.HANDLERS[name] = wrap(name, fn)
    orig = pescmds.HANDLERS.get("CMD_GET_MANNER_WARN")

    def manner(svc, sess, req):
        k = _ctl().get("manner_warn")
        if k:
            log.info("LOBBY ctl manner_warn %s", k)
            return {"result": NOERR, "warn_kind": k}
        return orig(svc, sess, req) if orig else {"result": NOERR}
    pescmds.HANDLERS["CMD_GET_MANNER_WARN"] = wrap("CMD_GET_MANNER_WARN", manner)
