"""PES 2021 NCL command handlers (msgpack request dict -> response dict).

Field names come from the client's own request/response parsers (strings clustered per command in
.rdata, e.g. CMD_GET_SVRLIST parser 0x141d48430). Unknown commands get a generic NOERR so the client
keeps going while we log what it asks for — that log drives which handler to write next.
"""
import logging, os, sqlite3, time

import msgpack

log = logging.getLogger("pesserver.cmd")

NOERR = "NOERR"
HANDLERS = {}


def blobs(items):
    """Lists of objects are arrays of msgpack-encoded blobs: the client runs its msgpack unpacker
    (0x141c190a0) on each element's (ptr, size) — e.g. svrlist loop at 0x141d48a41."""
    return [msgpack.packb(x, use_bin_type=True, unicode_errors="surrogateescape") for x in items]


def cmd(name):
    def deco(fn):
        HANDLERS[name] = fn
        return fn
    return deco


def _addr(svc, sess=None):
    from .netsetup import advertised_addr
    return advertised_addr(svc, sess)


def _port(svc):
    return int(os.environ.get("PESSERVER_GATE_PORT", "10000"))


# ---- gate ---------------------------------------------------------------------------------------
@cmd("CMD_GET_SVRLIST")
def get_svrlist(svc, sess, req):
    types = [t.strip() for t in os.environ.get("PESSERVER_SVRTYPES", "GATE,ACCOUNT,MENU,VDPCHAT").split(",") if t.strip()]
    lst = [{"svrtype": t, "svraddr": _addr(svc, sess), "svrport": _port(svc), "svrgid": i + 1,
            "max_player_num": 10000, "player_num": len(svc.online)} for i, t in enumerate(types)]
    return {"result": NOERR, "server_num": len(lst), "svrlist": blobs(lst)}


@cmd("CMD_GET_SVRTIME")
def get_svrtime(svc, sess, req):
    return {"result": NOERR, "date": int(time.time())}


@cmd("CMD_GET_SVRVERSION")
def get_svrversion(svc, sess, req):
    # Parser 0x141d4af80 copies EVERY field as a string; the client compares these with the EULA
    # versions it saved on acceptance, so they must be stable strings or it re-prompts forever.
    pv = str(req.get("patch_version", "1.01.00")); dv = str(req.get("dlc_version", "1.00"))
    ev = os.environ.get("PESSERVER_EULA_VERSION", "1")
    return {"result": NOERR, "version": pv, "patch_version": pv, "dlc_version": dv,
            "livedata_version": str(req.get("livedata_version", "0")),
            "eula_version": ev, "eula_myclub_version": ev, "eula_privacy_policy_version": ev,
            "eula_privacy_notice_version": ev, "eula_privacy_policy_california_version": ev}


# URL types the client looks up (table near .rdata 0x142ad3b40 + EULA strings near 0x142819138).
# Every URL type in the client's enum (0x14021ee70, 53 entries). Missing types fail locally with no
# network traffic (FLOW -> "data asset download unsuccessful", GET_GAMERELAY -> ER_MATCHING_RETRY).
URL_TYPES = ("EULA_PRIVACY_POLICY_CALIFORNIA RECV_MYCLUB_PRESENT GET_TURN_SERVER_LIST "
             "GET_GAMERELAY_QUALITYCHECKLIST GET_GAMERELAY PLAYDATA_MATCH WATCHING_GAME WEEKLY_INFO_BANNER "
             "LOCAL_GAMERELAY EULA_STORE EULA_CHANGE_LOG EULA_PRIVACY_NOTICE EULA_PRIVACY_POLICY "
             "MYCLUB_LIVEEVENT_BANNER MYCLUB_WEEKLY_RANKING_BYID MYCLUB_WEEKLY_RANKING STORELINK "
             "KONAMIID_API_JSON KONAMIID_API TRIAL_MATCH_DATA TRIAL_LOGIN TRIAL_SEND_ID TRIAL_WEB_API "
             "TRIAL_URL_LIST ONLINE_AD CLAN_RANKING_BYID CLAN_RANKING ONLINEID STEAM_STORE_STATE "
             "STEAM_STORE_END STEAM_STORE_START AUTH_STEAM AUTH_XSTS DMM_BODY DMM_TOP MYCLUB_AGENT_BANNER "
             "EULA_MYCLUB MYCLUB_RANKING_BYID MYCLUB_RANKING WEB_API DIVISION_RANK_BYID DIVISION_RANK FLOW "
             "TOPINFO AVATAR P2PMATCH RECORD BANNER ADFILE EULA_PES PC_SPEC RANK_BYID RANK").split()


