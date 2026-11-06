"""Test-only add-on: the TURN relay's second leg, TURN over DTLS 1.2 on UDP (RFC 7350).

What we know live (notes/re/functions/livetests.md): the game's TURN client allocates over TLS, then reads the
server's SOFTWARE attribute as a "dual route" directive "HOST:PORT,<text>,0" and opens DTLS 1.2 to HOST:PORT
(ECDHE-ECDSA-AES256-GCM-SHA384, our TURN certificate). This module:
  - adds that SOFTWARE directive to Allocate/Refresh successes on the TLS leg;
  - listens on UDP DTLS_PORT, runs DTLS per client address with pyOpenSSL memory BIOs, and hands every decrypted
    datagram (one STUN message or one ChannelData frame) to the relay's own _Conn logic, so Allocate, Refresh,
    permissions, channels and relaying work exactly as on the TLS leg.
Installed by importing it before the server starts. Logs with the prefix "DTLS".
"""
import asyncio, logging, os, struct, time
from OpenSSL import SSL
from pesserver import turnrelay as tr

log = logging.getLogger("pesserver.dtlsleg")
DTLS_PORT = int(os.environ.get("DTLS_PORT", "5350"))
DTLS_HOST = os.environ.get("DTLS_HOST", "")          # filled from the TLS socket's local address if empty
A_SOFTWARE = 0x8022
NAMES = {tr.ALLOCATE: "Allocate", tr.REFRESH: "Refresh", tr.CREATE_PERMISSION: "CreatePermission",
         tr.CHANNEL_BIND: "ChannelBind", tr.SEND: "Send", tr.DATA: "Data", tr.BINDING: "Binding"}
STATS = {"stun": 0, "chdata_in": 0, "out": 0}

# ---- 1. SOFTWARE directive on the TLS leg ------------------------------------------------------------
_build = tr.build


def build(method, cls, tid, attrs, key=None, fingerprint=True):
    if not _in_dtls[0] and cls == tr.SUCCESS and method in (tr.ALLOCATE, tr.REFRESH) and not any(a == A_SOFTWARE for a, _ in attrs):
        host = DTLS_HOST or _local_ip[0]
        attrs = list(attrs) + [(A_SOFTWARE, f"{host}:{DTLS_PORT},pes,0".encode())]
    return _build(method, cls, tid, attrs, key, fingerprint)


tr.build = build
_local_ip = ["192.168.1.100"]
import contextvars
_DTLS_CTX = contextvars.ContextVar("dtls", default=False)   # inherited by tasks the relay starts (async Allocate)


class _Flag(list):
    def __getitem__(self, i):
        return _DTLS_CTX.get()

    def __setitem__(self, i, v):
        _DTLS_CTX.set(v)


_in_dtls = _Flag([False])                             # replies on the DTLS leg get no directive

_conn_init = tr._Conn.__init__


def conn_init(self, server, writer):
    _conn_init(self, server, writer)
    if not DTLS_HOST and not getattr(writer, "is_dtls", False):
        _local_ip[0] = self.local[0]


tr._Conn.__init__ = conn_init


# ---- 2. the DTLS endpoint ----------------------------------------------------------------------------
class _Writer:
    """Looks like an asyncio StreamWriter to turnrelay._Conn; writes go out as DTLS records."""
    is_dtls = True

    def __init__(self, peer):
        self.p = peer

    def get_extra_info(self, name):
        return {"peername": self.p.addr, "sockname": (_local_ip[0], DTLS_PORT)}.get(name)

    def is_closing(self):
        return self.p.dead

    def write(self, data):
        self.p.send(data)

    def close(self):
        self.p.close()


