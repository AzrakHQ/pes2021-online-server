"""Text the server can put on players' screens, all tested against the real game (2026-11-05).

- News (CMD_GET_GLOBALINFOTITLELIST + CMD_GET_INFOBODY): the title is listed on the "Online Information"
  screen at sign-in, and title + body open as a pop-up when the player reaches the main menu.
- Box before Quick Match (CMD_GET_URGENT_ANNOUNCEMENT): asked each time a player enters an online mode;
  the reply's title and body open as a dialog with OK. We serve each message once per player.
- Messages to one player (CMD_GET_PRIVATEINFOTITLELIST + CMD_GET_INFOBODY is_private): the message goes into that
  player's own "Online Information" list (last INBOX_MAX, for INBOX_DAYS), same layout as the news. The game asks
  for the list at login and on entering an online mode, pops up each new item once (popup_type ONCE; it remembers
  closed items across restarts) and keeps it in the list. Seen live 2026-11-07. (Until then these messages went
  through the box before Quick Match; with both, the player got the same text twice in a row.)
- Lobby welcome (CMD_GET_LOBBYINFO {lobby_id}): has_info YES opens a notice window (title, then sentence_info and
  sentence_intro as two paragraphs, OK to close) when a player enters the Friendly Match Lobby (live 2026-11-07).
  Each player sees the short how-it-works note once.
- Planned restart (CMD_GET_MAINTENANCE_INFO): asked on "Go to Matchmaking"; maintenance_warn "YES" with a
  unix time makes the game's own "maintenance starting at HH:MM today... start a match now?" Yes/No dialog.

Enum values come from the game (docs/functions.html): "YES" turns a flag on ("TRUE" is ignored).
State lives in <data>/announce.json so it survives a restart.
"""
import json, logging, os, threading, time

import msgpack

from .pescmds import NOERR, blobs, cmd

log = logging.getLogger("pesserver.announce")

TITLE_MAX, BODY_MAX, REASON_MAX = 60, 600, 200
INBOX_MAX, INBOX_DAYS = 5, 30
RESTART_GRACE = 15 * 60        # keep warning this long after the planned time, then forget it
_lock = threading.Lock()
_STATE = {}                    # data_dir -> dict


def _path(svc):
    return os.path.join(getattr(svc, "data_dir", "") or ".", "announce.json")


def _load(svc):
    key = _path(svc)
    if key not in _STATE:
        try:
            with open(key) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        st.setdefault("news", None)          # {id, title, body, date}
        st.setdefault("seen", [])            # pids that got the news box before Quick Match
        st.setdefault("restart", None)       # unix time
        st.setdefault("personal", {})        # str(pid) -> {id, title, body}
        st.setdefault("lobby_seen", [])      # pids that got the Friendly Match Lobby welcome
        st.setdefault("inbox", {})           # str(pid) -> [{id, title, body, date, kind, seen}], newest last
        st.setdefault("seen_notes", [])      # messages players have opened, newest first (the admin page lists them)
        st.setdefault("history", [])         # earlier messages to everyone, newest first
        if st["restart"] and st["restart"] < time.time():
            st["restart"] = None             # starting up after the planned time: the restart happened
        _STATE[key] = st
    return _STATE[key]


def _save(svc):
    path = _path(svc)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(_STATE[path], f)
        os.replace(tmp, path)
    except OSError:
        log.exception("saving %s", path)


def clean(text, limit, lines=True):
    """Printable text only; newlines kept in bodies. Raises ValueError when empty."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c.isprintable() or (lines and c == "\n"))
    text = "\n".join(l.rstrip() for l in text.split("\n")).strip()
    if not text:
        raise ValueError("empty")
    return text[:limit]


# ---- admin side ---------------------------------------------------------------------------------
def snapshot(svc):
    with _lock:
        st = _load(svc)
        r = st["restart"]
        if r and time.time() > r + RESTART_GRACE:
            st["restart"] = r = None
            _save(svc)
        n = st["news"]
        waiting = sorted(({"pid": int(k), "id": m["id"], "title": m["title"], "body": m["body"], "sent": m.get("date"),
                           "kind": m.get("kind", "note")}
                          for k in list(st["inbox"]) for m in _inbox(st, k) if not m.get("seen")),
                         key=lambda x: (-(x["sent"] or 0), -x["id"]))
        return {"news": n and {"title": n["title"], "body": n["body"], "date": n["date"], "seen": len(st["seen"])},
                "restart": r, "pending": len(waiting), "personal": waiting,
                "seen_notes": st["seen_notes"][:8], "history": st["history"][:6]}


class TooLong(ValueError):
    """The reply would need more than one frame; the game rejects frame bodies over 0x410 bytes and
    our encoder doesn't split messages yet. Long non-Latin text (2-3 bytes a letter) can hit this."""


def _check_size(reply):
    from . import ncl
    if len(ncl.bf_encrypt(ncl.pack_plain({"msgid": "CMD_GET_INFOBODY", "rqid": 99999, **reply}))) > ncl.MAX_BODY - 16:
        raise TooLong("too long")