@cmd("CMD_GET_URLLIST")
def get_urllist(svc, sess, req):
    base = f"http://{_addr(svc, sess)}:{os.environ.get('PESSERVER_HTTP_PORT', '8080')}"
    # Short paths keep 53 entries inside one frame (body <= 0x410); the HTTP API routes on the POSTed
    # msgid, not the path, and document pages accept any path.
    ulist = [{"type": t, "url": f"{base}/r" if t == "WATCHING_GAME" else f"{base}/u{i}"} for i, t in enumerate(
             [x for x in os.environ.get("PESSERVER_URLTYPES", ",".join(URL_TYPES)).split(",") if x])]
    # (WATCHING_GAME: the game downloads match replays from <url>/<match_id>/<N>.enc, pesreplay.py)
    return {"result": NOERR, "url_num": len(ulist), "ulist": blobs(ulist)}


# EULA nation selection (OnlineLoginMenuSelectEulaNation). Parser 0x141bd88e0 copies nation_code /
# name / privacy_kind / has_area as STRINGS; nation_list_num is numeric.
NATIONS = [("US", "United States"), ("GB", "United Kingdom"), ("FR", "France"), ("DE", "Germany"),
           ("ES", "Spain"), ("IT", "Italy"), ("JP", "Japan"), ("EG", "Egypt"), ("SA", "Saudi Arabia")]


@cmd("CMD_GET_EULA_NATION_LIST")
def get_eula_nation_list(svc, sess, req):
    lst = [{"nation_code": c, "name": n, "privacy_kind": "PRIVACY_POLICY", "has_area": "NO"} for c, n in NATIONS]
    return {"result": NOERR, "nation_list_num": len(lst), "nation_list": blobs(lst)}


@cmd("CMD_GET_EULA_NATION_CODE")
def get_eula_nation_code(svc, sess, req):
    # parser 0x141bcdc60: both fields copied as strings
    return {"result": NOERR, "nation_code": os.environ.get("PESSERVER_NATION", "US"), "area_code": ""}


