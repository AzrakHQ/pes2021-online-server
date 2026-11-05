"""The server's permanent identity, and how friends' mods find it again after a restart or IP change.

- identity.key (Ed25519, in the data folder) is created on first start and kept through restarts and
  updates. Its public half is the server's identity; it is written into the mod friends download.
- A *note* says where the server is right now: its web address and game addresses, with a time,
  signed with that key. The server serves it at /mod/note and publishes it to the address book
  (PESSERVER_BOOK, a tiny web service) whenever it changes. The mod accepts a note only if this
  server's key signed it, so neither the address book nor anyone else can send friends elsewhere.
- A *hello* is the mod's check that an address really is this server: it sends a random nonce to
  /mod/hello and accepts the address only if the reply carries this key's signature over it.

Wire formats (text, UTF-8; first line is the base64 signature over everything after it):
  note:  <sig>\\nPES2021ONLINE-NOTE 1\\n<unix time>\\n<web address or ->\\n<addr>\\n<addr>...\\n
  hello: <sig>\\nPES2021ONLINE-HELLO 1\\n<public key hex>\\n<nonce>\\n
"""
import base64, logging, os, re, threading, time, urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

log = logging.getLogger("pesserver.identity")
_KEY = []
DEFAULT_BOOK = ""          # set to the project's address book once it is published
EMBED_MARKER = b"PES2021ONLINE-EMBEDDED-SETTINGS\0"
EMBED_SIZE = 2048          # must match the mod's reserved settings area


def init(data_dir):
    """Load identity.key from data_dir, creating it the first time."""
    path = os.path.join(data_dir, "identity.key")
    try:
        k = Ed25519PrivateKey.from_private_bytes(open(path, "rb").read())
    except FileNotFoundError:
        k = Ed25519PrivateKey.generate()
        raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        log.info("created the server identity %s", path)
    _KEY[:] = [k]
    return k


def _key():
    if not _KEY:
        init(os.environ.get("PESSERVER_DATA", "."))
    return _KEY[0]


def public_hex():
    return _key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _signed(body: str) -> str:
    return base64.b64encode(_key().sign(body.encode())).decode() + "\n" + body


def note(url, addrs, t=None):
    return _signed("PES2021ONLINE-NOTE 1\n%d\n%s\n%s" % (int(t or time.time()), url or "-",
                                                         "".join(a + "\n" for a in addrs)))


def hello(nonce, version=1):
    """Proof that this address is this server. Version 2 (asked for with v=2) also tells the mod that
    this server reads the player-id line at the start of each gate connection (gate.py); older mods
    ask for nothing and get version 1, which they check byte for byte."""
    if not re.fullmatch(r"[0-9a-fA-F]{8,64}", nonce or ""):
        raise ValueError("bad nonce")
    return _signed("PES2021ONLINE-HELLO %d\n%s\n%s\n" % (2 if version >= 2 else 1, public_hex(), nonce.lower()))


def current_note():
    from . import netsetup
    return note(None, netsetup.game_addrs())


# ------------------------------------------------------------------------------- address book
def book_url():
    return os.environ.get("PESSERVER_BOOK", DEFAULT_BOOK).rstrip("/")


def publish_once(text=None):
    """PUT the current note to the address book. Returns True on success."""
    base = book_url()
    if not base:
        return False
    req = urllib.request.Request(f"{base}/v1/{public_hex()}", data=(text or current_note()).encode(), method="PUT",
                                 headers={"Content-Type": "text/plain", "User-Agent": "pesserver"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception as e:  # noqa: BLE001 (network errors of every kind just mean "try again later")
        log.warning("address book: could not publish (%s)", e)
        return False


def start_publisher(every=60, refresh=24 * 3600):
    """Background thread: publish whenever the addresses change, and once a day anyway."""
    if not book_url():
        log.info("address book: none configured (PESSERVER_BOOK); mods use the addresses they know")
        return None

    def loop():
        from . import netsetup
        last, last_ok = None, 0.0
        while True:
            if netsetup.STATE.get("ready"):
                where = tuple(netsetup.game_addrs())
                if where != last or time.time() - last_ok > refresh:
                    if publish_once():
                        log.info("address book: published %s", where)
                        last, last_ok = where, time.time()
            time.sleep(every)
    t = threading.Thread(target=loop, name="book-publisher", daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------------------- the mod file
def mod_settings(name, url, addrs):
    lines = {"key": public_hex(), "name": name, "url": url or "", "addrs": ",".join(addrs), "book": book_url()}
    return "".join(f"{k}={v}\n" for k, v in lines.items())


def pack_mod(dll: bytes, settings: str) -> bytes:
    """The mod DLL with this server's settings written into its reserved area."""
    i = dll.find(EMBED_MARKER)
    if i < 0:
        raise ValueError("this mod build has no settings area")
    data = settings.encode()
    room = EMBED_SIZE - len(EMBED_MARKER) - 1
    if len(data) > room:
        raise ValueError("settings too long")
    start = i + len(EMBED_MARKER)
    return dll[:start] + data + b"\0" * (room + 1 - len(data)) + dll[start + room + 1:]
