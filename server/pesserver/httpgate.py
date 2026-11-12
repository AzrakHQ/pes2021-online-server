"""HTTP side of the server: the game's web API (form-urlencoded POST with msgid=CMD_… → raw msgpack
reply), the NTL GateInfo.php text reply used by the network test, and /ca.crt for players.
Every request is logged in full (method, path, headers, body)."""
import asyncio, sys, json, logging, os, sqlite3

log = logging.getLogger("pesserver.http")


def make_handler(service, stun_host, stun_port, gate_port):
    async def on_conn(reader, writer):
        served = 0                                  # requests answered on this connection
        try:
            while True:
                try:
                    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), KEEP_IDLE if served else 10)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                    if served:
                        return                      # the browser let go of a kept-open admin connection
                    raise
                lines = head.decode("latin-1").split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                headers = {k.lower(): v for k, v in (l.split(": ", 1) for l in lines[1:] if ": " in l)}
                body = b""
                if int(headers.get("content-length", 0)):
                    body = await reader.readexactly(min(int(headers["content-length"]), 1 << 16))
                route = path.split("?", 1)[0].rstrip("/").lower()
                log.log(logging.DEBUG if method == "GET" and route in ADMIN_POLLS else logging.INFO,
                        "HTTP %s %s hdr=%s body=%r", method, path,
                        {k: ("<hidden>" if k == "authorization" else v) for k, v in headers.items()},
                        b"<hidden>" if path.startswith("/admin") else body[:300])
                status = "200 OK"
                extra = b""
                cache, keep = b"no-store", False
                send_file = None                    # a big file sent piece by piece instead of from memory
                if route == "/admin" or route.startswith("/admin/api/"):
                    peer = (writer.get_extra_info("peername") or ("",))[0]
                    secure = writer.get_extra_info("ssl_object") is not None
                    status, out, ctype = await _admin(service, method, route, headers, body, peer, secure, path)
                    extra_dl = b""
                    if isinstance(out, tuple):          # ("file", path, size): a backup download, sent in pieces
                        send_file, out = out[1:], b""
                        extra_dl = b'Content-Disposition: attachment; filename="%s"\r\n' % os.path.basename(send_file[0]).encode()
                    cache, out, extra_enc = _admin_cache(route, method, status, out, ctype, headers)
                    if cache == b"not-modified":
                        status, out, cache = "304 Not Modified", b"", b"no-cache"
                    keep = (headers.get("connection", "").lower() == "keep-alive" and lines[0].endswith("HTTP/1.1")
                            and int(headers.get("content-length", 0) or 0) <= 1 << 16 and "transfer-encoding" not in headers
                            and served < KEEP_MAX)            # (only when the whole request was read: the next starts clean)
                    extra = (b"X-Frame-Options: DENY\r\nContent-Security-Policy: " + admin_csp().encode() + b"\r\n"
                             b"Referrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n"
                             b"Cross-Origin-Resource-Policy: same-origin\r\n"
                             b"Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()\r\n" + extra_enc + extra_dl)
                elif method == "GET" and route in ("", "/join", "/leave", "/join.sh", "/join.ps1", "/leave.sh",
                                                 "/leave.ps1", "/install-ca-proton.py", "/banner"):
                    out, ctype = _web(service, route, headers, writer)
                    if ctype.startswith("text/html"):       # the friends' page shares an origin with the admin page
                        extra = (b"Content-Security-Policy: " + page_csp(out.decode()).encode() + b"\r\n"
                                 b"X-Frame-Options: DENY\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n")
                        out, enc = gzipped(out, ctype, headers)     # (setup scripts stay plain for curl/PowerShell)
                        extra += enc
                elif method in ("GET", "HEAD") and route in ("/mod/note", "/mod/hello", "/mod/dinput8.dll"):
                    status, out, ctype, extra = _mod(service, route, headers, writer, path)
                    if method == "GET" and route == "/mod/dinput8.dll" and status.startswith("200") and "from=admin" not in path:
                        note_download(service, (writer.get_extra_info("peername") or ("",))[0])
                elif method in ("GET", "HEAD") and route.startswith("/r/"):
                    from . import pesreplay                     # match replays: /r/<match_id>/<N>.enc
                    parts = route.split("/")
                    f = pesreplay.chunk_file(service, parts[2], parts[3]) if len(parts) == 4 else None
                    try:
                        if f is None:
                            raise OSError("no such chunk")
                        with open(f, "rb") as fh:
                            out = fh.read()
                        ctype = "application/octet-stream"
                    except OSError:                              # (also: pruned between the check and the read)
                        status, out, ctype = "404 Not Found", b"not found", "text/plain"
                elif method in ("GET", "HEAD") and route.startswith("/liveupdate/"):
                    from . import liveupdate
                    f = liveupdate.file_for(service, route[len("/liveupdate/"):])
                    if f is None:
                        status, out, ctype = "404 Not Found", b"not found", "text/plain"
                    else:
                        try:                            # (hundreds of MB: never read whole, never on the loop)
                            send_file, out = (f, os.path.getsize(f)), b""
                        except OSError:
                            status, out, ctype = "404 Not Found", b"not found", "text/plain"
                    if send_file:
                        ctype = "application/octet-stream"
                        offered = liveupdate.safe_name(os.path.basename(f))
                        log.info("Live Update %s sent to %s", offered, (writer.get_extra_info("peername") or ("",))[0])
                        if method == "GET":
                            from . import adminextra
                            try:
                                adminextra.note_liveupdate(service, (writer.get_extra_info("peername") or ("",))[0], offered)
                            except Exception:  # noqa: BLE001
                                log.exception("noting a Live Update download")
                elif path.startswith("/status"):
                    from . import pesmatch
                    from . import identity
                    k = identity.public_hex()
                    out = json.dumps({"id": server_id(), "key": k[:8] + " " + k[8:16],
                                      "mod_sha256": mod_sha256(mod_url(service, headers, writer)), "online": len(service.online),
                                      "queued": len(pesmatch._state(service)["queue"]),
                                      "rooms": len(pesmatch._state(service)["rooms"])}).encode(); ctype = "application/json"
                elif path.rstrip("/") == "/ca.crt":
                    # The private CA players must trust (NTL GateInfo.php runs over HTTPS with OS validation).
                    ca = os.path.join(certs_dir(), "ca.crt")
                    try:
                        out = open(ca, "rb").read(); ctype = "application/x-x509-ca-cert"
                    except OSError:
                        out = b"no CA configured"; ctype = "text/plain"
                elif "GateInfo" in path:
                    # NTL (NAT traversal) gate info: POST https://ntl.service.konami.net/ntl/api/GateInfo.php
                    # body req={"titleCode":"PES2021","locale":..,"version":..,"apiLevel":..}. Reply is
                    # "KEY: VALUE" lines (tokenizer 0x1418e9560); STATUS 200 + API_STATUS 0 = OK.
                    import time as _t
                    out = ("STATUS: 200\r\nAPI_STATUS: 0\r\nLOG_ACTIVE: 0\r\nSERVER_TIME: %d\r\n"
                           % int(_t.time())).encode()
                    ctype = "text/plain"
                    log.info("NTL GateInfo -> %r", out)
                elif b"msgid=" in body:
                    # Client "web API" commands (form-urlencoded, e.g. CMD_AUTH_STEAM to the AUTH_STEAM URL).
                    # Response parser 0x141e49040 reads msgid/rqid/result via a JSON-style value API.
                    from urllib.parse import parse_qs
                    from . import pescmds, pesmatch  # noqa: F401 (registers matchmaking/relay handlers)
                    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
                    msgid = form.get("msgid", "?")
                    from .protocol import record
                    record("rx", msgid, {**form, "_via": "http"})
                    # no gate session here: give handlers that build URLs the addresses of this connection
                    from types import SimpleNamespace
                    sock, peer = writer.get_extra_info("sockname") or ("",), writer.get_extra_info("peername") or ("",)
                    form["_http"] = SimpleNamespace(local_ip=sock[0], peer_ip=peer[0], pes_pid=None)
                    resp = pescmds.dispatch(service, None, msgid, form)
                    resp = {"msgid": msgid, "rqid": int(form.get("rqid", 0) or 0), **resp}
                    import msgpack
                    ctype = "application/octet-stream"   # raw msgpack map (CmdAuthSteamResult parse 0x141e49160)
                    out = msgpack.packb(resp, use_bin_type=True, unicode_errors="surrogateescape")
                    record("tx", msgid, resp)
                    from .ncl import _short
                    log.info("HTTP API %s -> %s", msgid, {k: _short(v) for k, v in resp.items()}
                             if isinstance(resp, dict) else resp)
                elif path.lower().lstrip("/").startswith(("eula", "u")):
                    out = (b"<html><body><h3>Local PES 2021 server</h3><p>This is a private, local "
                           b"preservation server for PES 2021 online play.</p></body></html>")
                    ctype = "text/html; charset=utf-8"
                else:
                    out = b"ok"; ctype = "text/plain"
                writer.write(b"HTTP/1.1 " + status.encode() + b"\r\nCache-Control: " + cache + b"\r\nContent-Type: " + ctype.encode() +
                             b"\r\nContent-Length: " + str(send_file[1] if send_file else len(out)).encode() + b"\r\n" + extra
                             + (b"Connection: keep-alive\r\nKeep-Alive: timeout=%d\r\n\r\n" % KEEP_IDLE if keep else b"Connection: close\r\n\r\n")
                             + (b"" if method == "HEAD" else out))
                await writer.drain()
                if send_file and method != "HEAD":
                    await _send_file(writer, *send_file)
                served += 1
                if not keep:
                    return
        except Exception as e:  # noqa
            peer = writer.get_extra_info("peername"); tls = writer.get_extra_info("ssl_object")
            log.info("HTTP%s conn from %s ended without a complete request: %r",
                     "S" if tls else "", peer, e)
        finally:
            writer.close()
    return on_conn


