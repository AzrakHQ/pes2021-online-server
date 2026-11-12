"""Friendly Match Lobby (Kick Off -> Friendly Match Lobby): match rooms, so friends pick who they play.

Flow and rules from notes/re/functions/rooms.md, roomcrash.md, roomlist.md and the live tests (livetests.md):

- one block ("Friendly Rooms"); REQWATCHL_ROOMLIST subscribes and its reply is the block's rooms as RELOAD;
- CREATEJOIN_ROOM, then SET_GAMEENV (the 9 settings packed in a `game_env` blob). The owner's room is pushed
  into the room list (ADD) at once: after creating a room the game looks for its own pid there. The room's
  `gameenv` echoes the owner's blob; `limitTime` NOSET would hide the room from every list;
- REQWATCHL_ROOMPLAYERLIST_NTL is answered with the list. **A RELOAD is only ever sent as a reply to a new
  watch request**: the client's watch-list merger throws (and the game exits) on a RELOAD into a non-empty
  cache. Later changes are pushed as UPDATE / DEL, and only to members who already sent ENTRY_GAME (a push to
  the owner before its first ENTRY_GAME ends the game the same way);
- `pname.psn_user_id` and `owner_pname.psn_user_id` are nested blobs (an inline list or map crashes it);
- "Start Match" = ENTRY_GAME {entry TRUE}; when everyone has entered, the owner sends SET_GAMEMEMBER and the
  server pushes WATCH_DECIDE_GAMEENV + WATCH_ROOMSTATE StateChanged SETENV. From there the Quick Match session
  code takes over (a pesmatch room record is registered for GET_MATCHID, the relay check, notices, results);
- after the match both send LEAVE_GAME; when all have, StateChanged WAITING brings them back to the room.
"""
import logging

import msgpack

from .pescmds import NOERR, cmd, _player, _pname
from . import pesmatch

log = logging.getLogger("pesserver.rooms")

BLOCK = {"block_id": 1, "name": "Friendly Rooms", "max_player_num": 1000}
DEFAULT_ENV = {"cpuLevel": "NORMAL", "gametime": 10, "injury": "NO", "ball_type": 0, "exGame": "YES",
               "pkOnOff": "YES", "substitution": 3, "exSubstitution": "YES", "limitTime": "MIDDLE"}
ADDR_KEYS = ("host_addr", "reflexive_addr", "relayed_addr", "ntl_nat_type")
PUSH_DELAY = 0.05            # pushes go out just after the reply they belong to
ENTRY_PUSH_DELAY = 0.3


def pack(d):
    return msgpack.packb(d, use_bin_type=True, unicode_errors="surrogateescape")


def _st(svc):
    if not hasattr(svc, "pes_rooms"):
        svc.pes_rooms = {"rooms": {}, "next": 1, "subs": set()}
    return svc.pes_rooms


def _pid(sess):
    return pesmatch._pid(sess)


def room_of(svc, pid):
    return next((r for r in _st(svc)["rooms"].values() if pid in r["members"]), None)


# ---- pushes -----------------------------------------------------------------------------------------
def _later(fn, *a, delay=PUSH_DELAY):
    import asyncio
    try:
        asyncio.get_running_loop().call_later(delay, fn, *a)
    except RuntimeError:          # no loop (unit tests calling handlers directly): run now
        fn(*a)


def _push(svc, pid, msgid, body):
    s = svc.online.get(pid)
    if s is not None:
        pesmatch._send_later(s, msgid, 0, 0x60, 1, {"result": NOERR, **body})


def _wl(key, typ, elems):
    return {"list_type": typ, "list_key_name": key, "list_num": len(elems), "list_data": elems}


# ---- element builders -------------------------------------------------------------------------------
def room_pname(svc, pid):
    """pname for room lists: psn_user_id is itself a blob (never an inline [] / {})."""
    p = _player(svc, pesmatch._username(svc, pid)) or {"pid": pid, "name": f"Player{pid}"}
    return pack({"name": p.get("name") or "", "xuid_one": "",
                 "psn_user_id": pack({"account_id": pid, "online_id": "", "is_valid_cache": "YES"}),
                 "is_deleted": "NO"})


def _addrs(svc, pid, viewer_sess):
    out = {k: v for k, v in pesmatch._state(svc)["ntl"].get(pid, {}).items() if k in ADDR_KEYS}
    return pesmatch._for_viewer(svc, out, pid, viewer_sess)


