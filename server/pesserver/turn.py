"""TURN relay fallback: the built-in relay (turnrelay.py) plus the two commands that hand it to the game.

The game only relays when CMD_GET_TURN_SERVER_LIST returns a server (strategy factory 0x1411aa730:
empty list -> direct hole punching only). Its TURN client (Konami "ktc", RFC 5766) connects over TLS/TCP
to `tls_port`, logs in with `id`/`pw` and trusts the DER certificate given in `server_certificate`.
Each game then sends its relayed address with CMD_SEND_TURN_ADDRESS_DATA; we push it to the opponent as
CMD_WATCH_TURN_ADDRESS_DATA. Details: notes/re/functions/net.md.

Off unless TURN=1 (it is unconfirmed against the real game). Credentials use the TURN REST scheme:
username "<expiry>:<pid>", password base64(HMAC-SHA1(secret, username)).
"""
import base64, datetime, ipaddress, logging, os, time

from .pescmds import NOERR, cmd

log = logging.getLogger("pesserver.turn")

TLS_PORT = int(os.environ.get("TURN_TLS_PORT", "5349"))
PLAIN_PORT = int(os.environ.get("TURN_PORT", "3479"))         # 3478 is our STUN; must be non-zero, unused
DTLS_PORT = int(os.environ.get("TURN_DTLS_PORT", "5350"))     # UDP: the game's second leg, TURN over DTLS
RELAY_PORTS = (int(os.environ.get("TURN_MIN_PORT", "49160")), int(os.environ.get("TURN_MAX_PORT", "49179")))
REALM = "pes2021"
CRED_TTL = 12 * 3600          # refreshes during a long session re-check the username, so hours, not 60 s
STATE = {"server": None, "dir": None, "secret": None, "cert_der": None, "ips": ()}


def enabled():
    return os.environ.get("TURN") == "1"


def ports():
    """Router ports to open when TURN runs: the TLS listener, the DTLS leg and the UDP relay range."""
    if not enabled():
        return []
    return [(TLS_PORT, "TCP"), (DTLS_PORT, "UDP")] + [(p, "UDP") for p in range(RELAY_PORTS[0], RELAY_PORTS[1] + 1)]


def running():
    return STATE["server"] is not None


# ---- setup -----------------------------------------------------------------------------------------
def _secret(d):
    path = os.path.join(d, "secret")
    try:
        s = open(path).read().strip()
        if s:
            return s
    except OSError:
        pass
    s = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(s)
    return s


