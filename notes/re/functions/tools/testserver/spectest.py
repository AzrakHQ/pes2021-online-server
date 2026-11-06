"""Test-only add-on: spectating (record a match, replay it on one copy). Plan: notes/re/functions/spectate2.md.

Recording: GET_MATCHID upload "YES"; START_SEND_GAME_SEQUENCE_DATA / SEND_GAME_SEQUENCE_DATA from the players.
  The first pid that sends START_SEND for a match is its uploader; the other stream is counted and dropped.
  Each record is base64(256-byte buffer); data[:data_len] is already a viewer item. Every SEND becomes one chunk
  N.enc = IV + AES-256-CBC(key, PKCS7(0x00 + items)); closing adds `03 00` (if no type-3 item came) + END_OF_DATA.
Viewer: PES League lobby replies (Online Tournament -> competition -> team -> lobby -> watching list), then the
  game GETs <WATCHING_GAME url>/<id>/<N>.enc, served here on SPEC_PORT (GET_URLLIST points WATCHING_GAME at it).
Files: recordings/<match_id>/N.enc + meta.json next to this file.
"""
import base64, http.server, json, logging, os, socketserver, threading, time
import msgpack
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pesserver import pescmds, pesmatch
from pesserver.pescmds import NOERR

log = logging.getLogger("pesserver.spectest")
HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(HERE, "recordings")
PORT = int(os.environ.get("SPEC_PORT", "8091"))
HOST = os.environ.get("SPEC_HOST", "192.168.1.100")
KEY = bytes.fromhex("35a110675829e36845dc775162cca1bb69b786ed1e73d82ea9fe7e6176743881")   # 0x142897bd0
S = {}            # match_id -> {"uploader", "n", "type3", "closed", "dropped", "pids", "t0"}


def enc(plain):
    p = padding.PKCS7(128).padder(); data = p.update(plain) + p.finalize()
    iv = os.urandom(16)
    e = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    return iv + e.update(data) + e.finalize()


def dec(blob):
    d = Cipher(algorithms.AES(KEY), modes.CBC(blob[:16])).decryptor()
    data = d.update(blob[16:]) + d.finalize()
    u = padding.PKCS7(128).unpadder(); return u.update(data) + u.finalize()


def h(name):
    def deco(fn):
        orig = pescmds.HANDLERS.get(name)

        def w(svc, sess, req):
            r = fn(svc, sess, req, orig)
            if name not in ("CMD_SEND_GAME_SEQUENCE_DATA",):
                log.info("SPEC %s pid=%s req=%s -> %s", name, pesmatch._pid(sess) if sess else None,
                         {k: v for k, v in req.items() if not k.startswith("_") and k not in ("msgid", "svrtype", "client")},
                         {k: (v if not isinstance(v, (list, bytes)) else f"<{type(v).__name__} {len(v)}>") for k, v in r.items()})
            return r
        pescmds.HANDLERS[name] = w
        return fn
    return deco


def _room_mid(svc, pid):
    rid, room = pesmatch._room_of(svc, pid)
    return (room or {}).get("match_id")


@h("CMD_GET_MATCHID")
def get_matchid(svc, sess, req, orig):
    r = orig(svc, sess, req)
    if r.get("match_id"):
        r = {**r, "upload": "YES"}
    return r


@h("CMD_START_SEND_GAME_SEQUENCE_DATA")
def start_send(svc, sess, req, orig):
    pid = pesmatch._pid(sess); mid = _room_mid(svc, pid) or f"nomatch{int(time.time())}"
    st = S.get(mid)
    if st is None:
        os.makedirs(os.path.join(REC, mid), exist_ok=True)
        st = S[mid] = {"uploader": pid, "n": 0, "type3": False, "closed": False, "dropped": 0, "t0": time.time(),
                       "pids": list((pesmatch._room_of(svc, pid)[1] or {}).get("members", [pid]))}
        log.info("SPEC recording %s: uploader pid %s", mid, pid)
    return {"result": NOERR}


def _write_chunk(mid, items, final=False):
    st = S[mid]
    st["n"] += 1
    plain = b"\x00" + items
    if final:
        plain += (b"" if st["type3"] else b"\x03\x00") + b"END_OF_DATA"
    blob = enc(plain)
    open(os.path.join(REC, mid, f"{st['n']}.enc"), "wb").write(blob)
    assert dec(blob) == plain
    json.dump({"match_id": mid, **{k: v for k, v in st.items()}}, open(os.path.join(REC, mid, "meta.json"), "w"))
    log.info("SPEC %s chunk %d: %d item bytes%s", mid, st["n"], len(items), " (final)" if final else "")