@cmd("CMD_DISCONNECT")
def disconnect(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_AUTH_STEAM")
def auth_steam(svc, sess, req):
    # HTTP form: msgid, rqid, urltype=AUTH_STEAM, ticket (may be absent), steamid,
    # package_type. We trust the local client; register the steamid on first sight.
    sid = str(req.get("steamid", ""))
    log.info("AUTH_STEAM steamid=%s package_type=%s ticket=%s", sid, req.get("package_type"),
             "yes" if req.get("ticket") else "none")
    return {"result": NOERR}


@cmd("CMD_SEND_HEARTBEAT")
def heartbeat(svc, sess, req):
    return {"result": NOERR}


# ---- account / login ----------------------------------------------------------------------------
# Flow seen live: HTTP CMD_AUTH_STEAM -> (ACCOUNT svrtype on the gate socket) CMD_GET_CHALLENGECODE ->
# CMD_LOGIN{username, hash, platform, sku, ...} -> CMD_SEND_PSID -> CMD_GET_PLAYER_DATA -> CMD_CREATE_PLAYER.

def _db(svc):
    db = svc.accounts.db
    if not getattr(svc, "_pes_schema", False):
        with svc.accounts.lock, db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS pes_players(
                pid INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT,
                steamid TEXT, psid INTEGER, created REAL NOT NULL, last_login REAL);""")
            cols = {r[1] for r in db.execute("PRAGMA table_info(pes_players)")}
            for col, decl in (("rating", "INTEGER NOT NULL DEFAULT 1000"), ("win_streak", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cols:                     # databases made before ratings existed
                    db.execute(f"ALTER TABLE pes_players ADD COLUMN {col} {decl}")
        svc._pes_schema = True
    return db


def _player(svc, username):
    r = _db(svc).execute("SELECT pid, username, name, steamid FROM pes_players WHERE username=?",
                         (username,)).fetchone()
    return dict(zip(("pid", "username", "name", "steamid"), r)) if r else None


def _ensure_account(svc, username):
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("INSERT OR IGNORE INTO pes_players(username, created) VALUES(?, ?)", (username, time.time()))
        db.execute("UPDATE pes_players SET last_login=? WHERE username=?", (time.time(), username))
    return _player(svc, username)


def delete_player(svc, pid):
    """Remove a registered player (admin page). On their next login the game finds no player data
    (GET_PLAYER_DATA has_data NO) and asks for a region and a name again, as on the first visit.
    Match results stay under the names they had, but lose the pid, so a later player who gets the same
    number (SQLite reuses the highest rowid) doesn't inherit them. Returns the old name, or None."""
    db = _db(svc)
    with svc.accounts.lock, db:
        r = db.execute("SELECT name FROM pes_players WHERE pid=?", (pid,)).fetchone()
        if r is None:
            return None
        db.execute("DELETE FROM pes_players WHERE pid=?", (pid,))
        try:
            for col in ("home_pid", "away_pid"):
                db.execute(f"UPDATE pes_matches SET {col}=NULL WHERE {col}=?", (pid,))
        except sqlite3.OperationalError:                # no matches table yet
            pass
    return r[0] or f"Player {pid}"


@cmd("CMD_GET_CHALLENGECODE")
def get_challengecode(svc, sess, req):
    code = os.urandom(8).hex()
    if sess is not None:
        sess.pes_challenge = code
    return {"result": NOERR, "challenge_code": code}


def account_key(sess, username):
    """Which account a login belongs to. With the mod's install id (gate.py) it is that id: copies of
    the game that don't really run through Steam all send the same Steam id (CPY.ini), so the game's
    username can't tell players apart. Without it (older mods, join-line players): the game's username."""
    mod_id = getattr(sess, "mod_id", None)
    return "m:" + mod_id if mod_id else username


@cmd("CMD_LOGIN")
def login(svc, sess, req):
    game_user = str(req.get("username", ""))
    if not game_user:
        return {"result": "ERR_LOGIN_FAILED"}
    username = account_key(sess, game_user)
    p = _ensure_account(svc, username)
    if username != game_user:                           # keep the game's own id for reference
        with svc.accounts.lock, svc.accounts.db as db:
            db.execute("UPDATE pes_players SET steamid=? WHERE username=?", (game_user, username))
    if sess is not None:
        sess.pes_username = username
        sess.pes_pid = p["pid"]
        svc.online[p["pid"]] = sess
    log.info("LOGIN username=%s%s pid=%s platform=%s sku=%s", game_user,
             f" install={username[2:10]}…" if username != game_user else "", p["pid"], req.get("platform"), req.get("sku"))
    return {"result": NOERR}


@cmd("CMD_SEND_PSID")
def send_psid(svc, sess, req):
    u = getattr(sess, "pes_username", None)
    if u:
        db = _db(svc)
        with svc.accounts.lock, db:
            db.execute("UPDATE pes_players SET psid=? WHERE username=?", (int(req.get("psid_low", 0) or 0), u))
    return {"result": NOERR}


def _pname(p):
    # Nested blob (parser 0x141d3f450): player identity block.
    return msgpack.packb({"name": p["name"] or "", "xuid_one": "", "psn_user_id": msgpack.packb({}), "account_id": p["pid"],
                          "online_id": "", "is_valid_cache": "YES", "is_deleted": "NO"}, use_bin_type=True)


@cmd("CMD_GET_PLAYER_DATA")
def get_player_data(svc, sess, req):
    p = _player(svc, getattr(sess, "pes_username", "") or "")
    if not p or not p["name"]:
        return {"result": NOERR, "has_data": "NO"}
    return {"result": NOERR, "has_data": "YES", "pid": p["pid"], "pname": _pname(p)}


@cmd("CMD_CREATE_PLAYER")
def create_player(svc, sess, req):
    u = getattr(sess, "pes_username", None)
    name = str(req.get("name", "")).strip()
    if not u or not name:
        return {"result": "ERR_INVALIDARG"}
    db = _db(svc)
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_players SET name=? WHERE username=?", (name, u))
    log.info("CREATE_PLAYER username=%s name=%s", u, name)
    return {"result": NOERR}


@cmd("CMD_GET_DLC_FILELIST")
def get_dlc_filelist(svc, sess, req):
    # HTTP web-API to the FLOW URL (LiveDataSetFlow 0x1420ac3f0). Empty lists = nothing to download;
    # dlc_version must equal the client's ("1.00" -> 100, compared in state 4).
    # A Live Update the host put in <data>/liveupdate/ is listed every time (liveupdate.py).
    from . import liveupdate
    # an HTTP command has no gate session: use the connection it came on, or the game gets the public
    # address, which a game on the host's own network can't reach (SGJC_0007, seen live)
    viewer = req.get("_http") or sess
    e = liveupdate.entry(svc, f"http://{_addr(svc, viewer)}:{os.environ.get('PESSERVER_HTTP_PORT', '8080')}")
    if e is not None:
        return {"result": NOERR, "dlc_version": "1.00", "dlcfile_list_count": 1, "dlcfile_list": [e],
                "variation_file_list_count": 0, "variation_file_list": []}
    return {"result": NOERR, "dlc_version": "1.00", "dlcfile_list_count": 0, "dlcfile_list": "",
            "variation_file_list_count": 0, "variation_file_list": ""}


# Real team ids from Team.bin (internal DB ids). team_class drives class-based matching only.
TEAM_LIST = [(108, "TEAM_A"), (120, "TEAM_A"), (100, "TEAM_A"), (127, "TEAM_A"), (114, "TEAM_A"),
             (101, "TEAM_B"), (116, "TEAM_B"), (131, "TEAM_C")]


@cmd("CMD_GET_GAME_TEAM_LIST")
def get_game_team_list(svc, sess, req):
    lst = [msgpack.packb({"team_id": t, "team_class": c, "division_point": 0}, use_bin_type=True) for t, c in TEAM_LIST]
    return {"result": NOERR, "team_list_num": len(lst), "team_list": lst}


@cmd("CMD_GET_QUICKMATCHOPTION")
def get_quickmatchoption(svc, sess, req):
    return {"result": NOERR, "is_league": "NO", "is_pass_level": "NO", "is_team_power_lv": "NO",
            "ping_level": "PING_LEVEL_BALANCE", "rating_level": "RATING_LEVEL_NONE", "team_class": "TEAM_NONE"}


@cmd("CMD_GET_SERVER_ENV")
def get_server_env(svc, sess, req):
    # Match timeouts are copied into ParameterEnvironment (0x141210dd0) and used as frame limits
    # (x frame rate). 0 = instant LOAD_TIMEOUT (QVHE1459) and a crash in match::Online teardown.
    # Values = the client's own defaults (result ctor 0x141e45ee0).
    return {"result": NOERR, "match_client_timeout_load_timeout": 180, "match_client_timeout_long_outofplay": 120,
            "match_client_timeout_init_not_end": 240, "match_client_timeout_cmd_lack": 180,
            "trigger_keep_alive": 1, "quality_check_wait_sec": 3600,
            "teamlobby_game_rating_border_normal": 600, "teamlobby_game_rating_border_hard": 700,
            # matchrejectpid_max: size of the Match Block List; 0 makes "Add to Match Block List" always say the
            # list is full (static 0x140bdcd75); the game's own default is 400
            "matchrejectpid_max": 400, "is_revision_check": "YES",
            "max_division": "D10"}           # Online Divisions: D10 (bottom) .. D1 (pesdivisions.py)


# ---- MENU server -----------------------------------------------------------------------------------
def _session_key(sess):
    """A new session crypto key for the reply (installed via 0x141233ee0: 32 bytes -> AES enhanced tier,
    else Blowfish easy key). Keep it 16 bytes: 32 would switch the whole game to AES until restart. The
    client's next frame is encrypted with it; the codec learns the exact derivation by trying candidates
    (see NclCodec.offer_keys)."""
    import base64
    raw = os.urandom(16)
    key = base64.b64encode(raw).decode()
    codec = getattr(sess, "codec", None)
    if codec is not None and hasattr(codec, "offer_keys"):
        a = key.encode()
        codec.offer_keys([("base64-decoded", raw), ("ascii", a), ("ascii[:16]", a[:16]),
                          ("ascii+NUL", a + b"\0")])
    return {"key": key, "key_size": len(raw)}


@cmd("CMD_SET_CURRENTPLAYER")
def set_currentplayer(svc, sess, req):
    k = _session_key(sess)
    log.info("SET_CURRENTPLAYER -> session key %s", k["key"])
    return {"result": NOERR, **k,
            "nation_code_server": os.environ.get("PESSERVER_NATION", "US"), "chat_probi": "NO",
            "is_select_code_changed": "NO", "subscription_active": "NO", "subscription_expire_date": 0}


@cmd("CMD_RECONNECT")
def reconnect(svc, sess, req):
    # OnlineSystemClientReconnect.cpp (task 0x1411e1e70): after the gate connection drops, the game
    # fetches the server list, opens a new connection and sends {pid, platform, patch_version,
    # dlc_version, online_mode} without logging in again. The reply carries a new session key, like
    # SET_CURRENTPLAYER. The new connection takes over the player's place (rooms and pushes go by pid).
    try:
        pid = int(req.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    r = _db(svc).execute("SELECT username FROM pes_players WHERE pid=?", (pid,)).fetchone() if pid else None
    if r is None or sess is None:
        log.warning("RECONNECT refused: unknown pid %r", req.get("pid"))
        return {"result": "ERR_LOGIN_FAILED"}
    old = svc.online.get(pid)
    sess.pes_username, sess.pes_pid = r[0], pid
    for attr in ("pes_versions", "pes_sums", "pes_match_req"):
        if old is not None and hasattr(old, attr) and not hasattr(sess, attr):
            setattr(sess, attr, getattr(old, attr))
    svc.online[pid] = sess
    mm = getattr(svc, "pes_mm", None)
    if mm is not None:
        mm["queue"] = [(p, sess if p == pid else s) for p, s in mm["queue"]]
    k = _session_key(sess)
    log.info("RECONNECT pid %s (%s) -> session key %s", pid, r[0], k["key"])
    return {"result": NOERR, **k}


# ---- schema-driven defaults + validation ----------------------------------------------------------
# schemas.json is generated by notes/re/extract_schemas.py from the client's own response parsers.
_SCHEMAS = None


def schemas():
    global _SCHEMAS
    if _SCHEMAS is None:
        import json
        p = os.path.join(os.path.dirname(__file__), "schemas.json")
        try:
            _SCHEMAS = json.load(open(p))
        except OSError:
            _SCHEMAS = {}
    return _SCHEMAS


_DEFAULTS = {"STR": "", "NUM": 0, "LIST": [], "OBJ?": [], "?": None}


def default_response(msgid):
    out = {"result": NOERR}
    for k, t in schemas().get(msgid, {}).get("fields", {}).items():
        if k in ("msgid", "rqid", "result") or t == "?":
            continue
        out[k] = msgpack.packb({}) if t == "OBJ" else _DEFAULTS[t]
    return out


# Where the extracted schema is wrong, checked against the running game (notes/re/functions/results.md).
SCHEMA_FIXES = {
    "CMD_GET_RANKING": {"rank_list": "LIST"},          # inline maps over HTTP; the list showed live 2026-11-07
    "CMD_GET_RANKING_BYID": {"rank_list": "LIST"},
    "CMD_GET_DIVISION_RANKING": {"rank_list": "LIST"},
    "CMD_GET_DIVISION_RANKING_BYID": {"rank_list": "LIST"},
}


def validate(msgid, resp):
    """Warn about values whose msgpack type disagrees with how the client parser reads them."""
    fields = {**schemas().get(msgid, {}).get("fields", {}), **SCHEMA_FIXES.get(msgid, {})}
    bad = []
    for k, v in resp.items():
        t = fields.get(k)
        if t == "STR" and not isinstance(v, (str, bytes)):
            bad.append(f"{k}: expected STR got {type(v).__name__}")
        elif t == "NUM" and (isinstance(v, bool) or not isinstance(v, (int, float))) and not (
                isinstance(v, list) and all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
            # (the schema extractor types an int vector as NUM, e.g. sp_member_list, add_limit_rating)
            bad.append(f"{k}: expected NUM got {type(v).__name__}")
        elif t == "LIST" and not isinstance(v, list):
            bad.append(f"{k}: expected LIST got {type(v).__name__}")
        elif t == "OBJ" and not isinstance(v, (bytes, bytearray)):
            bad.append(f"{k}: expected OBJ blob got {type(v).__name__}")
    if bad:
        log.error("SCHEMA MISMATCH %s: %s", msgid, "; ".join(bad))
    return not bad


def dispatch(svc, sess, msgid, req):
    resp = _dispatch(svc, sess, msgid, req)
    try:
        from . import pesreplay
        pesreplay.observe(svc, sess, msgid, req)
    except Exception:  # noqa: BLE001 - replays must never break the game
        log.exception("pesreplay.observe %s", msgid)
    try:
        from . import events
        events.observe(svc, sess, msgid, req)
    except Exception:  # noqa: BLE001 - the admin feed must never break the game
        log.exception("events.observe %s", msgid)
    return resp


def _unjson(v):
    """{"__blob__": obj} -> one msgpack blob; {"__blobs__": [objs]} -> list of blobs (dev overrides)."""
    if isinstance(v, dict):
        if "__blob__" in v:
            return msgpack.packb(_unjson(v["__blob__"]), use_bin_type=True)
        if "__blobs__" in v:
            return blobs([_unjson(x) for x in v["__blobs__"]])
        return {k: _unjson(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_unjson(x) for x in v]
    return v


def _override(svc, msgid):
    """Development hook: <data>/overrides.json maps a command name to the reply to send instead.
    Read on every request, so reply shapes can be tried against the real game without a restart."""
    import json
    path = os.path.join(getattr(svc, "data_dir", "") or ".", "overrides.json")
    try:
        with open(path) as f:
            table = json.load(f)
    except (OSError, ValueError):
        return None
    if msgid not in table:
        return None
    resp = {"result": NOERR, **_unjson(table[msgid])}
    log.info("OVERRIDE %s -> %s", msgid, sorted(resp))
    return resp


def _dispatch(svc, sess, msgid, req):
    over = _override(svc, msgid)
    if over is not None:
        return over
    fn = HANDLERS.get(msgid)
    if fn is None:
        # Minimal reply on purpose: client parsers only overwrite keys that are present, so absent keys
        # keep the client's built-in defaults. Zero-filling (default_response) can be harmful, e.g.
        # GET_SERVER_ENV timeouts of 0 aborted every match load. default_response() stays available.
        resp = {"result": NOERR} if os.environ.get("PESSERVER_ZERO_DEFAULTS") != "1" else default_response(msgid)
        log.warning("UNHANDLED %s %s -> %s", msgid,
                    {k: v for k, v in req.items() if not k.startswith("_")}, list(resp))
        return resp
    resp = fn(svc, sess, req)
    if not resp.get("_defer"):
        validate(msgid, resp)
    return resp
