"""Test-only add-on: serve a Live Update CPK from www/ through CMD_GET_DLC_FILELIST.
The listing is switched at runtime by ctl.json next to this file: {"livedata": "livedata_101.cpk"} lists it,
anything else lists nothing. That lets one game copy take the update while the other boots without it.
Files are served by a small HTTP server on LIVE_PORT (default 8090)."""
import hashlib, http.server, json, logging, os, socketserver, threading
from pesserver import pescmds
from pesserver.pescmds import NOERR

log = logging.getLogger("pesserver.livedata")
HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, "www")
PORT = int(os.environ.get("LIVE_PORT", "8090"))
HOST = os.environ.get("LIVE_HOST", "192.168.1.100")


def ctl():
    try:
        return json.load(open(os.path.join(HERE, "ctl.json")))
    except Exception:  # noqa: BLE001
        return {}


_orig = pescmds.HANDLERS["CMD_GET_DLC_FILELIST"]


def filelist(svc, sess, req):
    name = ctl().get("livedata")
    p = os.path.join(WWW, name) if name else None
    if not p or not os.path.exists(p):
        log.info("LIVEDATA list: none")
        return _orig(svc, sess, req)
    b = open(p, "rb").read()
    ver = str(ctl().get("dlc_version", "101"))
    e = {"name": name, "size": len(b), "url": f"http://{HOST}:{PORT}/{name}",
         "hash": hashlib.sha256(b).hexdigest(), "dlc_version": ver}
    log.info("LIVEDATA list: %s", e)
    return {"result": NOERR, "dlc_version": "1.00", "dlcfile_list_count": 1, "dlcfile_list": [e],
            "variation_file_list_count": 0, "variation_file_list": []}


pescmds.HANDLERS["CMD_GET_DLC_FILELIST"] = filelist


class _H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WWW, **k)

    def log_message(self, fmt, *a):
        log.info("LIVEDATA http %s " + fmt, self.client_address[0], *a)


def _serve():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), _H) as s:
        s.serve_forever()


threading.Thread(target=_serve, daemon=True).start()