def close(mid, why):
    st = S.get(mid)
    if st and not st["closed"]:
        st["closed"] = True
        _write_chunk(mid, b"", final=True)
        log.info("SPEC %s closed (%s): %d chunks, %d foreign SENDs dropped", mid, why, st["n"], st["dropped"])


@h("CMD_SEND_GAME_SEQUENCE_DATA")
def send_seq(svc, sess, req, orig):
    pid = pesmatch._pid(sess)
    mid = str(req.get("match_id") or "") or _room_mid(svc, pid)
    st = S.get(mid)
    recs = req.get("game_seq_data") or []
    if st is None or st["closed"]:
        return {"result": NOERR}
    if pid != st["uploader"]:
        st["dropped"] += 1
        return {"result": NOERR}
    items, kinds = b"", []
    for b in recs:
        try:
            raw = b.encode("utf-8", "surrogateescape") if isinstance(b, str) else bytes(b)
            r = msgpack.unpackb(raw, raw=False, unicode_errors="surrogateescape")
            data = base64.b64decode(r.get("data", ""))[: int(r.get("data_len", 0))]
        except Exception as e:  # noqa: BLE001
            log.info("SPEC bad record: %s", e); continue
        kinds.append(r.get("type"))
        if r.get("type") == 3:
            st["type3"] = True
        items += data
    if st["n"] == 0:
        log.info("SPEC first SEND from pid %s: %d records, types %s, first bytes %s", pid, len(recs),
                 sorted(set(kinds)), items[:16].hex())
    _write_chunk(mid, items)
    if st["type3"]:
        close(mid, "type-3 item")
    return {"result": NOERR}


for _name, _why in (("CMD_LEAVE_GAME", "LEAVE_GAME"), ("CMD_SEND_ABNORMALEND", "abnormal end")):
    def _mk(why):
        def f(svc, sess, req, orig):
            mid = _room_mid(svc, pesmatch._pid(sess))
            if mid in S and S[mid]["uploader"] == pesmatch._pid(sess):
                close(mid, why)
            return orig(svc, sess, req) if orig else {"result": NOERR}
        return f
    h(_name)(_mk(_why))


@h("CMD_CHANGE_GAMEPHASE")
def change_phase(svc, sess, req, orig):
    r = orig(svc, sess, req)
    if req.get("phase") == "END":
        mid = _room_mid(svc, pesmatch._pid(sess))
        if mid in S and S[mid]["uploader"] == pesmatch._pid(sess):
            threading.Timer(8.0, close, (mid, "END phase")).start()
    return r


# ---- URL list: WATCHING_GAME -> this add-on's HTTP server -----------------------------------------------
@h("CMD_GET_URLLIST")
def urllist(svc, sess, req, orig):
    r = orig(svc, sess, req)
    out = []
    for b in r.get("ulist", []):
        e = msgpack.unpackb(b, raw=False)
        if e.get("type") == "WATCHING_GAME":
            e["url"] = f"http://{HOST}:{PORT}/w"
        out.append(msgpack.packb(e, use_bin_type=True))
    return {**r, "ulist": out}


def _latest():
    ms = [m for m in os.listdir(REC) if os.path.isdir(os.path.join(REC, m))] if os.path.isdir(REC) else []
    return max(ms, key=lambda m: os.path.getmtime(os.path.join(REC, m)), default=None)


class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        mid, name = (parts[-2], parts[-1]) if len(parts) >= 2 else ("0", parts[-1])
        d = os.path.join(REC, mid)
        if not os.path.isdir(d):
            d = os.path.join(REC, _latest() or "none")
        p = os.path.join(d, os.path.basename(name))
        if os.path.isfile(p):
            b = open(p, "rb").read()
            self.send_response(200); self.send_header("Content-Length", str(len(b)))
            self.send_header("Content-Type", "application/octet-stream"); self.end_headers(); self.wfile.write(b)
            log.info("SPEC http %s -> 200 %dB (%s)", self.path, len(b), os.path.basename(d))
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
            log.info("SPEC http %s -> 404", self.path)

    def log_message(self, *a):
        pass


def _serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), _H) as s:
        s.serve_forever()


threading.Thread(target=_serve, daemon=True).start()

# ---- PES League lobby path to the watching list (peslg_path.md) --------------------------------------------
NOW = int(time.time())
COMPE = {"compe_id": 1, "compe_db_id": 1, "compe_name": "Friends Replays", "is_myclub": "NO",
         "match_type": "PES_LEAGUE_MATCH_1VS1_LOBBY", "start_date": NOW - 86400, "end_date": NOW + 30 * 86400,
         "match_end_date": NOW + 30 * 86400, "entry_limit_date": NOW + 30 * 86400, "entry_user_limit": "NO",
         "entry_user_max": 1000, "entry_user_num": 1, "entry_limit_division": "NO", "entry_limit_division_min": 1,
         "entry_limit_division_max": 10, "is_end": "NO", "is_entry": "NO", "is_gm_account": "NO", "is_no_check": "NO",
         "mode": "PESLEAGUE"}