class _Peer:
    def __init__(self, ep, addr):
        self.ep, self.addr, self.dead, self.ready = ep, addr, False, False
        self.c = SSL.Connection(ep.ctx, None)
        self.c.set_accept_state()
        try:
            self.c.set_ciphertext_mtu(1200)
        except Exception:  # noqa: BLE001
            pass
        self.conn = None
        self.t0 = time.monotonic()
        self.timer = None

    def flush(self):
        while True:
            try:
                out = self.c.bio_read(65536)
            except SSL.WantReadError:
                break
            if not out:
                break
            self.ep.t.sendto(out, self.addr)

    def arm(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        try:
            t = self.c.DTLSv1_get_timeout()
        except Exception:  # noqa: BLE001
            t = None
        if t is not None and not self.dead:
            self.timer = asyncio.get_running_loop().call_later(max(t, 0.05), self.on_timer)

    def on_timer(self):
        self.timer = None
        try:
            self.c.DTLSv1_handle_timeout()
        except SSL.Error as e:
            log.info("DTLS %s timeout error %s", self.addr, e)
        self.flush(); self.arm()

    def feed(self, data):
        self.c.bio_write(data)
        try:
            if not self.ready:
                try:
                    self.c.do_handshake()
                except SSL.WantReadError:
                    return
                self.ready = True
                log.info("DTLS %s handshake done in %.2fs: %s %s", self.addr, time.monotonic() - self.t0,
                         self.c.get_protocol_version_name(), self.c.get_cipher_name())
                self.conn = tr._Conn(self.ep.srv, _Writer(self))
                try:
                    self.keys = _keys(self.c.master_key(), self.c.client_random(), self.c.server_random())
                except Exception as e:  # noqa: BLE001
                    log.info("DTLS key export failed: %s", e); self.keys = None
            while True:
                try:
                    msg = self.c.recv(65536)
                except SSL.WantReadError:
                    break
                except SSL.ZeroReturnError:
                    log.info("DTLS %s closed by peer", self.addr); self.close(); return
                if msg and msg[-1] == 0:
                    self.handle(msg[:-1])
                else:
                    log.info("DTLS %s ktc message type %s (%dB) not handled", self.addr, msg[-1:].hex(), len(msg))
        except SSL.Error as e:
            log.info("DTLS %s SSL error %s", self.addr, e); self.close()
        finally:
            self.flush(); self.arm()

    def manual(self, data):
        """The game's DTLS library (ktc) writes application records whose length field is 2 bytes too big
        (2 stray bytes after the GCM tag) and whose plaintext ends with an extra NUL. OpenSSL drops them, so we
        decrypt them here with the session keys (AES-256-GCM, TLS 1.2 key block) and return the plaintext."""
        if not getattr(self, "keys", None):
            return None
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        typ, ver, ep, seq, n = data[0], data[1:3], data[3:5], data[5:11], struct.unpack("!H", data[11:13])[0]
        body = data[13:13 + n]; ck, _, civ, _ = self.keys
        for extra in (2, 0, 1, 3, 4):
            core = body[:len(body) - extra]
            if len(core) < 25:
                continue
            aad = ep + seq + bytes([typ]) + ver + struct.pack("!H", len(core) - 24)
            try:
                pt = AESGCM(ck).decrypt(civ + core[:8], core[8:], aad)
            except Exception:  # noqa: BLE001
                continue
            self.quirk = extra
            if not getattr(self, "_quirk_logged", False):
                self._quirk_logged = True
                log.info("DTLS %s ktc record quirk: %d stray bytes after the tag, plaintext %dB (ends %s)",
                         self.addr, extra, len(pt), pt[-2:].hex())
            return pt
        log.info("DTLS %s app record could not be decrypted (%s)", self.addr, data[:16].hex())
        return None

    def trim(self, pt):
        """Cut the plaintext to the STUN message or ChannelData frame it holds (drops the trailing NUL)."""
        if len(pt) >= 4 and pt[0] & 0xC0 == 0x40:
            return pt[:4 + struct.unpack("!H", pt[2:4])[0]]
        if len(pt) >= 20:
            return pt[:20 + struct.unpack("!H", pt[2:4])[0]]
        return pt

    def handle(self, msg):
        if not msg:
            return
        if msg[0] & 0xC0 == 0x40:                       # ChannelData
            ch, n = struct.unpack("!HH", msg[:4])
            STATS["chdata_in"] += 1
            if STATS["chdata_in"] in (1, 10, 100, 1000) or STATS["chdata_in"] % 5000 == 0:
                log.info("DTLS %s ChannelData #%d ch=%#x len=%d", self.addr, STATS["chdata_in"], ch, n)
            self.conn.channel_data(ch, msg[4:4 + n])
            return
        try:
            method, cls, tid, attrs = tr.parse(msg)
            STATS["stun"] += 1
            log.info("DTLS %s <- %s class=%d tid=%s attrs=%s", self.addr, NAMES.get(method, hex(method)), cls,
                     tid.hex()[:8], _vals(attrs))
        except Exception:  # noqa: BLE001
            log.info("DTLS %s <- unparsable %dB %s", self.addr, len(msg), msg[:24].hex())
            return
        _in_dtls[0] = True
        try:
            self.conn.stun(msg)
        finally:
            _in_dtls[0] = False

    def send(self, data):
        if self.dead:
            return
        STATS["out"] += 1
        if data[:1] and data[0] & 0xC0 == 0x40:
            data = data[:4 + struct.unpack("!H", data[2:4])[0]]      # no TCP padding on a datagram leg
            STATS["chdata_out"] = STATS.get("chdata_out", 0) + 1
            if STATS["chdata_out"] in (1, 2, 5, 10, 50, 100, 1000) or STATS["chdata_out"] % 5000 == 0:
                log.info("DTLS %s -> ChannelData #%d len=%d", self.addr, STATS["chdata_out"], len(data))
        if data[:1] and data[0] & 0xC0 == 0:
            try:
                method, cls, tid, attrs = tr.parse(data)
                log.info("DTLS %s -> %s class=%d tid=%s attrs=%s", self.addr, NAMES.get(method, hex(method)), cls,
                         tid.hex()[:8], _vals(attrs))
            except Exception:  # noqa: BLE001
                pass
        try:
            self.c.send(data + b"\x00")          # ktc framing: last plaintext byte = message type (0 = plain)
        except SSL.Error as e:
            log.info("DTLS %s send error %s", self.addr, e)
        self.flush()

    def send_quirk(self, data):
        """Write an application record the way the ktc client writes them: plaintext + NUL, AES-256-GCM with
        the server write key, explicit nonce = epoch|seq, then `quirk` stray bytes after the tag (counted in the
        record length). OpenSSL used server epoch 1 seq 0 for its Finished; we continue from seq 1."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        _, sk, _, siv = self.keys
        self.wseq = getattr(self, "wseq", 9) + 1
        ep, seq = b"\x00\x01", self.wseq.to_bytes(6, "big")
        pt = data + b"\x00"
        aad = ep + seq + b"\x17\xfe\xfd" + struct.pack("!H", len(pt))
        ct = AESGCM(sk).encrypt(siv + ep + seq, pt, aad)
        body = ep + seq + ct + b"\x00" * self.quirk
        self.ep.t.sendto(b"\x17\xfe\xfd" + ep + seq + struct.pack("!H", len(body)) + body, self.addr)

    def close(self):
        if self.dead:
            return
        self.dead = True
        if self.timer:
            self.timer.cancel()
        if self.conn is not None:
            self.conn.closed()
        self.ep.peers.pop(self.addr, None)


def _prf384(secret, label, seed, n):
    import hmac, hashlib
    out, a = b"", label + seed
    while len(out) < n:
        a = hmac.new(secret, a, hashlib.sha384).digest()
        out += hmac.new(secret, a + label + seed, hashlib.sha384).digest()
    return out[:n]


def _keys(master, client_random, server_random):
    kb = _prf384(master, b"key expansion", server_random + client_random, 72)
    return kb[0:32], kb[32:64], kb[64:68], kb[68:72]


class _Endpoint(asyncio.DatagramProtocol):
    def __init__(self, srv, certfile, keyfile):
        self.srv, self.peers = srv, {}
        ctx = SSL.Context(SSL.DTLS_METHOD)
        ctx.use_certificate_chain_file(certfile)
        ctx.use_privatekey_file(keyfile)
        ctx.set_options(getattr(SSL, "OP_NO_QUERY_MTU", 0x1000))
        self.ctx = ctx

    def connection_made(self, t):
        self.t = t

    def datagram_received(self, data, addr):
        if data[:1] == b"\x17":
            log.info("DTLS %s rx app datagram %dB", addr, len(data))
        p = self.peers.get(addr)
        if p is None or (p.dead):
            if not data or data[0] != 22:              # only a handshake record may open a session
                return
            p = self.peers[addr] = _Peer(self, addr)
            log.info("DTLS %s new session (%dB first record)", addr, len(data))
        elif data[0] == 22 and len(data) > 13 and data[13] == 1 and p.ready:   # new ClientHello: restart
            log.info("DTLS %s new ClientHello on a live session: restart", addr)
            p.close(); p = self.peers[addr] = _Peer(self, addr)
        p.feed(_untrail(data))


def _vals(attrs):
    out = []
    for a, v, _ in attrs:
        if a in (0x000D, 0x0009, 0x000C, 0x0019):
            out.append(f"{a:#x}={v.hex()}")
        elif a in (0x8022, 0x0014, 0x0006):
            out.append(f"{a:#x}={v[:40]!r}")
        else:
            out.append(f"{a:#x}[{len(v)}]")
    return out


def _untrail(d):
    """ktc appends 2 bytes after the last record of every application-data datagram and adds 2 to that
    record's length (0x141235df0). Undo it so OpenSSL sees standard DTLS records."""
    if len(d) < 15 or d[0] != 0x17:
        return d
    off = 0
    while off + 13 <= len(d):
        n = struct.unpack("!H", d[off + 11:off + 13])[0]
        if off + 13 + n >= len(d):
            if off + 13 + n == len(d) and n > 2:
                return d[:off + 11] + struct.pack("!H", n - 2) + d[off + 13:-2]
            return d
        off += 13 + n
    return d


_start = tr.TurnServer.start


async def start(self, host, port, certfile, keyfile):
    r = await _start(self, host, port, certfile, keyfile)
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: _Endpoint(self, certfile, keyfile), local_addr=("0.0.0.0", DTLS_PORT))
    log.info("DTLS TURN leg listening on udp %d (directive host %s)", DTLS_PORT, DTLS_HOST or "<TLS local address>")
    return r


tr.TurnServer.start = start


# log every STUN message on the TLS leg too (method/class/attrs), to see why sessions restart
_stun = tr._Conn.stun


def _tls_stun(self, data):
    if not _in_dtls[0]:
        try:
            m, c, _, attrs = tr.parse(data)
            log.info("TLS %s <- %s class=%d attrs=%s", self.peer, NAMES.get(m, hex(m)), c, [hex(a) for a, _, _ in attrs])
        except Exception:  # noqa: BLE001
            pass
    return _stun(self, data)


tr._Conn.stun = _tls_stun
_closed = tr._Conn.closed


def _tls_closed(self):
    if not getattr(self.writer, "is_dtls", False):
        log.info("TLS %s closed", self.peer)
    return _closed(self)


tr._Conn.closed = _tls_closed
