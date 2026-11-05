"""Zero-config networking: find the addresses players use, open the router's ports (UPnP IGD), and
pick the address to advertise per client.

The game is told the server's address in CMD_GET_SVRLIST / CMD_GET_URLLIST / CMD_GET_GAMERELAY. With no
fixed PUBLIC_IP the server answers each client with the address that client can reach:
  - client on a private network (LAN, Tailscale/ZeroTier, loopback): the local address it connected to;
  - client from the internet: the detected public (WAN) address.
"""
import ipaddress, logging, os, re, socket, threading, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("pesserver.net")

STATE = {"ready": False, "url": None, "lan": None, "wan": None, "upnp": None, "upnp_error": None, "cgnat": False, "mapped": []}

# The ports the game (and the /join page) need from the outside.
PORTS = [(80, "TCP"), (443, "TCP"), (8080, "TCP"), (10000, "TCP"), (3478, "UDP"), (7600, "UDP")]


def _ip(s):
    try:
        ip = ipaddress.ip_address(s)
        return getattr(ip, "ipv4_mapped", None) or ip
    except (ValueError, TypeError):
        return None


def is_public(s):
    ip = _ip(s)
    return bool(ip and ip.is_global)


def lan_ip():
    """The address of the interface that holds the default route (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def advertised_addr(svc, sess=None):
    """The server address to hand to this client (see the module docstring)."""
    fixed = os.environ.get("PESSERVER_PUBLIC_ADDR") or getattr(svc, "public_ip", None)
    if fixed:
        return fixed
    peer, local = getattr(sess, "peer_ip", None), getattr(sess, "local_ip", None)
    if local and _ip(local) and not _ip(local).is_unspecified and peer and not is_public(peer):
        return str(_ip(local))
    return STATE["wan"] or (local if local and is_public(local) else None) or STATE["lan"] or lan_ip()


# ---- UPnP IGD -----------------------------------------------------------------------------------
WAN_SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2",
                "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1")


class UPnPError(Exception):
    pass


def parse_ssdp_location(data: bytes):
    m = re.search(rb"(?im)^location:\s*(\S+)", data)
    return m.group(1).decode() if m else None


def parse_igd_description(xml_text, base_url):
    """-> (control_url, service_type) of the first WAN*Connection service, or None."""
    root = ET.fromstring(xml_text)
    for el in root.iter():
        if el.tag.endswith("service"):
            fields = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
            if fields.get("serviceType") in WAN_SERVICES and fields.get("controlURL"):
                return urllib.parse.urljoin(base_url, fields["controlURL"]), fields["serviceType"]
    return None


class Gateway:
    def __init__(self, control_url, service_type):
        self.control_url, self.service_type = control_url, service_type
        host = urllib.parse.urlsplit(control_url)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # our address on the router's network
        try:
            s.connect((host.hostname, host.port or 80))
            self.local_ip = s.getsockname()[0]
        finally:
            s.close()

    def _soap(self, action, args=()):
        inner = "".join(f"<{k}>{v}</{k}>" for k, v in args)
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{self.service_type}">{inner}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self.control_url, body.encode(), headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service_type}#{action}"'})
        try:
            return urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            code = re.search(r"<errorCode>(\d+)</errorCode>", text)
            desc = re.search(r"<errorDescription>([^<]*)</errorDescription>", text)
            raise UPnPError(f"{action}: {code.group(1) if code else e.code} {desc.group(1) if desc else ''}".strip())

    def external_ip(self):
        m = re.search(r"<NewExternalIPAddress>([^<]*)<", self._soap("GetExternalIPAddress"))
        return m.group(1) if m else None

    def add(self, port, proto, lease=7200, desc="PES2021 private server"):
        args = [("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", proto),
                ("NewInternalPort", port), ("NewInternalClient", self.local_ip), ("NewEnabled", 1),
                ("NewPortMappingDescription", desc), ("NewLeaseDuration", lease)]
        try:
            self._soap("AddPortMapping", args)
        except UPnPError as e:
            if str(e).startswith("AddPortMapping: 725"):          # OnlyPermanentLeasesSupported
                args[-1] = ("NewLeaseDuration", 0)
                self._soap("AddPortMapping", args)
            else:
                raise

    def delete(self, port, proto):
        self._soap("DeletePortMapping", [("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", proto)])


def discover_gateway(timeout=3.0):
    targets = ["urn:schemas-upnp-org:device:InternetGatewayDevice:2",
               "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
               "urn:schemas-upnp-org:service:WANIPConnection:1"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        for st in targets:
            s.sendto((f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
                      f"MX: 2\r\nST: {st}\r\n\r\n").encode(), ("239.255.255.250", 1900))
        seen = set()
        while True:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                return None
            loc = parse_ssdp_location(data)
            if not loc or loc in seen:
                continue
            seen.add(loc)
            try:
                found = parse_igd_description(urllib.request.urlopen(loc, timeout=3).read(), loc)
            except Exception as e:  # noqa: BLE001 - other UPnP devices (TVs, printers) answer too
                log.debug("UPnP device %s: %s", loc, e)
                continue
            if found:
                return Gateway(*found)
    finally:
        s.close()


def public_ip_from_web():
    """Ask a what-is-my-IP service (only used to tell a real public IP from carrier-grade NAT)."""
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            ip = urllib.request.urlopen(url, timeout=5).read().decode().strip()
            if is_public(ip):
                return ip
        except Exception:  # noqa: BLE001
            continue
    return None


def setup(use_upnp=True, renew_every=1800):
    """Detect LAN/WAN addresses and open the router ports. Blocking; call once at startup."""
    STATE["lan"] = lan_ip()
    web_ip = None if os.environ.get("PESSERVER_NO_IPCHECK") == "1" else public_ip_from_web()
    gw = None
    if use_upnp:
        try:
            gw = discover_gateway()
            if gw is None:
                STATE["upnp_error"] = "no UPnP router found (UPnP disabled on the router?)"
            else:
                ext = gw.external_ip()
                STATE["upnp"] = gw.control_url
                if ext and not is_public(ext):
                    STATE["cgnat"] = True           # the router itself is behind another NAT (ISP CGNAT)
                for port, proto in PORTS:
                    try:
                        gw.add(port, proto)
                        STATE["mapped"].append(f"{port}/{proto.lower()}")
                    except (UPnPError, OSError) as e:
                        log.warning("UPnP: could not open %s/%s: %s", port, proto, e)
                if ext and is_public(ext):
                    web_ip = web_ip or ext
                    if web_ip != ext:
                        STATE["cgnat"] = True
        except Exception as e:  # noqa: BLE001
            STATE["upnp_error"] = str(e)
            gw = None
    STATE["wan"] = web_ip
    threading.Thread(target=_watch_wan, args=(gw,), daemon=True).start()
    if gw and STATE["mapped"]:
        def renew():
            while not _stop.wait(renew_every):
                for port, proto in PORTS:
                    try:
                        gw.add(port, proto)
                    except Exception as e:  # noqa: BLE001
                        log.warning("UPnP renew %s/%s: %s", port, proto, e)
        threading.Thread(target=renew, daemon=True).start()
    STATE["_gw"] = gw
    return STATE


_stop = threading.Event()


def _watch_wan(gw, every=600):
    """Home IPs change: re-check the public address so new join lines and URL lists stay right."""
    while not _stop.wait(every):
        ip = None
        if os.environ.get("PESSERVER_NO_IPCHECK") != "1":
            ip = public_ip_from_web()
        if not ip and gw is not None:
            try:
                ext = gw.external_ip()
                ip = ext if is_public(ext) else None
            except Exception:  # noqa: BLE001
                ip = None
        if ip and ip != STATE["wan"]:
            log.warning("public IP changed: %s -> %s (players outside your network: run the join line again)",
                        STATE["wan"], ip)
            STATE["wan"] = ip


def teardown():
    _stop.set()
    gw = STATE.get("_gw")
    for item in STATE["mapped"]:
        port, proto = item.split("/")
        try:
            gw.delete(int(port), proto.upper())
        except Exception:  # noqa: BLE001
            pass


# ---- public web address (Cloudflare quick tunnel) ------------------------------------------------
async def run_tunnel(port, on_url=None):
    """Keep a free Cloudflare quick tunnel (https://<words>.trycloudflare.com -> http://127.0.0.1:port)
    running. No account needed. The URL changes when the tunnel restarts."""
    import asyncio, shutil
    exe = shutil.which("cloudflared")
    if not exe:
        STATE["url_error"] = "cloudflared not installed"
        return
    while True:
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            async for line in proc.stderr:
                m = re.search(rb"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and STATE.get("url") != m.group(0).decode():
                    STATE["url"] = m.group(0).decode()
                    log.info("public web address: %s", STATE["url"])
                    if on_url:
                        on_url(STATE["url"])
            await proc.wait()
            log.warning("tunnel exited (rc=%s); restarting in 30 s", proc.returncode)
        except Exception as e:  # noqa: BLE001
            log.warning("tunnel: %s", e)
        STATE["url"] = None
        await asyncio.sleep(30)


def game_addrs():
    """Addresses the game can use to reach this server, best first (the join scripts pick the first
    one that answers /status with our server id)."""
    pinned = os.environ.get("PESSERVER_PUBLIC_ADDR")
    if pinned:
        return [pinned]
    out = []
    for a in (STATE["lan"] or lan_ip(), STATE["wan"]):
        if a and a not in out:
            out.append(a)
    return out


def admin_password(data_dir):
    """ADMIN_PASSWORD, or a random one created once in <data>/admin_password."""
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw:
        return pw
    path = os.path.join(data_dir, "admin_password")
    try:
        return open(path).read().strip()
    except OSError:
        import secrets
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        pw = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        with open(path, "w") as f:
            f.write(pw + "\n")
        os.chmod(path, 0o600)
        return pw


def banner(password=None, color=True):
    """The startup screen (docker logs, install.sh)."""
    c = (lambda code, t: f"\033[{code}m{t}\033[0m") if color else (lambda code, t: t)
    B, D, G, C, Y = (lambda t: c("1", t)), (lambda t: c("2", t)), (lambda t: c("1;32", t)), \
                    (lambda t: c("1;36", t)), (lambda t: c("1;33", t))
    lan, wan, url = STATE["lan"] or lan_ip(), STATE["wan"], STATE.get("url")
    pinned = os.environ.get("PESSERVER_PUBLIC_ADDR")
    main = url or f"http://{pinned or wan or lan}"
    line = D("  " + "─" * 66)
    out = ["", line, f"   ⚽  {B('PES 2021 SERVER')}" + " " * 39 + G("● ONLINE"), line, "",
           f"   {B('Your server')}    {C(main)}", "",
           f"   {B('Send this to your friends')} {D('(they run it once)')}",
           f"     Windows       {C(f'irm {main}/join | iex')}",
           f"     Linux / Deck  {C(f'curl -fsSL {main}/join | bash')}"]
    if not url and not pinned and wan and wan != lan:
        out.append(D(f"     Same house?   use http://{lan} instead"))
    out += ["", f"   {B('Admin page')}     {C(main + '/admin')}" + (D(f"   (or http://{lan}/admin)") if url else "")]
    if password:
        out.append(f"   {B('Password')}       {Y(password)}")
    net = [f"LAN {lan}"] + ([f"Internet {wan}"] if wan else []) + ([f"fixed {pinned}"] if pinned else [])
    if STATE["mapped"]:
        net.append(G("router ports open ✓"))
    out += ["", f"   {B('Network')}        " + D(" · ").join(net)]
    if STATE["cgnat"]:
        out.append(Y("   !  Your internet provider shares one public IP (CGNAT): friends outside your house\n"
                     "      can't connect. Use Tailscale (see the README) or a cloud server."))
    elif wan and not STATE["mapped"] and not pinned:
        out.append(Y("   !  Router ports are closed") + D(f" ({STATE['upnp_error'] or 'UPnP off'}).\n"
                 f"      At home: enable UPnP on your router, or forward TCP 80,443,8080,10000 + UDP 3478,7600 to {lan}.\n"
                 "      On a cloud server this is normal: open those ports in the provider's firewall instead."))
    out += [line, ""]
    return "\n".join(out)