TEAMS = [45, 8, 14, 50]


def _ok(**kw):
    return lambda svc, sess, req, orig: {"result": NOERR, **kw}


h("CMD_JOIN_PESLEAGUE_COMPE_MODE")(_ok())
h("CMD_GET_PESLEAGUE_MAINMENU_INFO")(_ok(division="D1", new_result="NO"))
h("CMD_GET_PESLEAGUE_COMPE_LIST")(_ok(compe_list=[msgpack.packb({**COMPE, "banner_file_info": msgpack.packb({}),
                                                                   "list_banner_file_info": msgpack.packb({})},
                                                                  use_bin_type=True)],
                                      list_num=1, list_max=1, is_end="YES"))
h("CMD_GET_PESLEAGUE_COMPE_INFO")(_ok(**COMPE, eula_url="", entry_compe="NO",
    compe_data=msgpack.packb({**COMPE, "eula_url": "", "entry_compe": "NO"}, use_bin_type=True)))
h("CMD_GET_PESLEAGUE_COMPE_TEAM_LIST")(_ok(team_list=TEAMS, team_list_num=len(TEAMS)))
h("CMD_JOIN_PESLEAGUE_COMPE")(_ok())
h("CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO")(_ok(compe_end_time=NOW + 30 * 86400, draw_count=0, lose_count=0, match_num=0,
                                              matching_first_date=NOW, matching_interval=0, matching_time=0, point=0,
                                              rank_type="NONE", regulation_match_num=0, team_id=45, team_name="BRAZIL",
                                              win_count=0))
h("CMD_SEND_WATCHING_RESULT")(_ok())


def _pname(name, pid):
    return msgpack.packb({"name": name, "xuid_one": "", "psn_user_id": msgpack.packb(
        {"account_id": pid, "online_id": "", "is_valid_cache": "YES", "is_deleted": "NO"}, use_bin_type=True)},
        use_bin_type=True)


@h("CMD_GET_WATCHING_MATCH_LIST")
def watching_list(svc, sess, req, orig):
    entries = []
    for mid in sorted(os.listdir(REC) if os.path.isdir(REC) else [], key=lambda m: -os.path.getmtime(os.path.join(REC, m))):
        try:
            meta = json.load(open(os.path.join(REC, mid, "meta.json")))
        except Exception:  # noqa: BLE001
            continue
        pids = (meta.get("pids") or [0, 0]) + [0, 0]
        score = msgpack.packb({"score_1st": 0, "score_2nd": 0, "score_ex1st": 0, "score_ex2nd": 0, "score_pk": 0},
                              use_bin_type=True)
        players = [msgpack.packb({"pid": p, "pname": _pname(f"Player{p}", p), "side": side, "is_com": "NO",
                                  "cpu_level": "NORMAL", "is_follow": "NO", "is_guest": "NO"}, use_bin_type=True)
                   for p, side in ((pids[0], "HOME"), (pids[1], "AWAY"))]
        gr = msgpack.packb({"date": int(meta.get("t0", NOW)), "ex_type": "", "pk_flag": "NO", "no_match": "NO",
                            "player": players, "team": [45, 8], "score": [score, score],
                            "match_category": "PESLEAGUE_LOBBY", "gameend_reason": "NORMAL_END"}, use_bin_type=True)
        entries.append(msgpack.packb({"match_id": mid, "game_phase": "2ND", "game_result": gr}, use_bin_type=True))
        if len(entries) >= 5:
            break
    return {"result": NOERR, "match_list": entries, "match_list_num": len(entries), "total_match_num": len(entries)}


log.info("SPEC test handlers installed (replay server on %d)", PORT)


# ---- ctl.json next to this file: {"server_env": {...}} merged into GET_SERVER_ENV (e.g. is_revision_check) ---
def _ctl():
    try:
        return json.load(open(os.path.join(HERE, "ctl.json")))
    except Exception:  # noqa: BLE001
        return {}


@h("CMD_GET_SERVER_ENV")
def server_env(svc, sess, req, orig):
    r = orig(svc, sess, req)
    extra = _ctl().get("server_env")
    return {**r, **extra} if extra else r


@h("CMD_LOGIN")
def login(svc, sess, req, orig):
    e = _ctl().get("login_err")
    if e:
        log.info("SPEC LOGIN forced -> %s", e)
        return {"result": e}
    return orig(svc, sess, req)