async def _send_file(writer, path, size, piece=1 << 18):
    """Send exactly `size` bytes of a file without holding it in memory or stopping the server while the disk
    reads: other players' games keep being answered during a long download."""
    left = size
    with open(path, "rb") as fh:
        while left > 0:
            data = await asyncio.to_thread(fh.read, min(piece, left))
            if not data:
                raise ConnectionError("file got shorter while it was being sent")
            writer.write(data)
            await writer.drain()                        # wait for a slow download instead of buffering it all
            left -= len(data)


WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_SID = []


def certs_dir():
    """Where the server's certificates are (__main__ sets it to <data>/certs)."""
    return os.environ.get("PESSERVER_CERTS") or "certs"


def server_id():
    """Short id of this server (from its CA), so join scripts can tell our server from another
    device at the same LAN address."""
    if not _SID:
        import hashlib
        ca = os.path.join(certs_dir(), "ca.crt")
        try:
            _SID.append(hashlib.sha1(open(ca, "rb").read()).hexdigest()[:12])
        except OSError:
            _SID.append("nocert")
    return _SID[0]


def _rl_key(peer):
    import ipaddress
    try:
        ip = ipaddress.ip_address(peer.split("%")[0])
    except ValueError:
        return peer
    if ip.version == 6 and not ip.ipv4_mapped:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip.ipv4_mapped or ip) if ip.version == 6 else str(ip)


_FAILED = {}                                  # client IP -> times of recent wrong passwords


class AdminSessions:
    """Sign-ins to the admin page. The browser gets a random token instead of keeping the password;
    only a hash of each token is stored (admin_sessions.json in the data folder), so the file can't
    be used to sign in. Sign out revokes the token."""
    REMEMBER, BRIEF = 30 * 86400, 12 * 3600

    def __init__(self, data_dir=None):
        self.path = os.path.join(data_dir, "admin_sessions.json") if data_dir else None
        self.s = {}
        if self.path:
            try:
                self.s = json.load(open(self.path))
            except (OSError, ValueError):
                self.s = {}

    @staticmethod
    def _h(token):
        import hashlib
        return hashlib.sha256(token.encode()).hexdigest()

    def _save(self):
        import time as _t
        self.s = {k: v for k, v in self.s.items() if v > _t.time()}
        if self.path:
            tmp = self.path + ".tmp"
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
                json.dump(self.s, f)
            os.replace(tmp, self.path)

    def new(self, remember):
        import secrets, time as _t
        token = secrets.token_urlsafe(32)
        self.s[self._h(token)] = _t.time() + (self.REMEMBER if remember else self.BRIEF)
        self._save()
        return token

    def valid(self, token):
        import time as _t
        return bool(token) and self.s.get(self._h(token), 0) > _t.time()

    def revoke(self, token):
        self.s.pop(self._h(token), None)
        self._save()

    def clear(self):
        self.s = {}
        self._save()

    def expires(self, token):
        return self.s.get(self._h(token), 0)


def _sessions(service):
    if not hasattr(service, "_admin_sessions"):
        service._admin_sessions = AdminSessions(getattr(service, "data_dir", None))
    return service._admin_sessions


# The admin page from the internet: off unless the host turns it on (a switch on the admin page,
# kept in <data>/admin_remote). When on, internet visitors get it over HTTPS only.
REMOTE = {"on": False, "path": None}


def load_remote(data_dir):
    REMOTE["path"] = os.path.join(data_dir, "admin_remote")
    REMOTE["on"] = os.path.exists(REMOTE["path"])


def set_remote(on):
    REMOTE["on"] = bool(on)
    if REMOTE["path"]:
        if on:
            open(REMOTE["path"], "w").close()
        elif os.path.exists(REMOTE["path"]):
            os.remove(REMOTE["path"])


def _peer_ip(peer):
    import ipaddress
    try:
        ip = ipaddress.ip_address((peer or "").split("%")[0])
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def from_internet(peer):
    """Not this machine, the home network or Tailscale (100.64.0.0/10 isn't 'global')."""
    ip = _peer_ip(peer)
    return ip is None or ip.is_global


PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "via",
                 "tailscale-user-login", "cf-connecting-ip")


def proxied(headers):
    """The request came through a proxy (tailscale serve, a tunnel, nginx): the connection is from this
    computer, the person may be anywhere."""
    return any(h in headers for h in PROXY_HEADERS)


def same_pc(peer, headers=None):
    """The browser runs on the server's own computer (loopback, or this machine's own address), and the
    request didn't come through a proxy running here."""
    from . import netsetup
    ip = _peer_ip(peer)
    return bool(ip) and (ip.is_loopback or str(ip) == (netsetup.STATE.get("lan") or "")) and not proxied(headers or {})


ADMIN_POLLS = ("/admin/api/state", "/admin/api/games", "/admin/api/games/auto", "/admin/api/program")
# ^ an open admin page asks these every few seconds: logged at debug so they don't crowd the game's lines out of the log
KEEP_IDLE = 15          # seconds a browser's admin connection stays open for the next poll (it polls every 3 s)
KEEP_MAX = 1000         # requests on one connection before it's closed and the browser opens a fresh one
_GZ = {}                # compressed copies of large unchanging bodies (the admin page): digest -> bytes


def _takes_gzip(headers):
    for part in headers.get("accept-encoding", "").lower().split(","):
        name, _, q = part.strip().partition(";")
        if name.strip() in ("gzip", "*"):
            return q.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000")
    return False


def gzipped(out, ctype, headers):
    """(body, extra headers): text and JSON compressed when the client takes it; JSON shrinks about 4x."""
    import gzip, hashlib
    if not ctype.startswith(("text/", "application/json")):
        return out, b""
    if len(out) < 1024 or not _takes_gzip(headers):
        return out, b"Vary: Accept-Encoding\r\n"
    if len(out) > 1 << 16:              # big and the same every time: compress once
        key = hashlib.blake2b(out, digest_size=16).digest()
        if key not in _GZ:
            _GZ.clear(); _GZ[key] = gzip.compress(out, 9, mtime=0)
        return _GZ[key], b"Content-Encoding: gzip\r\nVary: Accept-Encoding\r\n"
    return gzip.compress(out, 6, mtime=0), b"Content-Encoding: gzip\r\nVary: Accept-Encoding\r\n"


def state_reply(snap, seen):
    """The page polls this every 3 s and usually nothing changed but the clock: when the state matches the tag
    the page already has (X-State-Tag), answer {"same": tag} with just the clock instead of the whole state."""
    import hashlib
    clock = {k: snap.pop(k) for k in ("now", "uptime") if k in snap}
    core = json.dumps(snap, separators=(",", ":"))
    tag = hashlib.blake2b(core.encode(), digest_size=12).hexdigest()
    if seen == tag:
        return json.dumps({"same": tag, **clock}).encode()
    return json.dumps({**snap, **clock, "tag": tag}, separators=(",", ":")).encode()


def _admin_cache(route, method, status, out, ctype, headers):
    """(Cache-Control, body, extra headers) for an admin response. The page is kept by the browser but checked on
    every load (ETag), so opening it again costs a few bytes; the API is never stored. Both go compressed."""
    import hashlib
    if route == "/admin" and method in ("GET", "HEAD") and status.startswith("200"):
        tag = b'"' + hashlib.blake2b(out, digest_size=12).hexdigest().encode() + b'"'
        if tag in [t.strip().encode() for t in headers.get("if-none-match", "").split(",")]:
            return b"not-modified", b"", b"ETag: " + tag + b"\r\nVary: Accept-Encoding\r\n"
        out, enc = gzipped(out, ctype, headers)
        return b"no-cache", out, b"ETag: " + tag + b"\r\n" + enc
    out, enc = gzipped(out, ctype, headers)
    return b"no-store", out, enc


def admin_allowed(peer):
    """The admin page opens from this machine, the home network and Tailscale; from the internet
    only when the host switched that on."""
    return REMOTE["on"] or not from_internet(peer)


