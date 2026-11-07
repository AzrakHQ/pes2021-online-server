"""Match replays: every online match is recorded, and anyone can watch the recent ones in the game.

How it works (notes/re/functions/spectate2.md, livetests.md "Spectating", live with test code 2026-11-06):
- Recording: GET_MATCHID says upload "YES". Both players' games then send START_SEND_GAME_SEQUENCE_DATA and,
  about every 5 s, SEND_GAME_SEQUENCE_DATA {match_id, game_seq_data: [blob{type, data (base64 of 256 bytes),
  data_len}]}. Each record's data[:data_len] is already an item the viewer understands. The first player to
  start is the recorder; the other stream is dropped (two streams can't be mixed). Every message becomes one
  chunk file N.enc = IV + AES-256-CBC(game key, PKCS7(0x00 + items)). The last chunk ends with `03 00` (if no
  type-3 item came) + "END_OF_DATA". Closed at full time (8 s after END), on LEAVE_GAME or an abnormal end.
- Watching (no game change needed): eFootball -> Online Tournament -> the "Match Replays" competition -> pick any
  team -> the PES League lobby -> X (Match List) = CMD_GET_WATCHING_MATCH_LIST -> a match -> Spectate Match.
  The game then downloads <WATCHING_GAME url>/<match_id>/1.enc, 2.enc, ... (HTTP, served by httpgate under /r).
Kept in <data>/replays/<match_id>/ (N.enc + meta.json), the newest KEEP only. REPLAYS=0 turns recording off.
"""
import base64, json, logging, os, re, shutil, threading, time

import msgpack

from .pescmds import NOERR, cmd, _player
from . import pesmatch

log = logging.getLogger("pesserver.replay")
KEEP = 20
LIST_MAX = 10
KEY = bytes.fromhex("35a110675829e36845dc775162cca1bb69b786ed1e73d82ea9fe7e6176743881")   # game key, 0x142897bd0
_lock = threading.Lock()
_live = {}            # match_id -> state while recording


def enabled():
    return os.environ.get("REPLAYS", "1") != "0"


def _dir(svc):
    d = os.path.join(getattr(svc, "data_dir", "") or ".", "replays")
    os.makedirs(d, exist_ok=True)
    return d


def _safe(mid):
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{1,40}", str(mid or "")))


def _enc(plain):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    p = padding.PKCS7(128).padder()
    data = p.update(plain) + p.finalize()
    iv = os.urandom(16)
    e = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    return iv + e.update(data) + e.finalize()


def _dec(blob):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    d = Cipher(algorithms.AES(KEY), modes.CBC(blob[:16])).decryptor()
    data = d.update(blob[16:]) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return u.update(data) + u.finalize()


def _name(svc, pid):
    p = _player(svc, pesmatch._username(svc, pid)) if isinstance(pid, int) else None
    return (p or {}).get("name") or f"Player {pid}"


def _save_meta(svc, mid, st):
    meta = {k: v for k, v in st.items() if k != "type3"}
    tmp = os.path.join(_dir(svc), mid, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f)
    os.replace(tmp, os.path.join(_dir(svc), mid, "meta.json"))


def _prune(svc):
    d = _dir(svc)
    ms = sorted((m for m in os.listdir(d) if os.path.isdir(os.path.join(d, m))),
                key=lambda m: os.path.getmtime(os.path.join(d, m)), reverse=True)
    for m in ms[KEEP:]:
        if m not in _live:
            shutil.rmtree(os.path.join(d, m), ignore_errors=True)


# ---- recording ------------------------------------------------------------------------------------------
@cmd("CMD_START_SEND_GAME_SEQUENCE_DATA")
def start_send(svc, sess, req):
    pid = pesmatch._pid(sess)
    rid, room = pesmatch._room_of(svc, pid)
    mid = (room or {}).get("match_id")
    if not enabled() or not _safe(mid):
        return {"result": NOERR}                  # an error here would abort the match (MXNV264)
    with _lock:
        if mid not in _live and not os.path.exists(os.path.join(_dir(svc), mid, "meta.json")):
            members = list((room or {}).get("members", [pid]))
            home = (room or {}).get("owner", pid)
            away = next((p for p in members if p != home), None)
            os.makedirs(os.path.join(_dir(svc), mid), exist_ok=True)
            _live[mid] = {"match_id": mid, "uploader": pid, "home": home, "away": away,
                          "home_name": _name(svc, home), "away_name": _name(svc, away), "t0": int(time.time()),
                          "n": 0, "type3": False, "closed": False, "rid": rid}
            _prune(svc)
            log.info("recording match %s (from pid %s)", mid, pid)
    return {"result": NOERR}