def fits(title, body, personal=False):
    """How much of the game's message frame a draft fills (the admin page's live meter): 0-100+.
    personal: a message for one player's Information list."""
    from . import ncl
    try:
        news = {"id": 2_000_000_000, "title": clean(title, TITLE_MAX, lines=False), "body": clean(body, BODY_MAX),
                "date": int(time.time())}
    except ValueError:
        return 0
    reply = _body_reply(news, _PERSONAL if personal else None)
    size = len(ncl.bf_encrypt(ncl.pack_plain({"msgid": "CMD_GET_INFOBODY", "rqid": 99999, **reply})))
    return round(100 * size / (ncl.MAX_BODY - 16))


def set_news(svc, title, body):
    title, body = clean(title, TITLE_MAX, lines=False), clean(body, BODY_MAX)
    now = int(time.time())
    with _lock:
        st = _load(svc)
        # a new id each time, so games treat an edited message as new
        news = {"id": max(now % 2_000_000_000, (st["news"] or {}).get("id", 0) + 1),
                "title": title, "body": body, "date": now}
        _check_size(_body_reply(news))            # the biggest of the three replies
        _archive(st)
        st["news"] = news
        st["seen"] = []
        _save(svc)


def _archive(st):
    """Keep the message being replaced or removed, so the admin page can show it and reuse it."""
    n = st["news"]
    if n:
        st["history"].insert(0, {"title": n["title"], "body": n["body"], "date": n["date"], "until": int(time.time()),
                                 "seen": len(st["seen"])})
        del st["history"][6:]


def clear_news(svc):
    with _lock:
        _archive(_load(svc))
        _load(svc)["news"] = None
        _load(svc)["seen"] = []
        _save(svc)


def set_restart(svc, minutes):
    with _lock:
        _load(svc)["restart"] = int(time.time() + minutes * 60) if minutes else None
        _save(svc)


def tell(svc, pid, title, body, kind="note"):
    """A message for one player: pops up once at their next login or online-mode entry, then stays in their
    Information list. kind "note" (from the admin) or "kick" (the reason they were disconnected)."""
    with _lock:
        st = _load(svc)
        box = st["inbox"].get(str(pid), [])
        msg = {"id": max([int(time.time()) % 2_000_000_000] + [m["id"] + 1 for m in box]),
               "title": clean(title, TITLE_MAX, False), "body": clean(body, BODY_MAX), "date": int(time.time()),
               "kind": kind, "seen": None}
        _check_size(_body_reply(msg, _PERSONAL))
        st["inbox"][str(pid)] = (box + [msg])[-INBOX_MAX:]
        _save(svc)


def untell(svc, pid, mid=None):
    """Take back messages the player hasn't opened yet (one, with mid). Returns False if there was none."""
    with _lock:
        st = _load(svc)
        box = st["inbox"].get(str(pid), [])
        keep = [m for m in box if m.get("seen") or (mid is not None and m["id"] != mid)]
        if len(keep) == len(box):
            return False
        if keep:
            st["inbox"][str(pid)] = keep
        else:
            st["inbox"].pop(str(pid), None)
        _save(svc)
    return True


def forget(svc, pid):
    """Drop a deleted player's waiting note and news mark, so a new player with the same number starts clean."""
    with _lock:
        st = _load(svc)
        st["personal"].pop(str(pid), None)
        st["inbox"].pop(str(pid), None)
        st["lobby_seen"] = [p for p in st["lobby_seen"] if p != pid]
        st["replay_seen"] = [p for p in st.get("replay_seen", []) if p != pid]
        st["seen"] = [p for p in st["seen"] if p != pid]
        st["seen_notes"] = [n for n in st["seen_notes"] if n.get("pid") != pid]
        _save(svc)


# ---- game side ----------------------------------------------------------------------------------
def _basic(n):
    return {"announce_id": n["id"], "announce_type": "NO_BANNER_INFO", "title": n["title"], "date": n["date"],
            "event_id": 0, "evoked_text": "", "filename": "", "hash_str": "", "is_auto_make_compe_name": "NO",
            "is_use_emblem": "NO", "list_banner_file_info": msgpack.packb({"filename": "", "hash_str": ""}),
            "matchday_emblem_info": msgpack.packb({}), "popup_type": "EVERYTIME", "priority": 1,
            "weekly_info_type": ""}


@cmd("CMD_GET_GLOBALINFOTITLELIST")
def global_info_titles(svc, sess, req):
    with _lock:
        n = _load(svc)["news"]
    if not n:
        return {"result": NOERR}             # as before: the game keeps its empty default
    # the count key really is spelled with three n's; the client walks the list itself
    return {"result": NOERR, "announce_info_list": blobs([_basic(n)]), "annnounce_info_list_num": 1}


def _inbox(st, pid, now=None):
    """The player's messages still kept (drops old ones)."""
    cut = (now or time.time()) - INBOX_DAYS * 86400
    box = [m for m in st["inbox"].get(str(pid), []) if m.get("date", 0) >= cut]
    if box:
        st["inbox"][str(pid)] = box
    else:
        st["inbox"].pop(str(pid), None)
    return box


