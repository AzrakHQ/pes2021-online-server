"""fox::ncl NclMio wire codec — the real PES 2021 gate/command protocol.

Recovered 2026-11-04 (static RE of PES2021.exe 1.01.00 + live capture), see notes/network-map.html:

  frame   = XOR_5b9f2e64( header8 | md5(header8 + body) | body )      NclMio parse 0x1418548c0
  header8 = u16 kind | u16 body_len | u32 seq   (big-endian; body_len <= 0x410)
  body    = Blowfish-ECB(easy_key, plain)                             easy tier, crypto 0x141856610
  plain   = u32 data_len | u32 original_size | u8 compressed | data | zero pad to 8   (BE)
  data    = zlib(msgpack) if compressed else msgpack                  decoder 0x1418566c0
  msgpack = {"msgid": "CMD_...", "rqid": n, ...}

easy_key = the 56-byte static key the client installs at 0x14116eeb0 from .data 0x1434d61c0 (same
Konami ncl key as MGSV's, which is its first 16 bytes). An "enhanced" tier (AES-256, 32-byte key set
from a server response, NclCryptAes 0x1418522a0) exists for kinds 0x1060/0x1070; not used at the gate.
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


def pack_plain(obj: dict, compress: bool | None = None) -> bytes:
    raw = msgpack.packb(obj, use_bin_type=True, unicode_errors="surrogateescape")
    if compress is None:
        compress = len(raw) > COMPRESS_OVER
    data = zlib.compress(raw) if compress else raw
    plain = struct.pack(">IIB", len(data), len(raw), 1 if compress else 0) + data
    return plain + b"\0" * (-len(plain) % 8)


def unpack_plain(plain: bytes) -> tuple[dict, bool]:
    if len(plain) < 9:
        raise ProtocolError("plaintext shorter than its 9-byte header")
    dlen, orig, comp = struct.unpack(">IIB", plain[:9])
    data = plain[9:9 + dlen]
    if len(data) != dlen:
        raise ProtocolError(f"data_len {dlen} exceeds body ({len(plain) - 9})")
    raw = zlib.decompress(data) if comp else data
    if len(raw) != orig:
        log.warning("original_size mismatch: header %d, got %d", orig, len(raw))
    # The client packs some binary payloads as msgpack *str*; keep them losslessly (surrogateescape).
    obj = msgpack.unpackb(raw, raw=False, strict_map_key=False, unicode_errors="surrogateescape")
    if not isinstance(obj, dict):
        raise ProtocolError(f"msgpack root is {type(obj).__name__}, expected map")
    return obj, bool(comp)


def frame(kind: int, seq: int, body: bytes) -> bytes:
    if len(body) > MAX_BODY:
        raise ProtocolError(f"body {len(body)} > {MAX_BODY}; needs segmentation")
    hdr = struct.pack(">HHI", kind, len(body), seq)
    return xor(hdr + hashlib.md5(hdr + body).digest() + body)


def encode_message(obj: dict, kind: int = KIND_EASY, seq: int = 1, compress: bool | None = None) -> bytes:
    return frame(kind, seq, bf_encrypt(pack_plain(obj, compress)))


def decode_frame(buf: bytes) -> tuple[int, int, dict]:
    """Decode one complete frame (for tests/tools). Returns (kind, seq, msg dict)."""
    plain = xor(buf[:24])
    kind, blen, seq = struct.unpack(">HHI", plain[:8])
    body = xor(buf[24:24 + blen])
    if hashlib.md5(plain[:8] + body).digest() != plain[8:24]:
        raise ProtocolError("bad md5")
    obj, _ = unpack_plain(bf_decrypt(body))
    return kind, seq, obj


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

    async def read(self, reader):
        from .protocol import _read_or_none
        hdr = await _read_or_none(reader, 24)
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
        try:
            obj, comp = unpack_plain(bf_decrypt(body, self.key))
        except Exception as first:
            for lbl, k in self.candidates:
                try:
                    obj, comp = unpack_plain(bf_decrypt(body, k))
                except Exception:
                    continue
                log.warning("SESSION KEY adopted: %s (%d bytes) %s", lbl, len(k), k.hex())
                self.key, self.candidates = k, []
                break
            else:
                raise ProtocolError(f"undecodable body with current key and {len(self.candidates)} "
                                    f"candidates: {first}")
        msgid = str(obj.get("msgid", "?"))
        rqid = int(obj.get("rqid", 0) or 0)
        log.info("RX kind=0x%04x seq=%d %s %s", kind, seq, msgid,
                 {k: v for k, v in obj.items() if k not in ("msgid",)})
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
                 {k: v for k, v in obj.items() if k != "msgid"})
        record("tx", obj["msgid"], obj)
        return frame(kind, seq, bf_encrypt(pack_plain(obj), self.key))