def _chunk(svc, mid, items, final=False):
    st = _live[mid]
    st["n"] += 1
    plain = b"\x00" + items
    if final:
        plain += (b"" if st["type3"] else b"\x03\x00") + b"END_OF_DATA"
    with open(os.path.join(_dir(svc), mid, f"{st['n']}.enc"), "wb") as f:
        f.write(_enc(plain))
    _save_meta(svc, mid, st)


def close(svc, mid, why):
    with _lock:
        st = _live.get(mid)
        if not st or st["closed"]:
            return
        st["closed"] = True
        _score(svc, st)
        _chunk(svc, mid, b"", final=True)
        _live.pop(mid, None)
    log.info("recording %s closed (%s): %d chunks", mid, why, st["n"])


@cmd("CMD_SEND_GAME_SEQUENCE_DATA")
def send_seq(svc, sess, req):
    pid = pesmatch._pid(sess)
    mid = str(req.get("match_id") or "")
    with _lock:
        st = _live.get(mid)
        if st is None or st["closed"] or pid != st["uploader"]:
            return {"result": NOERR}              # (the other player's copy of the same match is not kept)
        items = b""
        for b in req.get("game_seq_data") or []:
            try:
                raw = b.encode("utf-8", "surrogateescape") if isinstance(b, str) else bytes(b)
                r = msgpack.unpackb(raw, raw=False, unicode_errors="surrogateescape")
                data = base64.b64decode(r.get("data", ""))[: int(r.get("data_len", 0) or 0)]
            except Exception:  # noqa: BLE001 - one bad record must not end the recording
                continue
            if r.get("type") == 3:
                st["type3"] = True
            items += data
        _chunk(svc, mid, items)
        done = st["type3"]
    if done:
        close(svc, mid, "end item")
    return {"result": NOERR}


def _score(svc, st):
    room = pesmatch._state(svc)["rooms"].get(st.get("rid")) or {}
    goals = room.get("goals", set())
    st["home_score"] = sum(1 for _, g in goals if g == "HOME")
    st["away_score"] = sum(1 for _, g in goals if g == "AWAY")
    teams = room.get("teams", {})
    st["home_team"] = int(teams.get(st["home"]) or 0)
    st["away_team"] = int(teams.get(st["away"]) or 0)


def observe(svc, sess, msgid, req):
    """Called for every command (pescmds.dispatch): close a recording when its match ends."""
    if not _live or msgid not in ("CMD_CHANGE_GAMEPHASE", "CMD_LEAVE_GAME", "CMD_SEND_ABNORMALEND", "CMD_LEAVE_ROOM"):
        return
    pid = pesmatch._pid(sess)
    rid, _room = pesmatch._room_of(svc, pid)
    if msgid == "CMD_SEND_ABNORMALEND":
        # either player's early end ends the match: close the recording of that room, whoever records it
        mid = next((m for m, st in _live.items() if st.get("rid") == rid and pid in (st["home"], st["away"])), None)
    else:
        mid = next((m for m, st in _live.items() if st["uploader"] == pid and st.get("rid") == rid), None)
    if mid is None:
        return
    if msgid == "CMD_CHANGE_GAMEPHASE":
        if req.get("phase") == "END":
            import asyncio
            try:
                asyncio.get_running_loop().call_later(8.0, close, svc, mid, "full time")
            except RuntimeError:
                close(svc, mid, "full time")
        return
    close(svc, mid, msgid[4:].lower())


def disconnected(svc, pid):
    """The player's connection closed: close every recording they were part of (a crash, a closed game)."""
    for mid in [m for m, st in list(_live.items()) if pid in (st["uploader"], st["home"], st["away"])]:
        close(svc, mid, "disconnect")


def chunk_file(svc, mid, name):
    """Path of a replay chunk for the HTTP server, or None."""
    if not _safe(mid) or not re.fullmatch(r"\d{1,5}\.enc", name or ""):
        return None
    p = os.path.join(_dir(svc), mid, name)
    return p if os.path.isfile(p) else None


# ---- watching: the PES League path to the lobby's Match List (peslg_path.md) -------------------------------
TEAMS = [45, 8, 14, 50]          # national teams (the team you pick here plays no role)


