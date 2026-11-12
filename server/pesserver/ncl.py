"""fox::ncl NclMio wire codec — the real PES 2021 gate/command protocol.

Recovered 2026-11-04 (static RE of PES2021.exe 1.01.00 + live capture), see docs/protocol.html:

  frame   = XOR_5b9f2e64( header8 | md5(header8 + body) | body )      NclMio parse 0x1418548c0
  header8 = u16 kind | u16 body_len | u32 seq   (big-endian; body_len <= 0x410)
  body    = Blowfish-ECB(easy_key, plain)                             easy tier, crypto 0x141856610
  plain   = u32 data_len | u32 original_size | u8 compressed | data | zero pad to 8   (BE)
  data    = zlib(msgpack) if compressed else msgpack                  decoder 0x1418566c0
  msgpack = {"msgid": "CMD_...", "rqid": n, ...}

easy_key = the 56-byte static key the client installs at 0x14116eeb0 from .data 0x1434d61c0 (same
Konami ncl key as MGSV's, which is its first 16 bytes). An "enhanced" tier (AES-256, 32-byte key set
from a server response, NclCryptAes 0x1418522a0) exists for kinds 0x1060/0x1070; not used at the gate.

Large messages (send path 0x1418537d0, encoders 0x141857190 / 0x141856d50 / 0x141856f70): data longer than
0x3f7 bytes goes out as one 0x70 frame {u32 total_len, u32 chunk_len, u32 original_size, u8 zlib, chunk
<= 0x3f3}, then 0x71 frames {u32 chunk_len, chunk <= 0x3fc}, ending with a 0x72 frame of the same layout.
The 0x10 family (0x20/0x21/0x22) is the same thing without the codec's "encrypt" flag; the client still
decrypts it. See notes/re/functions/wire.md section 1.
"""
import hashlib, logging, struct, warnings, zlib

import msgpack

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
    from cryptography.hazmat.primitives.ciphers import Cipher, modes

from .protocol import Codec, Message, ProtocolError, _rawlog, record

log = logging.getLogger("pesserver.ncl")

XOR_KEY = b"\x5b\x9f\x2e\x64"
EASY_KEY = bytes.fromhex(
    "d8890af066c96b40d701aefc436ff9fec98998167a74483d3914730c5c01c03c"
    "e28e86e589c4a185f8540651d2eca36b5c1a40eec5e9daae")
MAX_BODY = 0x410
KIND_EASY = 0x0060          # request/response kind observed for gate commands (easy tier, no session)
COMPRESS_OVER = 512         # zlib-compress payloads larger than this (client inflates when flag=1)
SINGLE_MAX = 0x3f7          # data bytes that fit one frame: 9-byte header + 0x3f7 = 0x400 after padding
FIRST_CHUNK = 0x3f3         # 13-byte header + 0x3f3 = 0x400
NEXT_CHUNK = 0x3fc          # 4-byte header + 0x3fc = 0x400
FRAG_KINDS = {0x60: (0x70, 0x71, 0x72), 0x10: (0x20, 0x21, 0x22)}    # single kind -> first/middle/last



def _short(v, limit=600):
    """Log form of a field: long strings and blobs (a 64 KB avatar, a replay upload's list of chunks) as their
    size only."""
    if isinstance(v, (str, bytes, bytearray)):
        return f"<{len(v)} {'chars' if isinstance(v, str) else 'bytes'}>" if len(v) > limit else v
    if isinstance(v, (list, tuple)):
        size = sum(len(x) for x in v if isinstance(x, (str, bytes, bytearray)))
        return f"<{len(v)} items, {size} bytes>" if size > limit else v
    return v

def xor(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY[i & 3] for i, b in enumerate(data))


def _bf(key: bytes):
    return Cipher(Blowfish(key), modes.ECB())


def bf_decrypt(data: bytes, key: bytes = EASY_KEY) -> bytes:
    if len(data) % 8:
        raise ProtocolError(f"body not a multiple of 8: {len(data)}")
    d = _bf(key).decryptor()
    return d.update(data) + d.finalize()


def bf_encrypt(data: bytes, key: bytes = EASY_KEY) -> bytes:
    e = _bf(key).encryptor()
    return e.update(data) + e.finalize()