def room_player(svc, room, pid, viewer_sess):
    i = room["members"].index(pid)
    e = room["entries"].get(pid)
    owner = pid == room["owner"]
    m = {"pid": pid, "room_player_id": i + 1, "room_entry_no": i + 1, "game_entry_no": e["entryNum"] if e else 0,
         "pname": room_pname(svc, pid), "lang": "en", "is_owner": "YES" if owner else "NO",
         "side": "HOME" if owner else "AWAY", "is_quality_bad": "NO", "has_guest": "NO"}
    m.update(_addrs(svc, pid, viewer_sess))
    return pack(m)


def room_entry(svc, room):
    gamer = [pack({"pid": p, "is_room_owner": "YES" if p == room["owner"] else "NO",
                   "side": "HOME" if p == room["owner"] else "AWAY", "enter_no": i + 1, "game_no": room["game_no"]})
             for i, p in enumerate(room["members"])]
    o = room["opts"]
    return pack({"room_id": room["id"], "status": room["state"], "game_phase": "", "name": o["name"],
                 "gameenv": pack(room["env"]), "gamer_num": len(gamer), "gamer": gamer,
                 "is_passwd": "YES" if o["password"] else "NO", "max_players": room["max_players"],
                 "is_invite_limit": o["is_invite_limit"], "dlcontent": "NO",
                 "is_original_player": o["is_original_player"], "vs_com": o["vs_com"],
                 "owner_pname": room_pname(svc, room["owner"]), "owner_lang": "en",
                 "room_user_count": len(room["members"]), "is_anyplayers": o["is_anyplayers"],
                 "is_select_gk": o["is_select_gk"], "auto_start_num": o["auto_start_num"],
                 "room_number": room["id"], "game_rating_limit": o["game_rating_limit"]})


def roomlist_push(svc, room, typ):
    body = _wl("room_id", typ, [room_entry(svc, room)] if typ != "DEL" else [pack({"room_id": room["id"]})])
    for p in list(_st(svc)["subs"]):
        _push(svc, p, "CMD_REQWATCHL_ROOMLIST", body)