def _compe():
    now = int(time.time())
    return {"compe_id": 1, "compe_db_id": 1, "compe_name": "Match Replays", "is_myclub": "NO",
            "match_type": "PES_LEAGUE_MATCH_1VS1_LOBBY", "start_date": now - 86400, "end_date": now + 30 * 86400,
            "match_end_date": now + 30 * 86400, "entry_limit_date": now + 30 * 86400, "entry_user_limit": "NO",
            "entry_user_max": 1000, "entry_user_num": 1, "entry_limit_division": "NO", "entry_limit_division_min": 1,
            "entry_limit_division_max": 10, "is_end": "NO", "is_entry": "NO", "is_gm_account": "NO",
            "is_no_check": "NO", "mode": "PESLEAGUE"}


def _pack(o):
    return msgpack.packb(o, use_bin_type=True)


@cmd("CMD_JOIN_PESLEAGUE_COMPE_MODE")
def join_peslg_mode(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_LEAVE_PESLEAGUE_COMPE_MODE")
def leave_peslg_mode(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_GET_PESLEAGUE_MAINMENU_INFO")
def peslg_mainmenu(svc, sess, req):
    return {"result": NOERR, "division": "D1", "new_result": "NO"}


@cmd("CMD_GET_PESLEAGUE_COMPE_LIST")
def peslg_compe_list(svc, sess, req):
    c = {**_compe(), "banner_file_info": _pack({}), "list_banner_file_info": _pack({})}
    return {"result": NOERR, "compe_list": [_pack(c)], "list_num": 1, "list_max": 1, "is_end": "YES"}


@cmd("CMD_GET_PESLEAGUE_COMPE_INFO")
def peslg_compe_info(svc, sess, req):
    c = _compe()
    # the fields must also be inside compe_data, or the game opens the eFootball League 1v1 menu (live)
    return {"result": NOERR, **c, "eula_url": "", "entry_compe": "NO",
            "compe_data": _pack({**c, "eula_url": "", "entry_compe": "NO"})}


@cmd("CMD_GET_PESLEAGUE_COMPE_TEAM_LIST")
def peslg_team_list(svc, sess, req):
    return {"result": NOERR, "team_list": TEAMS, "team_list_num": len(TEAMS)}


@cmd("CMD_JOIN_PESLEAGUE_COMPE")
def peslg_join(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO")
def peslg_1vs1(svc, sess, req):
    now = int(time.time())
    return {"result": NOERR, "compe_end_time": now + 30 * 86400, "draw_count": 0, "lose_count": 0, "match_num": 0,
            "matching_first_date": now, "matching_interval": 0, "matching_time": 0, "point": 0, "rank_type": "NONE",
            "regulation_match_num": 0, "team_id": TEAMS[0], "team_name": "", "win_count": 0}


@cmd("CMD_SEND_WATCHING_RESULT")
def send_watching_result(svc, sess, req):
    return {"result": NOERR}


def recordings(svc):
    """Finished (or still recording) matches, newest first: [(match_id, meta)]."""
    d = _dir(svc)
    out = []
    for m in os.listdir(d):
        try:
            with open(os.path.join(d, m, "meta.json")) as f:
                out.append((m, json.load(f)))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda x: x[1].get("t0", 0), reverse=True)
    return out


def _pname(name, pid):
    return _pack({"name": name, "xuid_one": "", "is_deleted": "NO",
                  "psn_user_id": _pack({"account_id": pid or 0, "online_id": "", "is_valid_cache": "YES"})})


@cmd("CMD_GET_WATCHING_MATCH_LIST")
def watching_list(svc, sess, req):
    try:
        start = max(0, int(req.get("index") or 0))
    except (TypeError, ValueError):
        start = 0
    recs = recordings(svc)
    entries = []
    for mid, m in recs[start:start + LIST_MAX]:
        def score(n):
            return _pack({"score_1st": n, "score_2nd": 0, "score_ex1st": 0, "score_ex2nd": 0, "score_pk": 0})
        players = [_pack({"pid": m.get(side) or 0, "pname": _pname(m.get(side + "_name") or "", m.get(side)),
                          "side": side.upper(), "is_com": "NO", "cpu_level": "NORMAL", "is_follow": "NO",
                          "is_guest": "NO"}) for side in ("home", "away")]
        gr = _pack({"date": int(m.get("t0", 0)), "ex_type": "", "pk_flag": "NO", "no_match": "NO", "player": players,
                    "team": [m.get("home_team") or TEAMS[0], m.get("away_team") or TEAMS[1]],
                    "score": [score(m.get("home_score", 0)), score(m.get("away_score", 0))],
                    "match_category": "PESLEAGUE_LOBBY", "gameend_reason": "NORMAL_END"})
        entries.append(_pack({"match_id": mid, "game_phase": "2ND", "game_result": gr}))
    return {"result": NOERR, "match_list": entries, "match_list_num": len(entries), "total_match_num": len(recs)}
