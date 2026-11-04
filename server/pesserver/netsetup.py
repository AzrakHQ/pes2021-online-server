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

STATE = {"ready": False, "lan": None, "wan": None, "upnp": None, "upnp_error": None, "cgnat": False, "mapped": []}

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


def teardown():
    _stop.set()
    gw = STATE.get("_gw")
    for item in STATE["mapped"]:
        port, proto = item.split("/")
        try:
            gw.delete(int(port), proto.upper())
        except Exception:  # noqa: BLE001
            pass


def banner(http_port=8080):
    """Human-readable 'how to join' text (startup log, /banner, install.sh)."""
    lan, wan = STATE["lan"] or lan_ip(), STATE["wan"]
    j = lambda a: (f"   Windows (PowerShell):    irm http://{a}/join | iex\n"
                   f"   Linux / Steam Deck:      curl -fsSL http://{a}/join | bash\n")
    out = ["", "=" * 72, " PES 2021 private server is running", "=" * 72,
           f" Players on your network ({lan}):", j(lan)]
    if os.environ.get("PESSERVER_PUBLIC_ADDR"):
        a = os.environ["PESSERVER_PUBLIC_ADDR"]
        if a != lan:
            out += [f" Players using {a}:", j(a)]
    elif wan:
        out += [f" Players over the internet ({wan}):", j(wan)]
        if STATE["cgnat"]:
            out.append(" !  Your internet provider shares one public IP between customers (CGNAT), so\n"
                       "    internet players can't reach you directly. Use a VPS, or Tailscale/ZeroTier.")
        elif STATE["mapped"]:
            out.append(f"    Router ports opened automatically (UPnP): {', '.join(STATE['mapped'])}")
        else:
            out.append(f"    Router ports were NOT opened ({STATE['upnp_error'] or 'UPnP off'}).\n"
                       "    For internet play, forward TCP 80, 443, 8080, 10000 and UDP 3478, 7600 to "
                       f"{lan}.")
    out += [" Players run one of these once, then start PES 2021 -> eFootball -> Quick Match.",
            " The same page is at http://<address>/ in a browser.", "=" * 72, ""]
    return "\n".join(out)
