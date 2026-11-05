"""HTTP side of the server: the game's web API (form-urlencoded POST with msgid=CMD_… → raw msgpack
reply), the NTL GateInfo.php text reply used by the network test, and /ca.crt for players.
Every request is logged in full (method, path, headers, body)."""
import asyncio, json, logging, os

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
                status, out, ctype = await _admin(service, method, route, headers, body, peer)
            elif method == "GET" and route in ("", "/join", "/leave", "/join.sh", "/join.ps1", "/leave.sh",
                                             "/leave.ps1", "/install-ca-proton.py", "/banner"):
                out, ctype = _web(service, route, headers, writer)
            elif method == "GET" and route in ("/mod/note", "/mod/hello", "/mod/dinput8.dll"):
                status, out, ctype, extra = _mod(service, route, headers, writer, path)
            elif path.startswith("/status"):
                from . import pesmatch
                out = json.dumps({"id": server_id(), "online": len(service.online),
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
                resp = pescmds.dispatch(service, None, msgid, form)
                resp = {"msgid": msgid, "rqid": int(form.get("rqid", 0) or 0), **resp}
                import msgpack
                ctype = "application/octet-stream"   # raw msgpack map (CmdAuthSteamResult parse 0x141e49160)
                out = msgpack.packb(resp, use_bin_type=True, unicode_errors="surrogateescape")
                record("tx", msgid, resp)
                log.info("HTTP API %s -> %s", msgid, resp)
            elif path.lower().lstrip("/").startswith(("eula", "u")):
                out = (b"<html><body><h3>Local PES 2021 server</h3><p>This is a private, local "
                       b"preservation server for PES 2021 online play.</p></body></html>")
                ctype = "text/html; charset=utf-8"
            else:
                out = b"ok"; ctype = "text/plain"
            writer.write(b"HTTP/1.1 " + status.encode() + b"\r\nCache-Control: no-store\r\nContent-Type: " + ctype.encode() +
                         b"\r\nContent-Length: " + str(len(out)).encode() + b"\r\n" + extra + b"Connection: close\r\n\r\n" + out)
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


def _sessions(service):
    if not hasattr(service, "_admin_sessions"):
        service._admin_sessions = AdminSessions(getattr(service, "data_dir", None))
    return service._admin_sessions


def admin_allowed(peer):
    """The admin page opens only from the server's own networks: home/LAN (private ranges),
    Tailscale (100.64.0.0/10), this machine. ADMIN_REMOTE=1 allows it from anywhere (cloud servers
    behind NAT); a machine whose own address is public (a cloud server) allows it automatically."""
    import ipaddress
    from . import netsetup
    if os.environ.get("ADMIN_REMOTE") == "1":
        return True
    try:
        ip = ipaddress.ip_address(peer.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
    except ValueError:
        return False
    if not ip.is_global:                      # home/LAN, Tailscale's 100.64.0.0/10, loopback, link-local
        return True
    lan = netsetup.STATE.get("lan")
    try:
        return bool(lan) and ipaddress.ip_address(lan).is_global
    except ValueError:
        return False


ADMIN_BLOCKED = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>PES 2021 Admin</title><body style="font:16px/1.6 system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px">
<h1>Admin page: home network only</h1><p>For safety, the admin page only opens from the server's own network
(and Tailscale). Open it from a device on the same Wi-Fi as the server, using the address the server printed.</p>
<p>Hosting on a cloud server? Run the install line with <code>ADMIN_REMOTE=1</code> before <code>sh</code>.</p></body>"""


async def _admin(service, method, route, headers, body, peer=""):
    """Admin page (/admin) and its JSON API, protected by the admin password."""
    import hmac, time as _time
    from . import events, netsetup
    if not admin_allowed(peer):
        if route == "/admin":
            return "403 Forbidden", ADMIN_BLOCKED, "text/html; charset=utf-8"
        return "403 Forbidden", b'{"error":"home network only"}', "application/json"
    if route == "/admin":
        return "200 OK", open(os.path.join(WEB, "admin.html"), "rb").read(), "text/html; charset=utf-8"
    now = _time.time()
    if route == "/admin/api/login" and method == "POST":
        recent = [t for t in _FAILED.get(peer, []) if now - t < 60]
        _FAILED[peer] = recent
        if len(recent) >= 5:
            return "429 Too Many Requests", b'{"error":"too many wrong passwords; wait a minute"}', "application/json"
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            req = {}
        pw = getattr(service, "admin_password", None)
        if not pw or not hmac.compare_digest(str(req.get("password", "")).encode(), pw.encode()):
            recent.append(now)
            await asyncio.sleep(1)            # slow down password guessing
            return "401 Unauthorized", b'{"error":"wrong password"}', "application/json"
        token = _sessions(service).new(bool(req.get("remember")))
        return "200 OK", json.dumps({"token": token}).encode(), "application/json"
    given = headers.get("authorization", "")[7:] if headers.get("authorization", "").startswith("Bearer ") else ""
    if not _sessions(service).valid(given):
        return "401 Unauthorized", b'{"error":"signed out"}', "application/json"
    if route == "/admin/api/logout" and method == "POST":
        _sessions(service).revoke(given)
        return "200 OK", b'{"ok":true}', "application/json"
    if route == "/admin/api/state":
        snap = events.snapshot(service)
        st = netsetup.STATE
        main = "http://" + netsetup.game_addrs()[-1]
        snap["server"] = {"main": main, "lan": st["lan"], "wan": st["wan"],
                          "pinned": os.environ.get("PESSERVER_PUBLIC_ADDR"), "ports_open": st["mapped"],
                          "cgnat": st["cgnat"], "upnp_error": st["upnp_error"],
                          "remote_admin": admin_allowed("8.8.8.8")}
        return "200 OK", json.dumps(snap).encode(), "application/json"
    if route == "/admin/api/games":
        from . import gamesetup
        return "200 OK", json.dumps(gamesetup.status()).encode(), "application/json"
    if route in ("/admin/api/games/install", "/admin/api/games/remove") and method == "POST":
        from . import gamesetup
        gid = str(json.loads(body or b"{}").get("id", ""))
        try:
            if route.endswith("install"):
                if not os.path.isfile(MOD_DLL):
                    return "404 Not Found", b'{"error":"This server was built without the mod."}', "application/json"
                result = gamesetup.install(gid, mod_bytes())
                events.feed(service, "The mod was installed in this PC's game", "info")
            else:
                result = gamesetup.remove(gid)
        except LookupError:
            return "404 Not Found", b'{"error":"That game folder is gone. Run the install command again to look for PES 2021."}', "application/json"
        except PermissionError as e:
            msg = str(e) if "dinput8.dll" in str(e) and "Errno" not in str(e) else \
                "Couldn't write to the game folder. Close the game and try again."
            return "409 Conflict", json.dumps({"error": msg}).encode(), "application/json"
        except OSError:
            log.exception("installing the mod")
            return "409 Conflict", b'{"error":"Couldn\'t write to the game folder. Close the game and try again."}', "application/json"
        return "200 OK", json.dumps(result).encode(), "application/json"
    if route == "/admin/api/kick" and method == "POST":
        try:
            pid = int(json.loads(body or b"{}").get("pid"))
        except (ValueError, TypeError):
            return "400 Bad Request", b'{"error":"pid"}', "application/json"
        sess = service.online.get(pid)
        if sess is None or not hasattr(sess, "close"):
            return "404 Not Found", b'{"error":"not online"}', "application/json"
        events.feed(service, f"{events.name_of(service, pid)} was kicked by the admin", "warn")
        sess.close()
        return "200 OK", b'{"ok":true}', "application/json"
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
    if not host:
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


MOD_DLL = os.environ.get("PESSERVER_MOD_DLL", "/app/mod/dinput8.dll")


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
        nonce = parse_qs(urlsplit(path).query).get("n", [""])[0]
        try:
            return "200 OK", identity.hello(nonce).encode(), "text/plain; charset=utf-8", b""
        except ValueError:
            return "400 Bad Request", b"bad nonce", "text/plain", b""
    if not os.path.isfile(MOD_DLL):
        return "404 Not Found", b"This server was built without the mod.", "text/plain", b""
    url = f"{scheme}://{host}" if not netsetup._ip(server) else ""   # a name (e.g. dynamic DNS) outlives IP changes
    return "200 OK", mod_bytes(url), "application/octet-stream", b'Content-Disposition: attachment; filename="dinput8.dll"\r\n'


def mod_bytes(url=""):
    """The mod DLL with this server's identity and addresses written in."""
    from . import identity, netsetup
    settings = identity.mod_settings(os.environ.get("SERVER_NAME", "PES 2021 server"), url, netsetup.game_addrs())
    return identity.pack_mod(open(MOD_DLL, "rb").read(), settings)


async def serve_tls(service, host, port, certfile, keyfile, stun_host="127.0.0.1", stun_port=3478, gate_port=10000):
    """Same handler over TLS (the client's port-443 traffic is remapped here by netshim)."""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)

    def sni(sslobj, name, _ctx):
        log.info("TLS ClientHello SNI=%s", name)
    ctx.sni_callback = sni

    def on_handshake_error(loop, context):
        log.info("TLS handshake/stream error: %s %r", context.get("message"), context.get("exception"))
    asyncio.get_event_loop().set_exception_handler(on_handshake_error)
    return await asyncio.start_server(make_handler(service, stun_host, stun_port, gate_port), host, port,
                                      ssl=ctx, ssl_handshake_timeout=10)


async def serve(service, host="0.0.0.0", port=8080, stun_host="127.0.0.1", stun_port=3478, gate_port=10000):
    return await asyncio.start_server(make_handler(service, stun_host, stun_port, gate_port), host, port)