def _payload(obj: dict, compress: bool | None = None) -> tuple[bytes, int, bool]:
    raw = msgpack.packb(obj, use_bin_type=True, unicode_errors="surrogateescape")
    if compress is None:
        compress = len(raw) > COMPRESS_OVER
    return (zlib.compress(raw) if compress else raw), len(raw), bool(compress)


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 8)


def pack_plain(obj: dict, compress: bool | None = None) -> bytes:
    """Single-frame plaintext. Use pack_plains() for anything that may be large."""
    data, orig, comp = _payload(obj, compress)
    return _pad(struct.pack(">IIB", len(data), orig, 1 if comp else 0) + data)


def pack_plains(obj: dict, compress: bool | None = None) -> list[bytes]:
    """Plaintexts for one message: [single] when it fits one frame, else [first, middle..., last]."""
    data, orig, comp = _payload(obj, compress)
    if len(data) <= SINGLE_MAX:
        return [_pad(struct.pack(">IIB", len(data), orig, 1 if comp else 0) + data)]
    first, rest = data[:FIRST_CHUNK], data[FIRST_CHUNK:]
    out = [_pad(struct.pack(">IIIB", len(data), len(first), orig, 1 if comp else 0) + first)]
    while rest:
        chunk, rest = rest[:NEXT_CHUNK], rest[NEXT_CHUNK:]
        out.append(_pad(struct.pack(">I", len(chunk)) + chunk))
    return out


def unpack_plain(plain: bytes) -> tuple[dict, bool]:
    if len(plain) < 9:
        raise ProtocolError("plaintext shorter than its 9-byte header")
    dlen, orig, comp = struct.unpack(">IIB", plain[:9])
    data = plain[9:9 + dlen]
    if len(data) != dlen:
        raise ProtocolError(f"data_len {dlen} exceeds body ({len(plain) - 9})")
    return _inflate(data, orig, comp), bool(comp)


def frame(kind: int, seq: int, body: bytes) -> bytes:
    if len(body) > MAX_BODY:
        raise ProtocolError(f"body {len(body)} > {MAX_BODY}; needs segmentation")
    hdr = struct.pack(">HHI", kind, len(body), seq)
    return xor(hdr + hashlib.md5(hdr + body).digest() + body)


def frames_for(plains: list[bytes], kind: int, seq: int, key: bytes = EASY_KEY) -> bytes:
    """Encrypt and frame a message's plaintexts; several plaintexts become first/middle/last frames.
    `kind` may be a fragment kind echoed from a split request; it is mapped back to its family."""
    kind = next((single for single, ks in FRAG_KINDS.items() if kind in ks), kind)
    if len(plains) == 1:
        return frame(kind, seq, bf_encrypt(plains[0], key))
    first, middle, last = FRAG_KINDS.get(kind, FRAG_KINDS[KIND_EASY])
    kinds = [first] + [middle] * (len(plains) - 2) + [last]
    return b"".join(frame(k, seq, bf_encrypt(p, key)) for k, p in zip(kinds, plains))


def encode_message(obj: dict, kind: int = KIND_EASY, seq: int = 1, compress: bool | None = None) -> bytes:
    return frames_for(pack_plains(obj, compress), kind, seq)


def _inflate(data: bytes, orig: int, comp: int) -> dict:
    raw = zlib.decompress(data) if comp else data
    if len(raw) != orig:
        log.warning("original_size mismatch: header %d, got %d", orig, len(raw))
    # The client packs some binary payloads as msgpack *str*; keep them losslessly (surrogateescape).
    obj = msgpack.unpackb(raw, raw=False, strict_map_key=False, unicode_errors="surrogateescape")
    if not isinstance(obj, dict):
        raise ProtocolError(f"msgpack root is {type(obj).__name__}, expected map")
    return obj


