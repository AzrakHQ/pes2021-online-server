"""Text the server can put on players' screens, all tested against the real game (2026-11-05).

- News (CMD_GET_GLOBALINFOTITLELIST + CMD_GET_INFOBODY): the title is listed on the "Online Information"
  screen at sign-in, and title + body open as a pop-up when the player reaches the main menu.
- Box before Quick Match (CMD_GET_URGENT_ANNOUNCEMENT): asked each time a player enters an online mode;
  the reply's title and body open as a dialog with OK. We serve each message once per player.
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
        return {"news": n and {"title": n["title"], "body": n["body"], "date": n["date"], "seen": len(st["seen"])},
                "restart": r, "pending": len(st["personal"])}


class TooLong(ValueError):
    """The reply would need more than one frame; the game rejects frame bodies over 0x410 bytes and
    our encoder doesn't split messages yet. Long non-Latin text (2-3 bytes a letter) can hit this."""


def _check_size(reply):
    from . import ncl
    if len(ncl.bf_encrypt(ncl.pack_plain({"msgid": "CMD_GET_INFOBODY", "rqid": 99999, **reply}))) > ncl.MAX_BODY - 16:
        raise TooLong("too long")


def set_news(svc, title, body):
    title, body = clean(title, TITLE_MAX, lines=False), clean(body, BODY_MAX)
    now = int(time.time())
    with _lock:
        st = _load(svc)
        # a new id each time, so games treat an edited message as new
        news = {"id": max(now % 2_000_000_000, (st["news"] or {}).get("id", 0) + 1),
                "title": title, "body": body, "date": now}
        _check_size(_body_reply(news))            # the biggest of the three replies
        st["news"] = news
        st["seen"] = []
        _save(svc)


def clear_news(svc):
    with _lock:
        _load(svc)["news"] = None
        _load(svc)["seen"] = []
        _save(svc)


def set_restart(svc, minutes):
    with _lock:
        _load(svc)["restart"] = int(time.time() + minutes * 60) if minutes else None
        _save(svc)


def tell(svc, pid, title, body):
    """A one-time message for one player, shown the next time they enter an online mode."""
    with _lock:
        st = _load(svc)
        st["personal"][str(pid)] = {"id": int(time.time()) % 2_000_000_000, "title": clean(title, TITLE_MAX, False),
                                    "body": clean(body, BODY_MAX)}
        _save(svc)


def forget(svc, pid):
    """Drop a deleted player's waiting note and news mark, so a new player with the same number starts clean."""
    with _lock:
        st = _load(svc)
        st["personal"].pop(str(pid), None)
        st["seen"] = [p for p in st["seen"] if p != pid]
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


@cmd("CMD_GET_INFOBODY")
def info_body(svc, sess, req):
    with _lock:
        n = _load(svc)["news"]
    if not n or req.get("announce_id") != n["id"]:
        return {"result": NOERR}
    return _body_reply(n)


def _body_reply(n):
    info = {"announce_basic_info": msgpack.packb(_basic(n)), "body": n["body"], "link_url": "",
            "direct_move": "NO", "use_template": "NO", "template_id": 0,
            "banner_file_info": msgpack.packb({"filename": "", "hash_str": ""}), "compe_name": "",
            "compe_start_time": 0}
    return {"result": NOERR, "announce_info": msgpack.packb(info)}


@cmd("CMD_GET_URGENT_ANNOUNCEMENT")
def urgent(svc, sess, req):
    pid = req.get("pid") or getattr(sess, "pes_pid", None)
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


@cmd("CMD_GET_MAINTENANCE_INFO")
def maintenance(svc, sess, req):
    with _lock:
        r = _load(svc)["restart"]
    if r and time.time() <= r + RESTART_GRACE:
        return {"result": NOERR, "maintenance_warn": "YES", "maintenance_time": r}
    return {"result": NOERR}
