"""HTTP side of the server: the game's web API (form-urlencoded POST with msgid=CMD_… → raw msgpack
reply), the NTL GateInfo.php text reply used by the network test, and /ca.crt for players.
Every request is logged in full (method, path, headers, body)."""
import asyncio, sys, json, logging, os

log = logging.getLogger("pesserver.http")


def make_handler(service, stun_host, stun_port, gate_port):
    async def on_conn(reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {k.lower(): v for k, v in (l.split(": ", 1) for l in lines[1:] if ": " in l)}
            body = b""
            if int(headers.get("content-length", 0)):
                body = await reader.readexactly(min(int(headers["content-length"]), 1 << 16))
            log.info("HTTP %s %s hdr=%s body=%r", method, path,
                     {k: ("<hidden>" if k == "authorization" else v) for k, v in headers.items()},
                     b"<hidden>" if path.startswith("/admin") else body[:300])
            route = path.split("?", 1)[0].rstrip("/").lower()
            status = "200 OK"
            extra = b""
            if route == "/admin" or route.startswith("/admin/api/"):
                peer = (writer.get_extra_info("peername") or ("",))[0]
                secure = writer.get_extra_info("ssl_object") is not None
                status, out, ctype = await _admin(service, method, route, headers, body, peer, secure)
                extra = (b"X-Frame-Options: DENY\r\nContent-Security-Policy: " + admin_csp().encode() + b"\r\n"
                         b"Referrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n"
                         b"Cross-Origin-Resource-Policy: same-origin\r\n"
                         b"Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()\r\n")
            elif method == "GET" and route in ("", "/join", "/leave", "/join.sh", "/join.ps1", "/leave.sh",
                                             "/leave.ps1", "/install-ca-proton.py", "/banner"):
                out, ctype = _web(service, route, headers, writer)
                if ctype.startswith("text/html"):       # the friends' page shares an origin with the admin page
                    extra = (b"Content-Security-Policy: " + page_csp(out.decode()).encode() + b"\r\n"
                             b"X-Frame-Options: DENY\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n")
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
                    with open(f, "rb") as fh:
                        out = fh.read()
                    ctype = "application/octet-stream"
                    log.info("Live Update %s sent to %s", os.path.basename(f), (writer.get_extra_info("peername") or ("",))[0])
                    if method == "GET":
                        from . import adminextra
                        try:
                            adminextra.note_liveupdate(service, (writer.get_extra_info("peername") or ("",))[0], os.path.basename(f))
                        except Exception:  # noqa: BLE001
                            log.exception("noting a Live Update download")
            elif path.startswith("/status"):
                from . import pesmatch
                from . import identity
                k = identity.public_hex()
                out = json.dumps({"id": server_id(), "key": k[:8] + " " + k[8:16],
                                  "mod_sha256": mod_sha256(mod_url(service, headers, writer)), "online": len(service.online),
                                  "queued": len(pesmatch._state(service)["queue"]),
                                  "rooms": len(service.mm.rooms)}).encode(); ctype = "application/json"
            elif path.rstrip("/") == "/ca.crt":
                # The private CA players must trust (NTL GateInfo.php runs over HTTPS with OS validation).
                ca = os.path.join(os.environ.get("PESSERVER_CERTS", "/data/certs"), "ca.crt")
                try:
                    out = open(ca, "rb").read(); ctype = "application/x-x509-ca-cert"
                except OSError:
                    out = b"no CA configured"; ctype = "text/plain"
            elif path.startswith("/admin/push") and os.environ.get("PESSERVER_ADMIN") == "1":  # dev hook
                from urllib.parse import parse_qs, urlsplit
                from . import pesmatch
                q = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
                out = json.dumps(pesmatch.admin_push(service, q)).encode(); ctype = "application/json"
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
            writer.write(b"HTTP/1.1 " + status.encode() + b"\r\nCache-Control: no-store\r\nContent-Type: " + ctype.encode() +
                         b"\r\nContent-Length: " + str(len(out)).encode() + b"\r\n" + extra + b"Connection: close\r\n\r\n"
                         + (b"" if method == "HEAD" else out))
            await writer.drain()
        except Exception as e:  # noqa
            peer = writer.get_extra_info("peername"); tls = writer.get_extra_info("ssl_object")
            log.info("HTTP%s conn from %s ended without a complete request: %r",
                     "S" if tls else "", peer, e)
        finally:
            writer.close()
    return on_conn


WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_SID = []


def server_id():
    """Short id of this server (from its CA), so join scripts can tell our server from another
    device at the same LAN address."""
    if not _SID:
        import hashlib
        ca = os.path.join(os.environ.get("PESSERVER_CERTS", "/data/certs"), "ca.crt")
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


def same_pc(peer):
    """The browser runs on the server's own computer (loopback, or this machine's own address)."""
    from . import netsetup
    ip = _peer_ip(peer)
    return bool(ip) and (ip.is_loopback or str(ip) == (netsetup.STATE.get("lan") or ""))


def admin_allowed(peer):
    """The admin page opens from this machine, the home network and Tailscale; from the internet
    only when the host switched that on."""
    return REMOTE["on"] or not from_internet(peer)


ADMIN_BLOCKED = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PES 2021 Admin</title><body style="font:16px/1.6 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1>Admin page: home network only</h1><p>For safety, the admin page only opens from the server's own network
(and Tailscale). Open it from a device on the same Wi-Fi as the server, using the address the server printed.</p>
<p>To open it from anywhere, the host turns on <b>Admin page from the internet</b> on the admin page (Network card).</p>
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


async def _admin(service, method, route, headers, body, peer="", secure=False):
    """Admin page (/admin) and its JSON API. The server's own PC signs in without a password; other devices
    use the password; from the internet only when switched on, and only over HTTPS."""
    import hmac, time as _time
    from . import events, netsetup
    if route == "/admin/api/info":            # public: lets the sign-in page warn before a password is typed
        return "200 OK", json.dumps({"remote": REMOTE["on"], "internet": from_internet(peer), "host_ok": bool(host_ok(headers)),
                                     "same_pc": same_pc(peer) and bool(host_ok(headers)),
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
        if req.get("local") and same_pc(peer) and headers.get("x-pes-local") == "1":
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
        snap["server"] = {"main": main, "version": VERSION, "update": updates.STATE["latest"],
                          "platform": sys.platform, "ports": netsetup.port_list(),
                          "data_dir": getattr(service, "data_dir", ""), "lan_url": netsetup.web_url(st["lan"] or ""), "lan": st["lan"], "wan": st["wan"],
                          "pinned": os.environ.get("PESSERVER_PUBLIC_ADDR"), "ports_open": st["mapped"],
                          "cgnat": st["cgnat"], "upnp_error": st["upnp_error"],
                          "remote_admin": REMOTE["on"], "remote_url": f"https://{st['wan']}:{os.environ.get('PESSERVER_HTTP_PORT', '8080')}/admin" if st["wan"] else "",
                          "admin_fp": _admin_fp(service), "same_pc": same_pc(peer), "local_url": netsetup.local_admin_url(),
                          "link_home_only": not reachable,
                          "sessions": len([1 for v in _sessions(service).s.values() if v > now]),
                          "key": (lambda k: k[:8] + " " + k[8:16])(__import__("pesserver.identity", fromlist=["x"]).public_hex()),
                          "mod_sha256": mod_sha256(mod_url(service, headers, None)), "has_mod": os.path.isfile(MOD_DLL)}
        from . import announce, adminextra
        snap["announce"] = announce.snapshot(service)
        adminextra.augment(service, snap)
        snap["server"].update(adminextra.server(service))
        snap["modes"] = [r[0] for r in service.accounts.db.execute(
            "SELECT DISTINCT COALESCE(mode, 'Quick Match') FROM pes_matches")]
        return "200 OK", json.dumps(snap).encode(), "application/json"
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
    if route in ("/admin/api/message/player", "/admin/api/players/message") and method == "POST":
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
                events.feed(service, f"The message to {name} was taken back", "msg")
            return "200 OK", json.dumps(announce.snapshot(service)).encode(), "application/json"
        try:
            announce.tell(service, pid, req.get("title"), req.get("body"), kind="note")
        except announce.TooLong:
            return "400 Bad Request", b'{"error":"too long"}', "application/json"
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"Write a title and a message."}', "application/json"
        events.feed(service, f"Message sent to {name}: “{announce.clean(req.get('title'), announce.TITLE_MAX, False)}”", "msg")
        return "200 OK", json.dumps(announce.snapshot(service)).encode(), "application/json"
    if route == "/admin/api/games":
        from . import gamesetup
        return "200 OK", json.dumps(gamesetup.status()).encode(), "application/json"
    if route in ("/admin/api/games/install", "/admin/api/games/remove") and method == "POST":
        from . import gamesetup
        gid = str(json.loads(body or b"{}").get("id", ""))
        try:
            if route.endswith("install"):
                if not os.path.isfile(MOD_DLL):
                    return "501 Not Implemented", b'{"error":"This server was built without the mod."}', "application/json"
                result = gamesetup.install(gid, mod_bytes(mod_url(service, headers, None)))
                verb = "installed in"
            else:
                result = gamesetup.remove(gid)
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
        events.feed(service, f"{events.name_of(service, pid)} was kicked by the admin", "warn")
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
        from . import adminextra
        p.update(adminextra.player(service, pid))
        return "200 OK", json.dumps(p).encode(), "application/json"
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
        events.feed(service, f"The admin cleared {events.name_of(service, pid)}'s courtesy record", "info")
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/players/delete" and method == "POST":
        try:
            pid = int(json.loads(body or b"{}").get("pid"))
        except (ValueError, TypeError, AttributeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        from . import announce, pescmds
        name = pescmds.delete_player(service, pid)
        if name is None:
            return "404 Not Found", b'{"error":"That player is already gone."}', "application/json"
        sess = service.online.get(pid)
        if sess is not None:
            sess.kicked = True          # closing it below is not an early exit (no forfeit, no courtesy mark)
        announce.forget(service, pid)
        from . import pescoop, pesmanner, adminextra
        pesmanner.forget(service, pid)
        pescoop.forget(service, pid)
        adminextra.forget(service, pid)
        from . import pesavatar, pesblock, pesprofile, pesresults
        pesresults.forget(service, pid)
        pesavatar.forget(service, pid)
        pesprofile.forget(service, pid)
        pesblock.forget(service, pid)
        from . import pesdivisions, pesmatchday, pestourney
        pesdivisions.forget(service, pid)
        pesmatchday.forget(service, pid)
        pestourney.forget(service, pid)
        if sess is not None and hasattr(sess, "close"):
            sess.close()                # their next login registers them again (name prompt)
        events.feed(service, f"{name} was deleted by the admin", "warn")
        return "200 OK", b'{"ok":true}', "application/json"
    if route.startswith("/admin/api/tourneys"):
        from . import pestourney                    # Online Tournament competitions (list, save, end, delete)
        return pestourney.admin_api(service, route, method, body)
    return "404 Not Found", b'{"error":"unknown"}', "application/json"


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

    def peek(conn):                           # in a thread: works on every event loop (Windows too)
        conn.settimeout(15)
        try:
            return conn.recv(1, socket.MSG_PEEK)
        except OSError:
            return b""
        finally:
            conn.setblocking(False)

    async def one(conn):
        try:
            first = await asyncio.to_thread(peek, conn)
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