def _first_fragment(plain: bytes) -> tuple[int, int, int, bytes]:
    """(total_len, original_size, zlib, chunk) of a 0x70 plaintext; raises on a header that doesn't fit."""
    if len(plain) < 13:
        raise ProtocolError("first fragment shorter than its 13-byte header")
    total, clen, orig, comp = struct.unpack(">IIIB", plain[:13])
    if comp not in (0, 1) or clen > len(plain) - 13 or clen > total or total > 1 << 20 or orig > 1 << 24:
        raise ProtocolError(f"bad first-fragment header total={total} chunk={clen} orig={orig} zlib={comp}")
    return total, orig, comp, plain[13:13 + clen]


def _next_fragment(plain: bytes) -> bytes:
    (clen,) = struct.unpack(">I", plain[:4])
    if clen > len(plain) - 4:
        raise ProtocolError(f"fragment chunk_len {clen} exceeds body ({len(plain) - 4})")
    return plain[4:4 + clen]


def decode_frame(buf: bytes) -> tuple[int, int, dict]:
    """Decode one complete frame (for tests/tools). Returns (kind, seq, msg dict)."""
    plain = xor(buf[:24])
    kind, blen, seq = struct.unpack(">HHI", plain[:8])
    body = xor(buf[24:24 + blen])
    if hashlib.md5(plain[:8] + body).digest() != plain[8:24]:
        raise ProtocolError("bad md5")
    obj, _ = unpack_plain(bf_decrypt(body))
    return kind, seq, obj


def decode_stream(buf: bytes, key: bytes = EASY_KEY) -> list[tuple[int, int, dict]]:
    """Decode every message in a byte string of frames, joining fragments (for tests/tools).
    Returns [(kind of the first frame, seq, msg dict)]."""
    out, pos, parts = [], 0, None
    while pos < len(buf):
        plain_hdr = xor(buf[pos:pos + 24])
        kind, blen, seq = struct.unpack(">HHI", plain_hdr[:8])
        body = xor(buf[pos + 24:pos + 24 + blen])
        if hashlib.md5(plain_hdr[:8] + body).digest() != plain_hdr[8:24]:
            raise ProtocolError("bad md5")
        pos += 24 + blen
        plain = bf_decrypt(body, key)
        if kind in (0x70, 0x20):
            total, orig, comp, chunk = _first_fragment(plain)
            parts = (kind, seq, total, orig, comp, [chunk])
        elif kind in (0x71, 0x72, 0x21, 0x22):
            if parts is None:
                raise ProtocolError(f"fragment 0x{kind:04x} without a first fragment")
            parts[5].append(_next_fragment(plain))
            if kind in (0x72, 0x22):
                k0, s0, total, orig, comp, chunks = parts
                data = b"".join(chunks)
                if len(data) != total:
                    raise ProtocolError(f"fragments carry {len(data)} bytes, first frame said {total}")
                out.append((k0, s0, _inflate(data, orig, comp)))
                parts = None
        else:
            out.append((kind, seq, unpack_plain(plain)[0]))
    return out