def _cert(d, ips):
    """Self-signed certificate naming every address players may use. The game adds it as a trust
    anchor; whether it also checks the name is unknown, so it is re-issued when the addresses change."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    crt, key = os.path.join(d, "turn.crt"), os.path.join(d, "turn.key")
    want = sorted(set(ips))
    try:
        c = x509.load_pem_x509_certificate(open(crt, "rb").read())
        have = sorted(str(i) for i in c.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                      .value.get_values_for_type(x509.IPAddress))
        if have == want and c.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30):
            return c.public_bytes(serialization.Encoding.DER)
    except (OSError, ValueError, x509.ExtensionNotFound):
        pass
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PES2021 private server TURN")])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=3650))
         .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(i)) for i in want]), False)
         .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
         .sign(k, hashes.SHA256()))
    with open(os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()))
    with open(crt, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    log.info("TURN certificate issued for %s", ", ".join(want))
    return c.public_bytes(serialization.Encoding.DER)


async def start(svc, data_dir, lan, wan, extra_ips=()):
    """Start the built-in TURN relay when TURN=1. Returns True if it runs; the server works without it."""
    if not enabled():
        return False
    from . import turnrelay
    from .netsetup import advertised_addr
    d = os.path.join(data_dir, "turn")
    os.makedirs(d, exist_ok=True)
    ips = [i for i in (lan, wan, *extra_ips, "127.0.0.1") if i and _is_ip(i)]
    STATE.update(dir=d, secret=_secret(d), cert_der=_cert(d, ips), ips=tuple(sorted(set(ips))))

    def advertise(local_ip, peer_ip):
        # The relayed address each game is told: the same address it already uses to reach this server.
        class _S:
            pass
        s = _S(); s.local_ip, s.peer_ip = local_ip, peer_ip
        return advertised_addr(svc, s)

    srv = turnrelay.TurnServer(STATE["secret"], REALM, RELAY_PORTS, advertise, own_ips=ips)
    try:
        await srv.start("0.0.0.0", TLS_PORT, os.path.join(d, "turn.crt"), os.path.join(d, "turn.key"),
                        dtls_port=DTLS_PORT)
    except OSError as e:
        log.error("TURN relay could not listen on tcp/%d or udp/%d: %s", TLS_PORT, DTLS_PORT, e)
        srv.close()
        return False
    STATE["server"] = srv
    log.info("TURN relay on tls/%d + dtls/%s (relay udp %d-%d)", TLS_PORT, srv.dtls_port or "off", *RELAY_PORTS)
    return True


def stop():
    srv, STATE["server"] = STATE["server"], None
    if srv is not None:
        srv.close()


def _is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def credentials(pid, now=None):
    from .turnrelay import rest_password
    user = f"{int((now or time.time()) + CRED_TTL)}:{pid}"
    return user, rest_password(STATE["secret"], user)


# ---- commands --------------------------------------------------------------------------------------
@cmd("CMD_GET_TURN_SERVER_LIST")
def get_turn_server_list(svc, sess, req):
    # HTTP web API, twice per match per game. Request: pid, turn_account_param (= pid), turn_time_limit 60.
    # An entry is dropped unless port and tls_port are both non-zero; only tls_port is used.
    if not running():
        return {"result": NOERR}                      # empty list: direct connection only, as before
    from .netsetup import advertised_addr
    try:
        pid = int(req.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    addr = advertised_addr(svc, svc.online.get(pid) if pid else sess)
    user, pw = credentials(pid)
    der = STATE["cert_der"]
    log.info("TURN list -> pid %s: %s:%d", pid, addr, TLS_PORT)
    return {"result": NOERR, "turn_server_list": [{"zone": 1, "address": addr, "port": PLAIN_PORT, "tls_port": TLS_PORT}],
            "id": user, "pw": pw, "server_certificate": base64.b64encode(der).decode(),
            "server_certificate_data_size": len(der)}


@cmd("CMD_SEND_TURN_ADDRESS_DATA")
def send_turn_address_data(svc, sess, req):
    """Forward a game's relayed address to its opponent(s), byte for byte. The opponent's watch exists
    from its CreateMultiplaySession on, and a game sends its own address only after that, so the
    sender also gets any address the others sent earlier (one may have gone out before its watch)."""
    from .pesmatch import _pid, _room_of, _send_later
    pid = _pid(sess)
    rid, room = _room_of(svc, pid)
    if room is None:
        log.info("TURN address from pid %s: no room", pid)
        return {"result": NOERR}
    addrs = room.setdefault("turn_addr", {})
    addrs[pid] = {"address": req.get("address", ""), "address_data_size": int(req.get("address_data_size", 0) or 0)}
    for p in room["members"]:
        if p == pid:
            continue
        if (s := svc.online.get(p)) is not None:
            _send_later(s, "CMD_WATCH_TURN_ADDRESS_DATA", 0, 0x60, 1, {"result": NOERR, **addrs[pid]})
        if p in addrs:
            _send_later(sess, "CMD_WATCH_TURN_ADDRESS_DATA", 0, 0x60, 1, {"result": NOERR, **addrs[p]})
    log.info("TURN address pid %s room %s (%d bytes) forwarded", pid, rid, addrs[pid]["address_data_size"])
    return {"result": NOERR}
