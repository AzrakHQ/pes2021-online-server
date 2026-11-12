"""Zero-config networking: find the addresses players use, open the router's ports (UPnP IGD), and
pick the address to advertise per client.

The game is told the server's address in CMD_GET_SVRLIST / CMD_GET_URLLIST / CMD_GET_GAMERELAY. With no
fixed PUBLIC_IP the server answers each client with the address that client can reach:
  - client on a private network (LAN, Tailscale/ZeroTier, loopback): the local address it connected to;
  - client from the internet: the detected public (WAN) address.
"""
import ipaddress, logging, os, re, socket, threading, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("pesserver.net")

STATE = {"ready": False, "lan": None, "wan": None, "upnp": None, "upnp_error": None, "cgnat": False, "mapped": []}

# The ports the game (and the /join page) need from the outside.
PORTS = [(80, "TCP"), (443, "TCP"), (8080, "TCP"), (10000, "TCP"), (3478, "UDP"), (7600, "UDP")]
WEB_PORT = 80          # where the player pages and the admin page are; 8080 when port 80 isn't available
NOTES = []             # extra warnings for the banner (e.g. a port the server couldn't open)


def web_url(addr):
    return f"http://{addr}" + ("" if WEB_PORT == 80 else f":{WEB_PORT}")


def port_list():
    """'TCP 8080,10000 + UDP 3478,7600': the ports this server listens on, for forwarding instructions."""
    def fmt(ports):
        out, ps = [], sorted(set(ports))
        while ps:
            a = b = ps.pop(0)
            while ps and ps[0] == b + 1:
                b = ps.pop(0)
            out.append(str(a) if a == b else f"{a}-{b}")
        return ",".join(out)
    all_ports = _ports()
    tcp = fmt([p for p, proto in all_ports if proto == "TCP"])
    udp = fmt([p for p, proto in all_ports if proto == "UDP"])
    return f"TCP {tcp} + UDP {udp}"


def local_admin_url():
    """The admin page on the server's own PC (signs in without a password)."""
    return ("http://localhost" + ("" if WEB_PORT == 80 else f":{WEB_PORT}")) + "/admin"


def skip_port(port):
    """The server isn't listening there (no rights for a low port): don't ask the router for it."""
    global PORTS
    PORTS = [(p, proto) for p, proto in PORTS if p != port]


def _ports():
    from . import turn
    return PORTS + turn.ports()


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
            if not loc or loc in seen or not loc.lower().startswith("http://"):
                continue                                # (any device at home can answer: only a plain web address)
            seen.add(loc)
            try:
                found = parse_igd_description(urllib.request.urlopen(loc, timeout=3).read(1 << 18), loc)
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


ON_CHANGE = []         # called (from the keeper thread) after this computer's or the public address changed
_stop = threading.Event()


def _open_ports(gw):
    """Ask the router for every port; returns the ones it opened ("8080/tcp"). OSError: the router is gone."""
    got = []
    for port, proto in _ports():
        try:
            gw.add(port, proto)
            got.append(f"{port}/{proto.lower()}")
        except UPnPError as e:
            log.warning("UPnP: could not open %s/%s: %s", port, proto, e)
    return got


def _find_router():
    """Find the UPnP router and open the ports (STATE: upnp, upnp_error, cgnat, mapped, _gw).
    Returns the router's public address, or None."""
    try:
        gw = discover_gateway()
        if gw is None:
            STATE["upnp_error"] = "no UPnP router found (UPnP disabled on the router?)"
            return None
        ext = gw.external_ip()
        STATE.update(upnp=gw.control_url, upnp_error=None, _gw=gw)
        STATE["cgnat"] = bool(ext and not is_public(ext))    # the router itself is behind another NAT (ISP CGNAT)
        STATE["mapped"] = _open_ports(gw)
        return ext if ext and is_public(ext) else None
    except Exception as e:  # noqa: BLE001
        STATE.update(upnp_error=str(e), _gw=None)
        return None


def _look(use_upnp):
    """Read the addresses and (re)open the router's ports."""
    STATE["lan"] = lan_ip()
    web_ip = None if os.environ.get("PESSERVER_NO_IPCHECK") == "1" else public_ip_from_web()
    if use_upnp:
        ext = _find_router()
        if ext:
            web_ip = web_ip or ext
            if web_ip != ext:
                STATE["cgnat"] = True
    STATE["wan"] = web_ip or STATE["wan"]


def setup(use_upnp=True, renew_every=1800, wait_for_net=30):
    """Detect LAN/WAN addresses and open the router ports. Blocking; call once at startup. A keeper thread then
    follows changes: a network that comes up later, a new address, a router that was restarted."""
    waited = 0
    while lan_ip() == "127.0.0.1" and waited < wait_for_net and not _stop.wait(2):
        waited += 2                                     # started at login, before the Wi-Fi: give it a moment
    if waited:
        log.info("waited %d s for the network: %s", waited, "up" if lan_ip() != "127.0.0.1" else "still down")
    STATE.setdefault("_gw", None)
    _look(use_upnp)
    threading.Thread(target=_keep, args=(use_upnp, renew_every), daemon=True).start()
    return STATE


def _changed():
    for fn in list(ON_CHANGE):
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("address change handler")


