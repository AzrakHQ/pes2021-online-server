"""Test-only add-on: change any reply of the REAL server from a control file, without restarts, and log every reply.

Loaded by run_server.py when PATCH_CTL is set (scenario.py and probesession.py set it). The file is read again
whenever it changes. Format (every key optional):

  {"patch": {"CMD_X": {"set": {"a.b.c": VALUE}, "del": ["a.b"]}},   edit fields of the handler's reply; a path
                                                                     goes through nested blobs (bytes are
                                                                     unpacked, edited, packed again) and list
                                                                     indexes ("list.0.field")
   "err":   {"CMD_X": "ERR_Y"},                                      reply {"result": "ERR_Y"} instead
   "push":  [{"id": "p1", "to": PID or "all", "msgid": "CMD_Y", "body": {...}}]}   sent once per id

VALUE / body use the server's override notation: {"__blob__": obj} packs obj as one blob, {"__blobs__": [...]}
a list of blobs, {"__bin__": "hex"} raw bytes. Patches only apply to replies, not to pushes the server makes
itself. Every reply (after patching) is appended to PATCH_TXLOG (default: next to PATCH_CTL, replies.jsonl) as
{"t", "msgid", "pid", "rqid", "patched", "body_hex"} — body_hex is the msgpack map the game receives, which the
parser bench feeds to the game's own reader. Log prefix "PATCH".
"""
import json, logging, os, threading, time
import msgpack
from pesserver import pescmds, pesmatch

log = logging.getLogger("pesserver.replypatch")
CTL = os.environ["PATCH_CTL"]
TXLOG = os.environ.get("PATCH_TXLOG") or os.path.join(os.path.dirname(os.path.abspath(CTL)), "replies.jsonl")
_cache = {"mtime": None, "data": {}}
_sent = set()
_lock = threading.Lock()


def ctl():
    try:
        m = os.path.getmtime(CTL)
    except OSError:
        return {}
    if m != _cache["mtime"]:
        try:
            _cache["data"] = json.load(open(CTL))
            _cache["mtime"] = m
            log.info("PATCH ctl reloaded: %s", sorted(_cache["data"]))
        except ValueError as e:          # half-written file: keep the last good one
            log.warning("PATCH ctl unreadable (%s)", e)
    return _cache["data"]


def value(v):
    if isinstance(v, dict) and set(v) == {"__bin__"}:
        return bytes.fromhex(v["__bin__"])
    return pescmds._unjson(v)


def _pack(o):
    return msgpack.packb(o, use_bin_type=True, unicode_errors="surrogateescape")


def _unpack(b):
    return msgpack.unpackb(b, raw=False, strict_map_key=False, unicode_errors="surrogateescape")


def _key(node, k):
    if isinstance(node, list):
        return int(k)
    return k


def edit(obj, path, op, v=None):
    """Set or delete obj[path] where path is 'a.b.0.c'; returns the new obj (blobs on the way are re-packed)."""
    head, _, rest = path.partition(".")
    if isinstance(obj, (bytes, bytearray)):
        return _pack(edit(_unpack(obj), path, op, v))
    k = _key(obj, head)
    if not rest:
        if op == "set":
            if isinstance(obj, list) and k == len(obj):
                obj.append(v)
            else:
                obj[k] = v
        elif isinstance(obj, dict):
            obj.pop(k, None)
        elif 0 <= k < len(obj):
            del obj[k]
        return obj
    if isinstance(obj, dict) and k not in obj:
        obj[k] = {}
    obj[k] = edit(obj[k], rest, op, v)
    return obj


def _jsonable(o):
    if isinstance(o, (bytes, bytearray)):
        return {"__bin__": bytes(o).hex()}
    if isinstance(o, dict):
        return {str(k): _jsonable(x) for k, x in o.items()}
    if isinstance(o, list):
        return [_jsonable(x) for x in o]
    return o


_dispatch = pescmds.dispatch


def dispatch(svc, sess, msgid, req):
    c = ctl()
    err = c.get("err", {}).get(msgid)
    if err:
        resp, patched = {"result": err}, ["err"]
    else:
        resp, patched = _dispatch(svc, sess, msgid, req), []
        p = c.get("patch", {}).get(msgid)
        if p and isinstance(resp, dict) and not resp.get("_defer"):
            for path, v in p.get("set", {}).items():
                resp = edit(resp, path, "set", value(v)); patched.append("set " + path)
            for path in p.get("del", []):
                resp = edit(resp, path, "del"); patched.append("del " + path)
    if patched:
        log.info("PATCH %s %s", msgid, patched)
    try:
        body = {k: x for k, x in resp.items() if not str(k).startswith("_")} if isinstance(resp, dict) else resp
        rec = {"t": time.time(), "msgid": msgid, "pid": pesmatch._pid(sess) if sess else None,
               "rqid": req.get("rqid") if isinstance(req, dict) else None, "patched": patched,
               "body_hex": _pack({**body, **({"rqid": req["rqid"]} if isinstance(req, dict) and "rqid" in req else {})}).hex()}
        with _lock, open(TXLOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break a reply
        log.exception("PATCH txlog %s", msgid)
    return resp


pescmds.dispatch = dispatch


def _tick(svc):
    for p in ctl().get("push", []):
        pid_key = p.get("id") or json.dumps(p, sort_keys=True)
        if pid_key in _sent:
            continue
        targets = list(svc.online) if p.get("to", "all") == "all" else [int(p["to"])]
        if not targets:
            continue
        _sent.add(pid_key)
        for pid in targets:
            s = svc.online.get(pid)
            if s is not None:
                pesmatch._send_later(s, p["msgid"], 0, 0x60, 1, {"result": pescmds.NOERR, **value(p.get("body", {}))})
        log.info("PATCH push %s %s -> %s", pid_key, p["msgid"], targets)


def start_ticker(svc):
    """Called with the Service once the server runs (run_server.py wires it via a GET_SERVER_ENV hook)."""
    import asyncio
    loop = asyncio.get_running_loop()

    def tick():
        try:
            _tick(svc)
        except Exception:  # noqa: BLE001
            log.exception("PATCH tick")
        loop.call_later(1.0, tick)
    loop.call_later(1.0, tick)


_started = []
_orig_dispatch_for_tick = pescmds.dispatch


def _first(svc, sess, msgid, req):
    if not _started and svc is not None and hasattr(svc, "online"):
        try:
            start_ticker(svc); _started.append(1)
            log.info("PATCH ticker on (ctl %s, replies %s)", CTL, TXLOG)
        except RuntimeError:
            pass
    return _orig_dispatch_for_tick(svc, sess, msgid, req)


pescmds.dispatch = _first
