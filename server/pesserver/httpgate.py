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
            log.info("HTTP %s %s hdr=%s body=%r", method, path, headers, body[:300])
            route = path.split("?", 1)[0].rstrip("/").lower()
            status = "200 OK"
            if route == "/admin" or route.startswith("/admin/api/"):
                status, out, ctype = await _admin(service, method, route, headers, body)
            elif method == "GET" and route in ("", "/join", "/leave", "/join.sh", "/join.ps1", "/leave.sh",
                                             "/leave.ps1", "/install-ca-proton.py", "/banner"):
                out, ctype = _web(service, route, headers, writer)
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
                         b"\r\nContent-Length: " + str(len(out)).encode() + b"\r\nConnection: close\r\n\r\n" + out)
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


async def _admin(service, method, route, headers, body):
    """Admin page (/admin) and its JSON API, protected by the admin password."""
    import hmac
    from . import events, netsetup
    if route == "/admin":
        return "200 OK", open(os.path.join(WEB, "admin.html"), "rb").read(), "text/html; charset=utf-8"
    pw = getattr(service, "admin_password", None)
    given = headers.get("authorization", "")[7:] if headers.get("authorization", "").startswith("Bearer ") else ""
    if not pw or not hmac.compare_digest(given.encode(), pw.encode()):
        await asyncio.sleep(1)                # slow down password guessing
        return "401 Unauthorized", b'{"error":"wrong password"}', "application/json"
    if route == "/admin/api/state":
        snap = events.snapshot(service)
        st = netsetup.STATE
        main = st.get("url") or "http://" + netsetup.game_addrs()[-1]
        snap["server"] = {"url": st.get("url"), "main": main, "lan": st["lan"], "wan": st["wan"],
                          "pinned": os.environ.get("PESSERVER_PUBLIC_ADDR"), "ports_open": st["mapped"],
                          "cgnat": st["cgnat"], "upnp_error": st["upnp_error"],
                          "join_windows": f"irm {main}/join | iex", "join_linux": f"curl -fsSL {main}/join | bash"}
        return "200 OK", json.dumps(snap).encode(), "application/json"
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
    host = headers.get("host", "").strip()
    if not host:
        from types import SimpleNamespace
        conn = SimpleNamespace(peer_ip=(writer.get_extra_info("peername") or ("",))[0],
                               local_ip=(writer.get_extra_info("sockname") or ("",))[0])
        host = netsetup.advertised_addr(service, conn)
    server = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
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
    scheme = "https" if headers.get("x-forwarded-proto") == "https" or host.endswith(".trycloudflare.com") else "http"
    addrs = netsetup.game_addrs()
    if netsetup._ip(server):                  # reached by IP: that address works for this player
        addrs = [server] + [a for a in addrs if a != server]
    text = (text.replace("@@SERVER@@", server).replace("@@BASE@@", f"{scheme}://{host}")
                .replace("@@ADDRS@@", " ".join(addrs)).replace("@@SID@@", server_id())
                .replace("@@UNINSTALL@@", "1" if "/leave" in route else "0"))
    return text.encode(), ctype


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