class NclCodec(Codec):
    """Gate codec: one msgpack command per frame. Message.name = msgid, Message.seq = rqid; the frame
    kind/seq are kept in body['_kind'] / body['_seq'] and echoed on the reply.

    Per-connection Blowfish key: starts as the static easy key; a server response can hand the client a
    session key (CMD_SET_CURRENTPLAYER.key). offer_keys() registers candidate derivations; the first
    incoming frame that fails with the current key is retried with each candidate and the one that
    decodes is adopted (and logged), so the derivation is learned from the client itself."""

    def __init__(self):
        self.key = EASY_KEY
        self.candidates = []

    def offer_keys(self, candidates):
        self.candidates = [(lbl, k) for lbl, k in candidates if 8 <= len(k) <= 56]

    async def _read_frame(self, reader, first=False):
        from .protocol import _read_or_none
        hdr = await (_read_or_none(reader, 24) if first else reader.readexactly(24))
        if hdr is None:
            return None
        _rawlog(hdr)
        plain_hdr = xor(hdr)
        kind, blen, seq = struct.unpack(">HHI", plain_hdr[:8])
        if blen > MAX_BODY:
            raise ProtocolError(f"body_len {blen} > {MAX_BODY} (kind 0x{kind:04x})")
        raw = await reader.readexactly(blen)
        _rawlog(raw)
        body = xor(raw)
        if hashlib.md5(plain_hdr[:8] + body).digest() != plain_hdr[8:24]:
            raise ProtocolError(f"bad md5 (kind 0x{kind:04x} len {blen})")
        if kind & 0x1000:
            raise ProtocolError(f"enhanced-tier (AES) kind 0x{kind:04x} not supported yet")
        return kind, seq, body

    def _decrypt_trying_keys(self, body, check):
        """Decrypt with the current key; when check() rejects the result, try each offered session key."""
        plain = bf_decrypt(body, self.key)
        try:
            return plain, check(plain)
        except Exception as first:
            for lbl, k in self.candidates:
                p2 = bf_decrypt(body, k)
                try:
                    got = check(p2)
                except Exception:
                    continue
                log.warning("SESSION KEY adopted: %s (%d bytes) %s", lbl, len(k), k.hex())
                self.key, self.candidates = k, []
                return p2, got
            raise ProtocolError(f"undecodable body with current key and {len(self.candidates)} "
                                f"candidates: {first}")

    async def _read_fragments(self, reader, kind, seq, body):
        """0x70 first frame, 0x71 middles, 0x72 last (or 0x20/0x21/0x22): join the chunks, then inflate."""
        _, (total, orig, comp, chunk) = self._decrypt_trying_keys(body, _first_fragment)
        chunks, got, n = [chunk], len(chunk), 1
        middle, last = (0x71, 0x72) if kind == 0x70 else (0x21, 0x22)
        while True:
            k2, s2, body2 = await self._read_frame(reader)
            if k2 not in (middle, last):
                raise ProtocolError(f"expected fragment 0x{middle:02x}/0x{last:02x} after 0x{kind:02x}, got 0x{k2:04x}")
            c = _next_fragment(bf_decrypt(body2, self.key))
            chunks.append(c); got += len(c); n += 1
            if got > total:
                raise ProtocolError(f"fragments carry more than the {total} bytes announced")
            if k2 == last:
                break
        if got != total:
            raise ProtocolError(f"fragments carry {got} bytes, first frame said {total}")
        log.info("fragmented message: %d frames, %d bytes", n, total)
        return _inflate(b"".join(chunks), orig, comp), bool(comp)

    async def read(self, reader):
        got = await self._read_frame(reader, first=True)
        if got is None:
            return None
        kind, seq, body = got
        try:
            if kind in (0x70, 0x20):
                obj, comp = await self._read_fragments(reader, kind, seq, body)
            elif kind in (0x71, 0x72, 0x21, 0x22):
                raise ProtocolError(f"fragment 0x{kind:04x} without a first fragment")
            else:
                _, (obj, comp) = self._decrypt_trying_keys(body, unpack_plain)
            msgid = str(obj.get("msgid", "?"))
            rqid = int(obj.get("rqid", 0) or 0)
        except (struct.error, zlib.error, ValueError, TypeError, msgpack.exceptions.UnpackException) as e:
            raise ProtocolError(f"undecodable message (kind 0x{kind:04x}): {e}") from None     # not a traceback
        log.info("RX kind=0x%04x seq=%d %s %s", kind, seq, msgid,
                 {k: _short(v) for k, v in obj.items() if k not in ("msgid",)})
        record("rx", msgid, obj)
        body_out = dict(obj)
        body_out["_kind"], body_out["_seq"], body_out["_compressed"] = kind, seq, comp
        return Message(msgid, rqid, body_out)

    def encode(self, msg: Message) -> bytes:
        obj = {k: v for k, v in msg.body.items() if not k.startswith("_")}
        obj.setdefault("msgid", msg.name)
        obj.setdefault("rqid", msg.seq)
        kind = int(msg.body.get("_kind", KIND_EASY))
        seq = int(msg.body.get("_seq", 1))
        log.info("TX kind=0x%04x seq=%d %s %s", kind, seq, obj["msgid"],
                 {k: _short(v) for k, v in obj.items() if k != "msgid"})
        record("tx", obj["msgid"], obj)
        plains = pack_plains(obj)
        if len(plains) > 1:
            log.info("TX %s split into %d frames", obj["msgid"], len(plains))
        return frames_for(plains, kind, seq, self.key)