@cmd("CMD_GET_PRIVATEINFOTITLELIST")
def private_info_titles(svc, sess, req):
    pid = getattr(sess, "pes_pid", None) or req.get("pid")      # the session's own player first
    with _lock:
        box = _inbox(_load(svc), pid)
    if not box:
        return {"result": NOERR}
    items = [{**_basic(m), **_PERSONAL} for m in reversed(box)]
    return {"result": NOERR, "announce_info_list": blobs(items), "annnounce_info_list_num": len(items)}


@cmd("CMD_GET_INFOBODY")
def info_body(svc, sess, req):
    aid = req.get("announce_id")
    with _lock:
        st = _load(svc)
        n = st["news"]
        if str(req.get("is_private", "")).upper() == "YES" or not n or aid != n["id"]:
            pid = getattr(sess, "pes_pid", None) or req.get("pid")      # the session's own player first
            m = next((m for m in _inbox(st, pid) if m["id"] == aid), None)
            if m:
                first = not m.get("seen")
                if first:                         # the pop-up asks for the body: the player has it on screen now
                    m["seen"] = int(time.time())
                    if m.get("kind", "note") == "note":
                        st["seen_notes"].insert(0, {"pid": pid, "title": m["title"], "seen": m["seen"], "sent": m.get("date")})
                        del st["seen_notes"][8:]
                    _save(svc)
                reply = _body_reply(m, _PERSONAL)
        else:
            m = None
    if m:
        if first and m.get("kind", "note") == "note":
            from . import events
            events.feed(svc, f"{events.name_of(svc, pid)} opened your message “{m['title']}”", "msg")
        return reply
    if not n or aid != n["id"]:
        return {"result": NOERR}
    return _body_reply(n)


_PERSONAL = {"announce_type": "PERSONAL_INFO", "popup_type": "ONCE"}


def _body_reply(n, over=None):
    info = {"announce_basic_info": msgpack.packb({**_basic(n), **(over or {})}), "body": n["body"], "link_url": "",
            "direct_move": "NO", "use_template": "NO", "template_id": 0,
            "banner_file_info": msgpack.packb({"filename": "", "hash_str": ""}), "compe_name": "",
            "compe_start_time": 0}
    return {"result": NOERR, "announce_info": msgpack.packb(info)}


@cmd("CMD_GET_URGENT_ANNOUNCEMENT")
def urgent(svc, sess, req):
    pid = getattr(sess, "pes_pid", None) or req.get("pid")      # the session's own player first
    with _lock:
        st = _load(svc)
        msg = st["personal"].pop(str(pid), None)
        if msg is None and st["news"] and pid is not None and pid not in st["seen"]:
            msg = st["news"]
            st["seen"].append(pid)
        if msg is None:
            return {"result": NOERR}
        _save(svc)
    log.info("showing %r to pid %s", msg["title"], pid)
    data = {"announcement_id": msg["id"], "online_mode": req.get("online_mode", "QUICK"),
            "announcement_type": req.get("announcement_type", "PRE_MAIN"), "read_check": "NO",
            "title": msg["title"], "body": msg["body"]}
    return {"result": NOERR, "is_announcement": "YES", "announcement_data": msgpack.packb(data)}


LOBBY_TITLE = "Friendly Match Lobby"
LOBBY_INFO = ("Make a match room, or pick a friend's room from the list (a room can have a password).\n"
              "When both players are in the room, both press Start Match.")
LOBBY_INTRO = "After the match you are back in the room for a rematch. Room chat works too."


REPLAY_TITLE = "Match Replays"
REPLAY_INFO = "Recent online matches on this server can be watched here.\nOpen the Match List, pick a match, then Spectate Match."
REPLAY_INTRO = "Every online match is recorded; the newest 20 are kept."


@cmd("CMD_GET_LOBBYINFO")
def lobby_info(svc, sess, req):
    """The lobby's notice window, once per player and lobby (a first-visit how-it-works note)."""
    pid = getattr(sess, "pes_pid", None) or req.get("pid")      # the session's own player first
    if not isinstance(pid, int):
        return {"result": NOERR, "has_info": "NO"}
    replays = getattr(sess, "pes_lobby_type", "FREE") == "PESLEAGUE"      # the Match Replays lobby (pesreplay)
    key = "replay_seen" if replays else "lobby_seen"
    with _lock:
        st = _load(svc)
        seen = st.setdefault(key, [])
        if pid in seen:
            return {"result": NOERR, "has_info": "NO"}
        seen.append(pid)
        _save(svc)
    if replays:
        return {"result": NOERR, "has_info": "YES", "title": REPLAY_TITLE, "sentence_info": REPLAY_INFO,
                "sentence_intro": REPLAY_INTRO}
    return {"result": NOERR, "has_info": "YES", "title": LOBBY_TITLE, "sentence_info": LOBBY_INFO,
            "sentence_intro": LOBBY_INTRO}


@cmd("CMD_GET_MAINTENANCE_INFO")
def maintenance(svc, sess, req):
    with _lock:
        r = _load(svc)["restart"]
    if r and time.time() <= r + RESTART_GRACE:
        return {"result": NOERR, "maintenance_warn": "YES", "maintenance_time": r}
    return {"result": NOERR}