ADMIN_BLOCKED = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PES 2021 Admin</title><body style="font:16px/1.6 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1>Admin page: home network only</h1><p>For safety, the admin page only opens from the server's own network
(and Tailscale). Open it from a device on the same Wi-Fi as the server, using the address the server printed.</p>
<p>To open it from anywhere, the host turns on <b>Admin page from the internet</b> on the admin page (Server, Admin access).</p>
</body>"""


def page_csp(html):
    """Only the page's own inline scripts may run (hash-pinned); no other origins."""
    import base64, hashlib, re as _re
    hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(m.encode()).digest()).decode()
                      for m in _re.findall(r"<script>(.*?)</script>", html, _re.S))
    return (f"default-src 'none'; script-src {hashes or chr(39) + 'none' + chr(39)}; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def admin_csp():
    if not _CSP:
        _CSP.append(page_csp(open(os.path.join(WEB, "admin.html"), encoding="utf-8").read()))
    return _CSP[0]


_CSP = []


def host_ok(headers):
    """Block DNS rebinding: the admin API answers only to addresses or local names, not web domains."""
    from . import netsetup
    host = headers.get("host", "").strip().lower()
    name = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    pinned = (os.environ.get("PESSERVER_PUBLIC_ADDR") or "").lower()
    return (not name or netsetup._ip(name) or name == "localhost" or name == pinned
            or name.endswith((".local", ".lan", ".home", ".internal", ".ts.net")) or "." not in name)


def _admin_fp(service):
    from . import certs
    return certs.admin_fingerprint(os.path.join(getattr(service, "data_dir", "") or ".", "certs"))


def _https_page(headers):
    url = "https://" + headers.get("host", "") + "/admin"
    return ('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="0;url={url}"><title>PES 2021 Admin</title>'
            '<body style="font:16px/1.6 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">'
            f'<h1>Opening the secure page…</h1><p>From the internet the admin page only opens encrypted: <a href="{url}">{url}</a>. '
            "Your browser will warn that it can't check the certificate (it's the server's own): continue to the site.</p></body>").encode()


async def _admin(service, method, route, headers, body, peer="", secure=False, path=""):
    """Admin page (/admin) and its JSON API. The server's own PC signs in without a password; other devices
    use the password; from the internet only when switched on, and only over HTTPS."""
    import hmac, time as _time
    from . import events, netsetup
    if route == "/admin/api/info":            # public: lets the sign-in page warn before a password is typed
        return "200 OK", json.dumps({"remote": REMOTE["on"], "internet": from_internet(peer), "host_ok": bool(host_ok(headers)),
                                     "same_pc": same_pc(peer, headers) and bool(host_ok(headers)),
                                     "lan": (netsetup.STATE.get("lan") or "") if admin_allowed(peer) else "",
                                     "admin_url": netsetup.web_url(netsetup.STATE.get("lan") or "") + "/admin" if admin_allowed(peer) else "",
                                     "password_replaced": bool(netsetup.PASSWORD_IGNORED)}).encode(), "application/json"
    if route.startswith("/admin/api/") and not host_ok(headers):
        return "403 Forbidden", b'{"error":"open the admin page by its address"}', "application/json"
    if not admin_allowed(peer):
        if route == "/admin":
            return "403 Forbidden", ADMIN_BLOCKED, "text/html; charset=utf-8"
        return "403 Forbidden", b'{"error":"home network only"}', "application/json"
    if from_internet(peer) and not secure:        # never let the password cross the internet in the clear
        if route == "/admin":
            return "200 OK", _https_page(headers), "text/html; charset=utf-8"
        return "403 Forbidden", b'{"error":"use https"}', "application/json"
    if route == "/admin":
        return "200 OK", open(os.path.join(WEB, "admin.html"), "rb").read(), "text/html; charset=utf-8"
    now = _time.time()
    if route == "/admin/api/login" and method == "POST":
        for ip in [ip for ip, ts in _FAILED.items() if not ts or now - ts[-1] > 60]:
            del _FAILED[ip]                   # forget addresses with no recent failures
        key = _rl_key(peer)                   # IPv6: one /64 counts as one address
        recent = [t for t in _FAILED.get(key, []) if now - t < 60]
        _FAILED[key] = recent
        everyone = sum(len([t for t in ts if now - t < 60]) for ts in _FAILED.values())
        import ipaddress
        try:
            local = not ipaddress.ip_address(peer.split("%")[0]).is_global
        except ValueError:
            local = False
        # per address; the overall cap only stops outside guessers, never the owner at home
        if len(recent) >= 5 or (everyone >= 30 and not local):
            return "429 Too Many Requests", b'{"error":"too many wrong passwords; wait a minute"}', "application/json"
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            req = {}
        # The host's own PC: no password. host_ok (checked above) stops DNS rebinding; the custom header
        # can't be sent cross-site without a CORS preflight we never answer, so websites can't even try.
        if req.get("local") and same_pc(peer, headers) and headers.get("x-pes-local") == "1":
            token = _sessions(service).new(True)
            return "200 OK", json.dumps({"token": token, "expires": _sessions(service).expires(token)}).encode(), "application/json"
        pw = getattr(service, "admin_password", None)
        if not pw or not hmac.compare_digest(str(req.get("password", "")).encode(), pw.encode()):
            recent.append(now)
            await asyncio.sleep(1)            # slow down password guessing
            return "401 Unauthorized", b'{"error":"wrong password"}', "application/json"
        # sign-ins from the internet can't be kept for 30 days
        token = _sessions(service).new(bool(req.get("remember")) and not from_internet(peer))
        return "200 OK", json.dumps({"token": token, "expires": _sessions(service).expires(token)}).encode(), "application/json"
    given = headers.get("authorization", "")[7:] if headers.get("authorization", "").startswith("Bearer ") else ""
    if not _sessions(service).valid(given):
        return "401 Unauthorized", b'{"error":"signed out"}', "application/json"
    if route == "/admin/api/logout" and method == "POST":
        _sessions(service).revoke(given)
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/logout-all" and method == "POST":
        _sessions(service).clear()
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/remote" and method == "POST":
        try:
            on = bool(json.loads(body or b"{}").get("on"))
        except ValueError:
            return "400 Bad Request", b'{"error":"bad request"}', "application/json"
        set_remote(on)
        events.feed(service, "The admin page can now be opened from the internet (HTTPS)" if on
                    else "The admin page is back to home network only", "info")
        return "200 OK", json.dumps({"remote_admin": REMOTE["on"]}).encode(), "application/json"
    if route == "/admin/api/state":
        snap = events.snapshot(service)
        st = netsetup.STATE
        pinned = os.environ.get("PESSERVER_PUBLIC_ADDR") or ""
        reachable = bool(st["mapped"] or pinned) and (not st["cgnat"] or _tailscale(pinned))
        addrs = netsetup.game_addrs()
        main = netsetup.web_url(addrs[-1] if reachable else addrs[0])   # a link that works for someone
        from . import VERSION, updates
        snap["results"] = []                  # (an admin page from before 2026-11-12, left open over the update, reads it)
        snap["server"] = {"main": main, "version": VERSION, "update": updates.STATE["latest"],
                          "ports": netsetup.port_list(),
                          "data_dir": getattr(service, "data_dir", ""), "lan_url": netsetup.web_url(st["lan"] or ""), "lan": st["lan"], "wan": st["wan"],
                          "pinned": os.environ.get("PESSERVER_PUBLIC_ADDR"), "ports_open": st["mapped"],
                          "cgnat": st["cgnat"], "upnp_error": st["upnp_error"],
                          "remote_admin": REMOTE["on"], "remote_url": f"https://{st['wan']}:{os.environ.get('PESSERVER_HTTP_PORT', '8080')}/admin" if st["wan"] else "",
                          "admin_fp": _admin_fp(service), "same_pc": same_pc(peer, headers),
                          "key": (lambda k: k[:8] + " " + k[8:16])(__import__("pesserver.identity", fromlist=["x"]).public_hex()),
                          "has_mod": os.path.isfile(MOD_DLL)}
        from . import announce, adminextra, pesbackup, pesdiscord
        snap["announce"] = announce.snapshot(service)
        snap["server"]["discord"] = pesdiscord.status(service)
        snap["server"]["backups"] = {"folder": pesbackup.folder(service), "list": pesbackup.listing(service)[:12]}
        adminextra.augment(service, snap)
        snap["server"].update(adminextra.server(service))
        snap["modes"] = [r[0] for r in service.accounts.db.execute(
            "SELECT DISTINCT COALESCE(mode, 'Quick Match') FROM pes_matches")]
        return "200 OK", state_reply(snap, headers.get("x-state-tag", "")), "application/json"
    if route in ("/admin/api/message", "/admin/api/restart") and method == "POST":
        from . import announce
        try:
            req = json.loads(body or b"{}")
            if route.endswith("message"):
                if req.get("check"):          # the live "fits in the game's box" meter; nothing is saved
                    return "200 OK", json.dumps({"pct": announce.fits(req.get("title"), req.get("body"), bool(req.get("personal")))}).encode(), "application/json"
                if req.get("clear"):
                    announce.clear_news(service)
                    events.feed(service, "The message to everyone was removed", "msg")
                else:
                    announce.set_news(service, req.get("title"), req.get("body"))
                    events.feed(service, f"Message to everyone: “{announce.clean(req.get('title'), announce.TITLE_MAX, False)}”", "msg")
            else:
                minutes = int(req.get("minutes") or 0)
                if not 0 <= minutes <= 24 * 60:
                    raise ValueError("minutes")
                announce.set_restart(service, minutes)
                events.feed(service, "Restart warning cancelled" if not minutes
                            else f"Restart warning: the server goes down in {minutes} minutes" if minutes < 120
                            else f"Restart warning: the server goes down in {minutes // 60} hours", "msg")
        except announce.TooLong:
            return "400 Bad Request", b'{"error":"too long"}', "application/json"
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"Write a title and a message."}', "application/json"
        return "200 OK", json.dumps(announce.snapshot(service)).encode(), "application/json"
    if route == "/admin/api/message/player" and method == "POST":
        from . import announce
        try:
            req = json.loads(body or b"{}")
            pid = int(req.get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        if pid not in service.online and not service.accounts.db.execute("SELECT 1 FROM pes_players WHERE pid=?", (pid,)).fetchone():
            return "404 Not Found", b'{"error":"That player is gone."}', "application/json"
        name = events.name_of(service, pid)
        if req.get("cancel"):
            mid = req.get("id")
            if announce.untell(service, pid, int(mid) if isinstance(mid, (int, str)) and str(mid).isdigit() else None):
                events.feed(service, f"The message to {name} was taken back", "msg", [pid])
            return "200 OK", json.dumps(announce.snapshot(service)).encode(), "application/json"
        try:
            announce.tell(service, pid, req.get("title"), req.get("body"), kind="note")
        except announce.TooLong:
            return "400 Bad Request", b'{"error":"too long"}', "application/json"
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"Write a title and a message."}', "application/json"
        events.feed(service, f"Message sent to {name}: “{announce.clean(req.get('title'), announce.TITLE_MAX, False)}”", "msg", [pid])
        return "200 OK", json.dumps(announce.snapshot(service)).encode(), "application/json"
    if route == "/admin/api/games":
        from . import gamesetup                     # (off the loop: it reads each copy's mod file and Wine settings)
        return "200 OK", json.dumps(await asyncio.to_thread(gamesetup.status)).encode(), "application/json"
    if route == "/admin/api/games/auto":
        from . import gamesetup                     # keep the mod up to date in this computer's copies
        if method == "POST":
            try:
                on = bool(json.loads(body or b"{}").get("on"))
            except (ValueError, AttributeError):
                return "400 Bad Request", b'{"error":"on"}', "application/json"
            gamesetup.set_auto(on)
            events.feed(service, "The server keeps the mod up to date in PES 2021 on this computer" if on else
                        "The server no longer puts the mod into PES 2021 on this computer by itself", "info")
        running = await asyncio.to_thread(gamesetup.game_running)    # reads every process's command line
        return "200 OK", json.dumps({"on": gamesetup.AUTO["on"], "available": gamesetup.AUTO["started"],
                                     "running": running}).encode(), "application/json"
    if route in ("/admin/api/games/install", "/admin/api/games/remove") and method == "POST":
        from . import gamesetup
        try:
            gid = str(json.loads(body or b"{}").get("id", ""))
        except (ValueError, AttributeError):
            return "400 Bad Request", b'{"error":"bad request"}', "application/json"
        try:
            if route.endswith("install"):
                if not os.path.isfile(MOD_DLL):
                    return "501 Not Implemented", b'{"error":"This server was built without the mod."}', "application/json"
                result = await asyncio.to_thread(gamesetup.install, gid, mod_bytes(mod_url(service, headers, None)))
                verb = "installed in"
            else:
                result = await asyncio.to_thread(gamesetup.remove, gid)
                verb = "removed from"
            path = next((g["path"] for g in result if g["id"] == gid), "")
            events.feed(service, f"The mod was {verb} PES 2021 on the server computer ({copy_name(path)})", "info")
        except LookupError:
            return "404 Not Found", b'{"error":"That game folder is gone. Start the server again to look for PES 2021."}', "application/json"
        except PermissionError as e:
            msg = str(e) if "dinput8.dll" in str(e) and "Errno" not in str(e) else \
                "The server isn't allowed to write to this folder. Check that your user owns the game folder."
            return "409 Conflict", json.dumps({"error": msg}).encode(), "application/json"
        except OSError:
            log.exception("installing the mod")
            return "409 Conflict", b'{"error":"Couldn\'t write to the game folder. Close the game and try again."}', "application/json"
        return "200 OK", json.dumps(result).encode(), "application/json"
    if route == "/admin/api/kick" and method == "POST":
        try:
            req = json.loads(body or b"{}")
            pid = int(req.get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        sess = service.online.get(pid)
        if sess is None or not hasattr(sess, "close"):
            return "404 Not Found", b'{"error":"not online"}', "application/json"
        events.feed(service, f"{events.name_of(service, pid)} was kicked by the admin", "warn", [pid])
        # The game shows only "Connection to server lost" (VNBP_0001) whatever error we send, so the
        # reason waits as a one-time box the next time they enter an online mode.
        from . import announce
        try:
            reason = "\n\nReason: " + announce.clean(req.get("reason"), announce.REASON_MAX)
        except ValueError:
            reason = ""
        try:
            announce.tell(service, pid, "You were disconnected", "The host disconnected you from this server." + reason, kind="kick")
        except announce.TooLong:                  # a very long non-Latin reason: send the note without it
            announce.tell(service, pid, "You were disconnected", "The host disconnected you from this server.", kind="kick")
        sess.kicked = True              # not an early exit for the courtesy rating
        from . import pescmds
        pescmds.show_out(service, pid)  # ... and the game's own reconnect isn't let back in right away
        sess.close()
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/results" and method == "POST":
        try:
            req = json.loads(body or b"{}")
            rows, total = events.results_page(service, req.get("q", ""), req.get("since") or 0, req.get("limit") or 25,
                                               req.get("until") or None, bool(req.get("early")), req.get("mode") or None)
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"bad request"}', "application/json"
        return "200 OK", json.dumps({"rows": rows, "total": total}).encode(), "application/json"
    if route == "/admin/api/match/count" and method == "POST":
        try:
            req = json.loads(body or b"{}")
            mid, counted = int(req.get("id")), bool(req.get("counted"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"id"}', "application/json"
        m = events.set_counted(service, mid, counted)
        if m is None:
            return "404 Not Found", b'{"error":"That match is gone."}', "application/json"
        return "200 OK", json.dumps(m).encode(), "application/json"
    if route == "/admin/api/player" and method == "POST":
        try:
            pid = int(json.loads(body or b"{}").get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        p = events.player_detail(service, pid)
        if p is None:
            return "404 Not Found", b'{"error":"That player is gone."}', "application/json"
        from . import adminextra, pesnames
        p.update(adminextra.player(service, pid))
        p["blocked"], p["claims"] = pesnames.is_blocked(service, pid), pesnames.claims_for(service, pid)
        return "200 OK", json.dumps(p).encode(), "application/json"
    if route in ("/admin/api/players/block", "/admin/api/players/move") and method == "POST":
        from . import pesnames                      # Block a player's copy; move a player to their new copy
        try:
            req = json.loads(body or b"{}")
            pid = int(req.get("pid"))
            res = pesnames.set_blocked(service, pid, bool(req.get("on"))) if route.endswith("/block") \
                else pesnames.move(service, pid, str(req.get("copy") or ""))
        except LookupError as e:
            return "404 Not Found", json.dumps({"error": str(e).strip("'")}).encode(), "application/json"
        except (ValueError, TypeError, AttributeError) as e:
            return "400 Bad Request", json.dumps({"error": str(e) or "bad request"}).encode(), "application/json"
        return "200 OK", json.dumps(res).encode(), "application/json"
    if route == "/admin/api/player/courtesy" and method == "POST":
        # {pid}: clear the courtesy record (early exits the host knows weren't the player's fault)
        try:
            pid = int(json.loads(body or b"{}").get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        if not service.accounts.db.execute("SELECT 1 FROM pes_players WHERE pid=?", (pid,)).fetchone():
            return "404 Not Found", b'{"error":"That player is gone."}', "application/json"
        from . import adminextra
        adminextra.clear_courtesy(service, pid)
        events.feed(service, f"The admin cleared {events.name_of(service, pid)}'s courtesy record", "info", [pid])
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/players/delete" and method == "POST":
        try:
            pid = int(json.loads(body or b"{}").get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        from . import pescmds, pesundo
        undo = pesundo.capture(service, pid)            # kept for 30 days: Players, Deleted players, Restore
        name = pescmds.purge_player(service, pid)       # the player and every module's rows for them
        if name is None:
            return "404 Not Found", b'{"error":"That player is already gone."}', "application/json"
        sess = service.online.get(pid)
        if sess is not None:
            sess.kicked = True          # closing it below is not an early exit (no forfeit, no courtesy mark)
            pescmds.show_out(service, pid)
        if sess is not None and hasattr(sess, "close"):
            sess.close()                # their next login registers them again (name prompt)
        events.feed(service, f"{name} was deleted by the admin", "warn")
        return "200 OK", json.dumps({"ok": True, "undo": undo}).encode(), "application/json"
    if route == "/admin/api/players/restore" and method == "POST":
        from . import pesundo                       # undo a delete
        try:
            name = pesundo.restore(service, int(json.loads(body or b"{}").get("id")))
        except LookupError as e:
            return "404 Not Found", json.dumps({"error": str(e).strip("'")}).encode(), "application/json"
        except (ValueError, TypeError, AttributeError) as e:
            return "409 Conflict", json.dumps({"error": str(e) if isinstance(e, ValueError) and " " in str(e) else "bad request"}).encode(), "application/json"
        events.feed(service, f"The admin restored {name}", "info")
        return "200 OK", json.dumps({"ok": True, "name": name}).encode(), "application/json"
    if route.startswith("/admin/api/season") and method == "POST":
        from . import pesseason                     # the league table's seasons
        try:
            req = json.loads(body or b"{}")
            if route == "/admin/api/season/new":
                done = pesseason.start_new(service, req.get("name"))
                events.feed(service, f"The admin closed {done['name']} and started a new season", "info")
                return "200 OK", json.dumps(done).encode(), "application/json"
            if route == "/admin/api/season/reopen":
                name = pesseason.reopen(service)
                if name is None:
                    return "404 Not Found", b'{"error":"There is no finished season."}', "application/json"
                events.feed(service, f"The admin reopened {name}: the table counts its matches again", "info")
                return "200 OK", json.dumps({"name": name}).encode(), "application/json"
            t = pesseason.table(service, req.get("id"))
        except (ValueError, TypeError, AttributeError) as e:
            return "400 Bad Request", json.dumps({"error": str(e) if isinstance(e, ValueError) and " " in str(e) else "bad request"}).encode(), "application/json"
        if t is None:
            return "404 Not Found", b'{"error":"That season is gone."}', "application/json"
        return "200 OK", json.dumps(t).encode(), "application/json"
    if route == "/admin/api/discord" and method == "POST":
        from . import pesdiscord                    # notices in the friends' Discord channel
        try:
            req = json.loads(body or b"{}")
            if req.get("test"):
                err = await asyncio.to_thread(pesdiscord.test, service)
                return "200 OK", json.dumps({**pesdiscord.status(service), "sent": not err}).encode(), "application/json"
            was = pesdiscord.status(service)["on"]
            st = pesdiscord.save(service, req.get("url"), req.get("search"), req.get("results"))
        except (ValueError, TypeError, AttributeError) as e:
            return "400 Bad Request", json.dumps({"error": str(e) if isinstance(e, ValueError) and " " in str(e) else "bad request"}).encode(), "application/json"
        except OSError:
            return "500 Internal Server Error", b'{"error":"could not write the file"}', "application/json"
        if st["on"] != was:
            events.feed(service, "The server now posts in your Discord channel" if st["on"] else "The server no longer posts in Discord", "info")
        return "200 OK", json.dumps(st).encode(), "application/json"
    if route.startswith("/admin/api/backups"):
        from . import pesbackup                     # the database: back up, download, restore
        if route == "/admin/api/backups/file" and method in ("GET", "HEAD"):
            from urllib.parse import parse_qs, urlsplit
            f = pesbackup.file_for(service, parse_qs(urlsplit(path).query).get("name", [""])[0])
            if f is None:
                return "404 Not Found", b'{"error":"That backup is gone."}', "application/json"
            return "200 OK", ("file", f, os.path.getsize(f)), "application/octet-stream"
        if method != "POST":
            return "404 Not Found", b'{"error":"unknown"}', "application/json"
        try:
            if route == "/admin/api/backups/new":
                made = await asyncio.to_thread(pesbackup.make, service)
                events.feed(service, "The admin made a backup", "info")
                return "200 OK", json.dumps(made).encode(), "application/json"
            if route == "/admin/api/backups/restore":
                name = str(json.loads(body or b"{}").get("name") or "")
                found = pesbackup.restore(service, name)
                events.feed(service, f"The admin restored the backup {name}", "warn")
                return "200 OK", json.dumps(found).encode(), "application/json"
        except LookupError as e:
            return "404 Not Found", json.dumps({"error": str(e).strip("'")}).encode(), "application/json"
        except (ValueError, AttributeError) as e:
            return "400 Bad Request", json.dumps({"error": str(e) if " " in str(e) else "bad request"}).encode(), "application/json"
        except RuntimeError:
            log.exception("restore")
            return "500 Internal Server Error", b'{"error":"The backup couldn\'t be put in place. Nothing was changed."}', "application/json"
        except (OSError, sqlite3.Error):
            log.exception("backup")
            return "500 Internal Server Error", b'{"error":"Couldn\'t write the backup. Is the disk full?"}', "application/json"
        return "404 Not Found", b'{"error":"unknown"}', "application/json"
    if route == "/admin/api/program" or route.startswith("/admin/api/program/"):
        from . import hostctl                       # update, restart, stop, start when I log in (hostctl.py)
        # off the event loop: it runs systemctl (up to 15 s each) and the page asks every few seconds
        status, res = await asyncio.to_thread(hostctl.admin_api, service, route, method, body)
        if method == "POST" and status.startswith("200"):
            what = {"/admin/api/program/update": "asked for a server update", "/admin/api/program/restart": "restarted the server",
                    "/admin/api/program/stop": "stopped the server", "/admin/api/program/background": "moved the server to the background"}
            if route in what and not (route.endswith("/update") and b"cancel" in (body or b"")):
                events.feed(service, "The admin " + what[route], "info")
            elif route == "/admin/api/program/autostart":
                events.feed(service, "The server now starts when you log in" if res.get("autostart") else
                            "The server no longer starts by itself", "info")
        return status, json.dumps(res).encode(), "application/json"
    if route.startswith("/admin/api/tourneys"):
        from . import pestourney                    # Online Tournament competitions (list, save, end, delete)
        before = {c["compe_id"]: c["name"] for c in pestourney.competitions(service)} if method == "POST" else {}
        status, out, ctype = pestourney.admin_api(service, route, method, body)
        if method == "POST" and status.startswith("200"):
            try:
                req, res = json.loads(body or b"{}"), json.loads(out)
                cid = res.get("compe_id", req.get("compe_id"))
                name = before.get(_int(cid)) or req.get("name") or "a competition"
                line = {"/admin/api/tourneys/save": f"The admin {'changed' if _int(req.get('compe_id')) in before else 'made'} the Online Tournament “{req.get('name') or name}”",
                        "/admin/api/tourneys/end": f"The admin ended the Online Tournament “{name}”",
                        "/admin/api/tourneys/delete": f"The admin deleted the Online Tournament “{name}”",
                        "/admin/api/tourneys/monthly": "The admin turned the monthly Friends Cup " + ("on" if res.get("monthly_cup") else "off")}.get(route)
                if line:
                    events.feed(service, line, "info")
            except Exception:  # noqa: BLE001 - the change itself is done
                log.exception("tourney feed line")
        return status, out, ctype
    if route == "/admin/api/teams" and method == "GET":
        from . import adminextra, pestourney        # team names for the tournament form (the game's own list)
        return "200 OK", json.dumps({**adminextra.teams(), "default": pestourney.TEAMS}).encode(), "application/json"
    if route == "/admin/api/words" and method == "POST":
        # {words: [..]}: replace the word filter (<data>/blocked_words.txt)
        from . import adminextra
        try:
            w = adminextra.set_words(service, json.loads(body or b"{}").get("words"))
        except (ValueError, AttributeError) as e:
            return "400 Bad Request", json.dumps({"error": str(e)}).encode(), "application/json"
        except OSError:
            return "500 Internal Server Error", b'{"error":"could not write the file"}', "application/json"
        events.feed(service, f"The admin changed the word filter ({len(w['words'])} word{'' if len(w['words']) == 1 else 's'})", "info")
        return "200 OK", json.dumps(w).encode(), "application/json"
    return "404 Not Found", b'{"error":"unknown"}', "application/json"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _web(service, route, headers, writer):
    """Player-facing pages: '/' (instructions), '/join' and '/leave' (setup script for the caller's
    OS, picked from the User-Agent: PowerShell -> .ps1, curl/wget -> bash, browser -> the page)."""
    from . import netsetup
    host, server, scheme, addrs = _origin(service, headers, writer)
    if route == "/banner":
        return (netsetup.banner(color=False) if netsetup.STATE["ready"] else "starting").encode(), "text/plain; charset=utf-8"
    if route == "/install-ca-proton.py":
        return open(os.path.join(WEB, "install-ca-proton.py"), "rb").read(), "text/x-python"
    ua = headers.get("user-agent", "").lower()
    if route.endswith(".ps1") or (route in ("/join", "/leave") and "powershell" in ua):
        name, ctype = "join.ps1", "text/plain; charset=utf-8"
    elif route.endswith(".sh") or (route in ("/join", "/leave") and ua.startswith(("curl/", "wget/"))):
        name, ctype = "join.sh", "text/x-shellscript; charset=utf-8"
    else:
        name, ctype = "index.html", "text/html; charset=utf-8"
    text = open(os.path.join(WEB, name), encoding="utf-8").read()
    text = (text.replace("@@SERVER@@", server).replace("@@BASE@@", f"{scheme}://{host}")
                .replace("@@ADDRS@@", " ".join(addrs)).replace("@@SID@@", server_id())
                .replace("@@UNINSTALL@@", "1" if "/leave" in route else "0"))
    return text.encode(), ctype


def _origin(service, headers, writer):
    """How this player reached us: (host[:port], host, scheme, game addresses best first)."""
    from . import netsetup
    host = headers.get("host", "").strip()
    if not host and writer is not None:
        from types import SimpleNamespace
        conn = SimpleNamespace(peer_ip=(writer.get_extra_info("peername") or ("",))[0],
                               local_ip=(writer.get_extra_info("sockname") or ("",))[0])
        host = netsetup.advertised_addr(service, conn)
    server = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    scheme = "https" if headers.get("x-forwarded-proto") == "https" else "http"
    addrs = netsetup.game_addrs()
    if netsetup._ip(server):                  # reached by IP: that address works for this player
        addrs = [server] + [a for a in addrs if a != server]
    return host, server, scheme, addrs


# Packed into the program (PyInstaller) or, in a checkout, built by mod/build.sh
MOD_DLL = os.environ.get("PESSERVER_MOD_DLL") or os.path.join(
    getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mod", "dinput8.dll")


def _mod(service, route, headers, writer, path=""):
    """The mod's endpoints. /mod/note: where this server is now (signed). /mod/hello?n=<nonce>: proof
    that this address is this server (signed). /mod/dinput8.dll: the mod, with this server's identity
    and addresses written in, for the host to send to friends.
    Returns (status, body, content type, extra header bytes)."""
    from urllib.parse import parse_qs, urlsplit
    from . import identity, netsetup
    host, server, scheme, addrs = _origin(service, headers, writer)
    if route == "/mod/note":
        return "200 OK", identity.current_note().encode(), "text/plain; charset=utf-8", b""
    if route == "/mod/hello":
        q = parse_qs(urlsplit(path).query)
        nonce, v = q.get("n", [""])[0], q.get("v", ["1"])[0]
        try:
            return "200 OK", identity.hello(nonce, int(v) if v.isdigit() else 1).encode(), "text/plain; charset=utf-8", b""
        except ValueError:
            return "400 Bad Request", b"bad nonce", "text/plain", b""
    if not os.path.isfile(MOD_DLL):
        return "404 Not Found", b"This server was built without the mod.", "text/plain", b""
    return "200 OK", mod_bytes(mod_url(service, headers, writer)), "application/octet-stream", \
        b'Content-Disposition: attachment; filename="dinput8.dll"\r\n'


def copy_name(path):
    """A short name for a game copy: its folder, skipping generic parts (never a Linux user name)."""
    import re as _re
    generic = _re.compile(r"^((efootball )?pes ?2021( season update)?|files|bin|game|games|data|common|steamapps|drive_c)$", _re.I)
    parts = [x for x in str(path).split("/") if x]
    for i in range(len(parts) - 1, -1, -1):
        if generic.match(parts[i]):
            continue
        if i > 0 and parts[i - 1] == "home" or parts[i] in ("home", "opt", "mnt"):
            break
        return parts[i]
    return "game folder"


def _tailscale(addr):
    import ipaddress
    try:
        ip = ipaddress.ip_address(str(addr).strip("[]"))
    except ValueError:
        return False
    return ip in ipaddress.ip_network("100.64.0.0/10") if ip.version == 4 else ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")


_DOWNLOADS = {}


def note_download(service, peer):
    """An activity line when someone downloads the mod (once per address per 10 minutes), so the host
    sees that a friend got the file. Says where from, not who: downloads need no sign-in."""
    import ipaddress, time as _time
    from . import events
    now = _time.time()
    if now - _DOWNLOADS.get(peer, 0) < 600:
        return
    if len(_DOWNLOADS) > 500:
        _DOWNLOADS.clear()
    _DOWNLOADS[peer] = now
    try:
        ip = ipaddress.ip_address(peer.split("%")[0])
        ip = (ip.ipv4_mapped or ip) if ip.version == 6 else ip
    except ValueError:
        ip = None
    if ip is None:
        where = ""
    elif ip.is_loopback:
        where = " on this computer"
    elif ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48"):
        where = f" over Tailscale ({ip})"
    elif ip.is_private or ip.is_link_local:
        where = f" on your home network ({ip})"
    else:
        where = f" from the internet ({ip})"
    events.feed(service, f"Someone{where} downloaded the mod", "web")


def mod_url(service, headers, writer):
    """The web address written into a friend's mod: a real DNS name (e.g. dynamic DNS) outlives IP
    changes; IPs and local-only names (which would break friends' files) give none."""
    from . import netsetup
    host, server, scheme, _ = _origin(service, headers, writer)
    web = lambda n: bool(n) and not netsetup._ip(n) and n != "localhost" and "." in n \
        and not n.endswith((".local", ".lan", ".home", ".internal"))
    if web(server):
        return f"{scheme}://{host}"
    pinned = (os.environ.get("PESSERVER_PUBLIC_ADDR") or "").strip().lower()
    # reached by IP (the admin page, the server computer): use the fixed web name if there is one,
    # so every copy of the file (and its published hash) is the same
    return f"http://{pinned}" if web(pinned) else ""


_SHA = {}


def mod_sha256(url=""):
    """SHA-256 of the exact file /mod/dinput8.dll hands out (same server, same addresses), or None."""
    import hashlib
    from . import netsetup
    if not os.path.isfile(MOD_DLL):
        return None
    k = (url, tuple(netsetup.game_addrs()), os.path.getmtime(MOD_DLL))
    if k not in _SHA:
        _SHA.clear()
        _SHA[k] = hashlib.sha256(mod_bytes(url)).hexdigest()
    return _SHA[k]


def mod_bytes(url=""):
    """The mod DLL with this server's identity and addresses written in."""
    from . import identity, netsetup
    settings = identity.mod_settings(os.environ.get("SERVER_NAME", "PES 2021 server"), url, netsetup.game_addrs())
    return identity.pack_mod(open(MOD_DLL, "rb").read(), settings)


def tls_context(certfile, keyfile):
    """The game's network test asks for an NTL name (SNI) and gets the NTL certificate; anything else
    (a browser opening https://<address>:8080/admin sends no name for an IP) gets the admin certificate."""
    import ssl
    from . import certs
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    d = os.path.dirname(certfile)
    if os.path.exists(os.path.join(d, "admin.crt")):
        admin = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        admin.load_cert_chain(os.path.join(d, "admin.crt"), os.path.join(d, "admin.key"))

        def sni(sslobj, name, _ctx):
            if (name or "").lower() not in certs.NTL:
                sslobj.context = admin
        ctx.sni_callback = sni
    return ctx


async def serve_tls(service, host, port, certfile, keyfile, stun_host="127.0.0.1", stun_port=3478, gate_port=10000):
    """Same handler over TLS on its own port (443: players set up with the /join line, and older mods)."""
    ctx = tls_context(certfile, keyfile)

    def on_handshake_error(loop, context):
        log.info("TLS handshake/stream error: %s %r", context.get("message"), context.get("exception"))
    asyncio.get_event_loop().set_exception_handler(on_handshake_error)
    return await asyncio.start_server(make_handler(service, stun_host, stun_port, gate_port), host, port,
                                      ssl=ctx, ssl_handshake_timeout=10)


async def serve(service, host="0.0.0.0", port=8080, stun_host="127.0.0.1", stun_port=3478, gate_port=10000, tls=None):
    handler = make_handler(service, stun_host, stun_port, gate_port)
    if not tls:
        return await asyncio.start_server(handler, host, port)
    return await _serve_mux(handler, host, port, tls)


class _MuxServer:
    def __init__(self, sock, task):
        self.sock, self.task = sock, task

    def close(self):
        self.task.cancel()
        self.sock.close()


async def _serve_mux(handler, host, port, tls):
    """Plain HTTP and HTTPS on one port: a connection whose first byte is a TLS handshake (0x16) gets TLS.
    The mod sends the game's network test (normally HTTPS on 443) here, so the server needs no port
    below 1024 and no administrator rights. tls = (certfile, keyfile)."""
    import socket
    ctx = tls_context(*tls)
    loop = asyncio.get_running_loop()
    sock = socket.create_server((host, port), family=socket.AF_INET6 if ":" in host else socket.AF_INET, backlog=128)
    sock.setblocking(False)

    async def peek(conn, wait=15):
        """The connection's first byte, left in place. Waits on the loop: a connection that says nothing must
        not hold one of the worker threads the admin page's slower actions run on."""
        conn.setblocking(False)
        ready, fd = loop.create_future(), conn.fileno()
        loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
        try:
            await asyncio.wait_for(ready, wait)
            return conn.recv(1, socket.MSG_PEEK)
        except (asyncio.TimeoutError, OSError):
            return b""
        finally:
            loop.remove_reader(fd)

    async def one(conn):
        try:
            first = await peek(conn)
            if not first:
                conn.close()
                return
            reader = asyncio.StreamReader(limit=2 ** 16)
            proto = asyncio.StreamReaderProtocol(reader, handler)
            await loop.connect_accepted_socket(lambda: proto, conn, ssl=ctx if first == b"\x16" else None,
                                               ssl_handshake_timeout=10 if first == b"\x16" else None)
        except Exception as e:  # noqa: BLE001  (bad handshake, client gone)
            log.info("connection on :%d dropped: %r", port, e)
            conn.close()

    pending = set()

    async def accept_loop():
        while True:
            try:
                conn, _ = await loop.sock_accept(sock)
            except asyncio.CancelledError:
                raise
            except OSError as e:
                log.info("accept on :%d: %s", port, e)
                await asyncio.sleep(0.1)
                continue
            t = asyncio.create_task(one(conn))
            pending.add(t); t.add_done_callback(pending.discard)   # keep a reference until it's done

    return _MuxServer(sock, asyncio.create_task(accept_loop()))