def _tick(use_upnp, due):
    """One round of the keeper. `due` = {"renew": bool, "wan": bool, "find": bool}: what is due by the clock."""
    lan, was = lan_ip(), (STATE["lan"], STATE["wan"])
    if lan != STATE["lan"] and (lan != "127.0.0.1" or STATE["lan"] is None):
        log.warning("this computer's address changed: %s -> %s", STATE["lan"], lan)
        _look(use_upnp)                                 # another network: its router, its public address
    else:
        gw = STATE.get("_gw")
        if use_upnp and gw is None and due["find"] and lan != "127.0.0.1":
            ext = _find_router()                        # no router found so far (it, or the network, was still starting)
            if ext and not STATE["wan"]:
                STATE["wan"] = ext
            if STATE["mapped"]:
                log.info("UPnP: router found, ports open: %s", ", ".join(STATE["mapped"]))
        elif use_upnp and gw is not None and due["renew"]:
            try:
                STATE["mapped"] = _open_ports(gw)       # (one that failed at first may open now)
            except OSError as e:                        # the router is gone or was replaced: look for it again
                log.warning("UPnP renew: %s", e)
                STATE["_gw"] = None
        if due["wan"]:
            ip = None
            if os.environ.get("PESSERVER_NO_IPCHECK") != "1":
                ip = public_ip_from_web()
            gw = STATE.get("_gw")
            if not ip and gw is not None:
                try:
                    ext = gw.external_ip()
                    ip = ext if is_public(ext) else None
                except Exception:  # noqa: BLE001
                    ip = None
            if ip and ip != STATE["wan"]:
                log.warning("public IP changed: %s -> %s (friends outside your network: send them a fresh mod file from the admin page)",
                            STATE["wan"], ip)
                STATE["wan"] = ip
    if (STATE["lan"], STATE["wan"]) != was:
        _changed()


def _keep(use_upnp, renew_every=1800, wan_every=600, tick=60):
    """Home networks change under a server that runs for weeks: follow them."""
    last = {"renew": time.monotonic(), "wan": time.monotonic(), "find": time.monotonic()}
    started = time.monotonic()
    while not _stop.wait(tick):
        now = time.monotonic()
        find_every = tick if now - started < 600 else wan_every      # look often at first, then now and then
        every = {"renew": renew_every, "wan": wan_every, "find": find_every}
        due = {k: now - last[k] >= every[k] - 1 for k in last}
        try:
            _tick(use_upnp, due)
        except Exception:  # noqa: BLE001 - the keeper must outlive one bad round
            log.exception("network check")
        for k in due:
            if due[k]:
                last[k] = now


def teardown(deadline=10):
    """Close the router's ports again. A router that doesn't answer is given up on at once, and the whole thing
    takes at most `deadline` seconds, so stopping or restarting the server never hangs on it."""
    _stop.set()
    gw, end = STATE.get("_gw"), time.monotonic() + deadline
    if gw is None:
        return
    for port, proto in _ports():                        # every port: one that failed at first may have opened later
        if time.monotonic() > end:
            log.warning("UPnP: closing the router's ports took too long; the rest stay open until their lease ends")
            return
        try:
            gw.delete(port, proto)
        except UPnPError:
            pass                                        # (it wasn't open)
        except Exception as e:  # noqa: BLE001
            log.warning("UPnP: the router doesn't answer (%s); its ports stay open until their lease ends", e)
            return


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


EXAMPLE_PASSWORDS = {"choose-a-long-password"}
PASSWORD_IGNORED = []                           # non-empty when ADMIN_PASSWORD was refused


def admin_password(data_dir):
    """ADMIN_PASSWORD, or a random one created once in <data>/admin_password. The website's example
    value and anything under 10 characters are refused (the admin page may face the internet)."""
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw and (pw in EXAMPLE_PASSWORDS or len(pw) < 10):
        log.warning("ADMIN_PASSWORD is the example or shorter than 10 characters; using a generated password")
        PASSWORD_IGNORED.append(True)
        pw = None
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
    """The startup screen, shown in the server's window."""
    c = (lambda code, t: f"\033[{code}m{t}\033[0m") if color else (lambda code, t: t)
    B, D, G, C, Y = (lambda t: c("1", t)), (lambda t: c("2", t)), (lambda t: c("1;32", t)), \
                    (lambda t: c("1;36", t)), (lambda t: c("1;33", t))
    lan, wan = STATE["lan"] or lan_ip(), STATE["wan"]
    pinned = os.environ.get("PESSERVER_PUBLIC_ADDR")
    here = pinned or lan
    line = D("  " + "─" * 66)
    from . import VERSION, httpgate
    port = os.environ.get("PESSERVER_HTTP_PORT", "8080")
    out = ["", line, f"   ⚽  {B('PES 2021 SERVER')}" + " " * 39 + G("● ONLINE"),
           D(f"       version {VERSION}"), line, "",
           f"   {B('Admin page')}     {C(local_admin_url())}" + D("   (this PC, no password)"),
           f"   {B('Other devices')}  {C(web_url(here) + '/admin')}"]
    if wan and httpgate.REMOTE["on"]:
        out.append(f"   {B('Internet')}       {C(f'https://{wan}:{port}/admin')}")
    if password:
        out.append(f"   {B('Password')}       {Y(password)}" + D("   (other devices)"))
    out += ["", D("   Open it to invite your friends (Invite friends) and to update or stop the server."), ""]
    if PASSWORD_IGNORED:
        out.append(Y("   !  ADMIN_PASSWORD was the example or too short, so this generated password is used instead."))
    from . import certs
    for note in NOTES:
        out.append(Y("   !  " + note))
    if certs.CA_REPLACED:
        out.append(Y("   !  New server certificate: players set up with the old join line run it once more (mod users: nothing to do)."))
    if STATE["cgnat"]:
        out.append(Y("   !  Your internet provider shares one public IP (CGNAT): friends outside your house\n"
                     "      can't connect. Use Tailscale (see the README)."))
    elif wan and not STATE["mapped"] and not pinned:
        out.append(Y("   !  Router ports are closed") + D(f" ({STATE['upnp_error'] or 'UPnP off'}).\n"
                 f"      Enable UPnP on your router, or forward {port_list()} to {lan}."))
    out += [line, ""]
    return "\n".join(out)