def playerlist_push(svc, room, typ="UPDATE", only=None, gone=None):
    """UPDATE (or DEL for `gone`) to members whose list is safe to update: they sent ENTRY_GAME since joining."""
    for p in list(room["members"]):
        if p not in room["listening"] or (only is not None and p not in only):
            continue
        s = svc.online.get(p)
        if s is None:
            continue
        if typ == "DEL":
            body = _wl("pid", "DEL", [pack({"pid": gone})])
        else:
            body = _wl("pid", typ, [room_player(svc, room, m, s) for m in room["members"]])
        pesmatch._send_later(s, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", 0, 0x60, 1, {"result": NOERR, **body})


def _yes(v, default="NO"):
    v = str(v or default).upper()
    return "YES" if v in ("YES", "TRUE", "1") else "NO"


def _unpack_env(raw):
    if not raw:
        return {}
    try:
        b = raw.encode("utf-8", "surrogateescape") if isinstance(raw, str) else bytes(raw)
        env = msgpack.unpackb(b, raw=False, unicode_errors="surrogateescape")
    except (ValueError, TypeError, msgpack.ExtraData, msgpack.FormatError, msgpack.StackError):
        return {}
    return {k: env[k] for k in DEFAULT_ENV if isinstance(env, dict) and k in env}


# ---- block and room list ----------------------------------------------------------------------------
@cmd("CMD_GET_BLOCKLIST")
def get_blocklist(svc, sess, req):
    # A bare NOERR here shows "Unable to send or receive data."; one block lets the player in.
    return {"result": NOERR, "count": 1,
            "bklist": [pack({"name": BLOCK["name"], "player_num": len(svc.online),
                             "max_player_num": BLOCK["max_player_num"], "block_id": BLOCK["block_id"]})]}


@cmd("CMD_JOIN_BLOCK")
def join_block(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_LEAVE_BLOCK")
def leave_block(svc, sess, req):
    _st(svc)["subs"].discard(_pid(sess))
    return {"result": NOERR}


@cmd("CMD_REQWATCHL_ROOMLIST")
def reqwatchl_roomlist(svc, sess, req):
    st = _st(svc)
    st["subs"].add(_pid(sess))
    return {"result": NOERR, **_wl("room_id", "RELOAD", [room_entry(svc, r) for r in st["rooms"].values()])}


# ---- room life cycle --------------------------------------------------------------------------------
def _leave(svc, pid, why=""):
    """Take pid out of its room: hand the room over, or delete it when it becomes empty."""
    st = _st(svc)
    room = room_of(svc, pid)
    if room is None:
        return False
    room["members"].remove(pid)
    room["entries"].pop(pid, None)
    room["listening"].discard(pid)
    room["left_game"].discard(pid)
    if not room["members"]:
        st["rooms"].pop(room["id"], None)
        roomlist_push(svc, room, "DEL")
        log.info("ROOM %d closed (%s left%s)", room["id"], pid, f", {why}" if why else "")
        return True
    playerlist_push(svc, room, "DEL", gone=pid)
    if room["owner"] == pid:
        new = room["owner"] = room["members"][0]
        _push(svc, new, "CMD_WATCH_ROOMSTATE", {"event": "AppointOwner", "state": room["state"], "owner_pid": new,
                                                "password": room["opts"]["password"]})
        for p in room["members"][1:]:
            _push(svc, p, "CMD_WATCH_ROOMSTATE", {"event": "OwnerChanged", "state": room["state"], "owner_pid": new,
                                                  "password": ""})
        playerlist_push(svc, room, "UPDATE")
        log.info("ROOM %d: owner %s left, %s is the owner now", room["id"], pid, new)
    roomlist_push(svc, room, "UPDATE")
    _after_match(svc, room)                          # the one who hadn't left the match screen is gone
    return True


def _after_match(svc, room):
    """Everyone still in the room has left the match: the room waits for the next one."""
    if room["state"] == "WAITING" or not room["left_game"] >= set(room["members"]):
        return
    room.update(state="WAITING", entries={}, left_game=set())
    room["game_no"] += 1
    for p in room["members"]:
        _push(svc, p, "CMD_WATCH_ROOMSTATE", {"event": "StateChanged", "state": "WAITING",
                                              "owner_pid": room["owner"], "password": ""})
    _later(roomlist_push, svc, room, "UPDATE")
    log.info("ROOM %d: back in the room after the match", room["id"])


@cmd("CMD_CREATEJOIN_ROOM")
def createjoin_room(svc, sess, req):
    st = _st(svc)
    pid = _pid(sess)
    _leave(svc, pid, "made a new room")              # a player is in one room at a time
    rid = st["next"]; st["next"] += 1
    room = {"id": rid, "owner": pid, "members": [pid], "state": "WAITING", "entries": {}, "game_no": 0,
            "env": dict(DEFAULT_ENV), "listening": set(), "left_game": set(),
            "max_players": max(2, min(int(req.get("max_players") or 2), 2)),
            "opts": {"name": str(req.get("name") or f"Room {rid}")[:64], "password": str(req.get("password") or ""),
                     "is_invite_limit": _yes(req.get("is_invite_limit")), "vs_com": str(req.get("vs_com") or "PL_VS_PL"),
                     "is_original_player": _yes(req.get("use_original_player"), "YES"),
                     "is_anyplayers": _yes(req.get("is_anyplayers"), "YES"),
                     "is_select_gk": _yes(req.get("is_select_gk"), "YES"),
                     "auto_start_num": int(req.get("auto_start_num") or 0),
                     "game_rating_limit": str(req.get("game_rating_limit") or "NONE")}}
    st["rooms"][rid] = room
    st["subs"].add(pid)
    log.info("ROOM %d created by %s: %r%s", rid, pid, room["opts"]["name"], " (password)" if room["opts"]["password"] else "")
    _later(roomlist_push, svc, room, "ADD")         # the owner looks for its own room in the room list
    return {"result": NOERR, "room_id": rid}


@cmd("CMD_SET_GAMEENV")
def set_gameenv(svc, sess, req):
    room = room_of(svc, _pid(sess))
    env = _unpack_env(req.get("game_env"))
    if room is not None and env:
        room["env"].update(env)
        _later(roomlist_push, svc, room, "UPDATE")  # the ADD went out before this arrived
    return {"result": NOERR}


@cmd("CMD_REQWATCHL_ROOMPLAYERLIST_NTL")
def reqwatchl_roomplayerlist(svc, sess, req):
    try:
        room = _st(svc)["rooms"].get(int(req.get("room_id") or 0))
    except (TypeError, ValueError):
        room = None
    room = room or room_of(svc, _pid(sess))
    if room is None:
        return {"result": "ERR_ROOMNOTFOUND"}
    # a new watch request: its cache is empty, so this RELOAD is safe (also for the details screen)
    return {"result": NOERR, **_wl("pid", "RELOAD", [room_player(svc, room, m, sess) for m in room["members"]])}


@cmd("CMD_JOIN_ROOM_NTL")
def join_room(svc, sess, req):
    pid = _pid(sess)
    try:
        room = _st(svc)["rooms"].get(int(req.get("room_id") or 0))
    except (TypeError, ValueError):
        room = None
    if room is None:
        return {"result": "ERR_ROOMNOTFOUND"}
    if pid not in room["members"]:
        if len(room["members"]) >= room["max_players"]:
            return {"result": "ERR_ROOMISFULL"}
        if room["state"] != "WAITING":
            return {"result": "ERR_ROOMSTATNOTMATCH"}
        if room["opts"]["password"] and str(req.get("password") or "") != room["opts"]["password"]:
            return {"result": "ERR_PASSWDINCORRECT"}
        owner_sess = svc.online.get(room["owner"])
        if owner_sess is not None and not pesmatch._compatible(owner_sess, sess):
            # different player data (or mod math) ends the match 2 s after kick-off ("Unable to connect.",
            # GNCX_0006); say it now: the game shows "Data Pack Incompatible." for this reply
            pesmatch._note_mismatch(svc, room["owner"], pid, owner_sess, sess)
            log.info("ROOM %d: %s refused, game data differs from the owner's", room["id"], pid)
            return {"result": "ERR_DIFFDCVERSION"}
        _leave(svc, pid, "joined another room")
        room["members"].append(pid)
        log.info("ROOM %d: %s joined", room["id"], pid)
    others = [p for p in room["members"] if p != pid]
    _later(roomlist_push, svc, room, "UPDATE")
    _later(playerlist_push, svc, room, "UPDATE", set(others), delay=ENTRY_PUSH_DELAY)
    owner = room["owner"]
    p = _player(svc, pesmatch._username(svc, owner)) or {"pid": owner, "name": f"Player{owner}"}
    room_owner = {"pid": owner, "pname": _pname(p), **_addrs(svc, owner, sess)}
    return {"result": NOERR, "room_owner": pack(room_owner), "room_stat": room["state"]}


@cmd("CMD_LEAVE_ROOM")
def leave_room(svc, sess, req):
    _leave(svc, _pid(sess), str(req.get("error_code") or ""))
    return {"result": NOERR}


@cmd("CMD_ENTRY_GAME")
def entry_game(svc, sess, req):
    pid = _pid(sess)
    room = room_of(svc, pid)
    if room is None:
        return {"result": "ERR_NOTINROOM"}
    room["listening"].add(pid)                       # from now on its player list may be updated
    if str(req.get("entry", "")).upper() == "TRUE":
        if pid not in room["entries"]:
            taken = {e["entryNum"] for e in room["entries"].values()}     # not len()+1: 1 may have left
            room["entries"][pid] = {"entryNum": next(n for n in range(1, len(taken) + 2) if n not in taken),
                                    "side": req.get("side")}
    else:
        room["entries"].pop(pid, None)

    def pushes():
        eg = [pack({"pid": p, "entryNum": e["entryNum"], "gameNum": room["game_no"]}) for p, e in room["entries"].items()]
        for p in room["members"]:
            _push(svc, p, "CMD_WATCH_ENTRY_GAME", {"entry_count": len(eg), "entrygame": eg})
        playerlist_push(svc, room, "UPDATE")         # the owner's start check runs after a list update
    _later(pushes, delay=ENTRY_PUSH_DELAY)
    return {"result": NOERR, "entryflag": req.get("entry", "FALSE"), "gameNum": room["game_no"]}


@cmd("CMD_SET_GAMEMEMBER")
def set_gamemember(svc, sess, req):
    # Lobby: the owner asks to start. Quick Match / co-op owners send it too, outside any room: plain NOERR.
    pid = _pid(sess)
    room = room_of(svc, pid)
    if room is None or room["owner"] != pid or room["state"] != "WAITING":
        return {"result": NOERR}
    if len(room["members"]) < 2 or set(room["entries"]) != set(room["members"]):
        return {"result": NOERR}                     # sent on every join/entry change; start only when all entered
    room["state"] = "SETENV"
    room["left_game"] = set()
    mid = pesmatch.new_room(svc, owner=room["owner"], members=list(room["members"]), lobby_room=room["id"])
    log.info("ROOM %d: match %d starts (%s)", room["id"], mid, " vs ".join(map(str, room["members"])))
    for p in room["members"]:
        _push(svc, p, "CMD_WATCH_DECIDE_GAMEENV", {"game_env": pack(room["env"])})
        _push(svc, p, "CMD_WATCH_ROOMSTATE", {"event": "StateChanged", "state": "SETENV", "owner_pid": room["owner"],
                                              "password": ""})
    _later(roomlist_push, svc, room, "UPDATE")
    return {"result": NOERR}


@cmd("CMD_LEAVE_GAME")
def leave_game(svc, sess, req):
    pid = _pid(sess)
    room = room_of(svc, pid)
    if room is None:
        return {"result": NOERR}
    room["left_game"].add(pid)
    _after_match(svc, room)
    return {"result": NOERR}


@cmd("CMD_SET_ROOM_OPTION")
def set_room_option(svc, sess, req):
    pid = _pid(sess)
    room = room_of(svc, pid)
    if room is None:
        return {"result": "ERR_NOTINROOM"}
    if room["owner"] != pid:
        return {"result": "ERR_NOTROOMOWNER"}
    o = room["opts"]
    if req.get("name"):
        o["name"] = str(req["name"])[:64]
    if "password" in req:
        o["password"] = str(req.get("password") or "")
    for k in ("is_anyplayers",):
        if k in req:
            o[k] = _yes(req[k])
    if "auto_start_num" in req:
        o["auto_start_num"] = int(req.get("auto_start_num") or 0)
    if req.get("game_rating_limit"):
        o["game_rating_limit"] = str(req["game_rating_limit"])
    _later(roomlist_push, svc, room, "UPDATE")
    return {"result": NOERR}


def disconnected(svc, sess):
    """A player's gate connection ended: out of the block and out of their room."""
    pid = _pid(sess)
    if pid is None or svc.online.get(pid) not in (None, sess):
        return                                       # an older connection of a player who is still online
    _st(svc)["subs"].discard(pid)
    _leave(svc, pid, "disconnected")


def snapshot(svc):
    """Rooms for the admin page."""
    return [{"id": r["id"], "name": r["opts"]["name"], "owner": r["owner"], "members": list(r["members"]),
             "state": r["state"], "password": bool(r["opts"]["password"])} for r in _st(svc)["rooms"].values()]


# ---- chat -------------------------------------------------------------------------------------------
CHAT_MAX = 200


@cmd("CMD_SEND_TEXTCHAT")
def send_textchat(svc, sess, req):
    """Room / match / block chat: WATCH_TEXTCHAT {channel, from_pid, name, statement} to the channel's players.
    The sender gets its own line back too (whether the game prints it locally is not known; rooms.md)."""
    pid = _pid(sess)
    channel = str(req.get("channel") or "ROOM")
    text = str(req.get("statement") or "")[:CHAT_MAX]
    if not text.strip():
        return {"result": NOERR}
    if channel == "BLOCK":
        to = set(_st(svc)["subs"]) | {pid}
    elif channel == "ROOM":
        room = room_of(svc, pid)
        to = set(room["members"]) if room else {pid}
    else:                                            # in-match / co-op channels: the players of this match
        _, mroom = pesmatch._room_of(svc, pid)
        to = set(mroom["members"]) if mroom else {pid}
    listed = req.get("list_pid")
    if str(req.get("list_flg") or "") == "CHAT_SEND" and isinstance(listed, list) and listed:
        to &= {int(p) for p in listed if str(p).isdigit()} | {pid}
    p = _player(svc, pesmatch._username(svc, pid)) or {}
    body = {"channel": channel, "from_pid": pid, "name": p.get("name") or f"Player{pid}", "statement": text}
    for q in to:
        _push(svc, q, "CMD_WATCH_TEXTCHAT", body)
    log.info("CHAT %s %s -> %s: %r", channel, pid, sorted(to), text)
    return {"result": NOERR}
