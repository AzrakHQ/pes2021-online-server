"""Read PES 2021 save files (EDIT00000000, SYSTEM00000000, LIVE00000000): the container around their data.

notes/re/functions/dbgaps.md §3b, update.md; full tool: notes/re/functions/tools/pessave.py.
- 0x000..0x13f header: 4 SHA-512 digests (one per section; 64 zero bytes for an empty section) + 64 bytes kept in
  clear (+0x100). Decrypted with the MT19937 XOR stream keyed by raw[0x100:0x140] XOR SHA-512(title, UTF-16LE).
- section key k = XOR of the 5 header blocks; the 0xd0-byte info block (key k, each u64 XOR 0xd0) has the
  section sizes at +0x40 (2), +0x44 (1), +0x48 (0), +0x4c (3, in UTF-16 characters).
- sections 0..3 from 0x210, section i with key k (each u64 XOR i): display title, PNG, the data, owner SID.
The stream: init_by_array(16 LE u32 of the key); s0..s3 = 4 outputs; per dword out = in ^ s0^s1^s2^s3^m with m
the next output, then s0=ror(s1,15), s1=rol(s2,11), s2=rol(s3,7), s3=ror(m,13) (0x14140e0f0).
"""
import hashlib
import struct

TITLES = ("eFootball PES 2021 SEASON UPDATE", "eFootball Winning Eleven 2021 SEASON UPDATE")


class _MT:
    def __init__(self, key):
        mt = [0] * 624
        mt[0] = 19650218
        for i in range(1, 624):
            mt[i] = (1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & 0xffffffff
        i, j, n = 1, 0, len(key)
        for _ in range(max(624, n)):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525)) + key[j] + j) & 0xffffffff
            i += 1; j += 1
            if i >= 624:
                mt[0] = mt[623]; i = 1
            if j >= n:
                j = 0
        for _ in range(623):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941)) - i) & 0xffffffff
            i += 1
            if i >= 624:
                mt[0] = mt[623]; i = 1
        mt[0] = 0x80000000
        self.mt, self.idx = mt, 624

    def block(self):
        """The next 624 outputs (tempered)."""
        mt = self.mt
        for k in range(624):
            y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7fffffff)
            mt[k] = mt[(k + 397) % 624] ^ (y >> 1) ^ (0x9908b0df if y & 1 else 0)
        out = []
        for y in mt:
            y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680; y ^= (y << 15) & 0xefc60000; y ^= y >> 18
            out.append(y)
        return out


def crypt(data, key64):
    """The game's symmetric XOR stream keyed by 64 bytes (encrypts and decrypts)."""
    m = _MT(list(struct.unpack("<16I", key64)))
    stream = iter(())

    def gen():
        while True:
            yield from m.block()
    stream = gen()
    s0, s1, s2, s3 = next(stream), next(stream), next(stream), next(stream)
    n4 = len(data) // 4
    words = struct.unpack(f"<{n4}I", data[:n4 * 4])
    out = []
    append = out.append
    for w in words:
        r = next(stream)
        append(w ^ s0 ^ s1 ^ s2 ^ s3 ^ r)
        s0, s1, s2, s3 = ((s1 >> 15) | (s1 << 17)) & 0xffffffff, ((s2 << 11) | (s2 >> 21)) & 0xffffffff, \
            ((s3 << 7) | (s3 >> 25)) & 0xffffffff, ((r >> 13) | (r << 19)) & 0xffffffff
    res = bytearray(struct.pack(f"<{n4}I", *out))
    if len(data) % 4:
        ks = struct.pack("<I", s0 ^ s1 ^ s2 ^ s3 ^ next(stream))
        res += bytes(b ^ k for b, k in zip(data[n4 * 4:], ks))
    return bytes(res)


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _subkey(k, v):
    return b"".join(struct.pack("<Q", struct.unpack_from("<Q", k, i)[0] ^ v) for i in range(0, 64, 8))


def _digest(sec):
    return hashlib.sha512(sec).digest() if sec else bytes(64)


def open_save(raw):
    """The four sections of a save file's bytes, checked against their digests. ValueError if it isn't one."""
    if len(raw) < 0x210:
        raise ValueError("too short for a PES 2021 save")
    for title in TITLES:
        k0 = _xor(raw[0x100:0x140], hashlib.sha512(title.encode("utf-16-le")).digest())
        hdr = crypt(raw[:0x140], k0)
        digests = [hdr[i * 64:(i + 1) * 64] for i in range(4)]
        k = raw[0x100:0x140]
        for d in digests:
            k = _xor(k, d)
        info = crypt(raw[0x140:0x210], _subkey(k, 0xd0))
        s2, s1, s0, s3 = struct.unpack_from("<4I", info, 0x40)
        sizes = [s0, s1, s2, s3 * 2]
        if sum(sizes) + 0x210 > len(raw):
            continue
        secs, p = [], 0x210
        for i, n in enumerate(sizes):
            secs.append(crypt(raw[p:p + n], _subkey(k, i)) if i != 1 else raw[p:p + n])   # (the PNG: not needed)
            p += n
        if all(_digest(s) == d for i, (s, d) in enumerate(zip(secs, digests)) if i != 1):
            return secs
    raise ValueError("not a PES 2021 save file")


def data_of(raw):
    """Section 2 (the data) of a save file."""
    return open_save(raw)[2]


def seal(data, title=b"Edit Data", sid="S-1-5-21-0-0-0-1000", clear=None, kind=TITLES[0]):
    """A save file around `data` (tests; the game writes its own). No thumbnail."""
    secs = [title.ljust(384, b"\0"), b"", data, sid.encode("utf-16-le")]
    digests = [_digest(s) for s in secs]
    clear = clear or hashlib.sha512(data[:64]).digest()
    k = clear
    for d in digests:
        k = _xor(k, d)
    hdr = crypt(b"".join(digests) + clear, _xor(clear, hashlib.sha512(kind.encode("utf-16-le")).digest()))[:0x100]
    info = bytearray(0xd0)
    struct.pack_into("<4I", info, 0x40, len(secs[2]), len(secs[1]), len(secs[0]), len(secs[3]) // 2)
    out = hdr + clear + crypt(bytes(info), _subkey(k, 0xd0))
    for i, s in enumerate(secs):
        out += crypt(s, _subkey(k, i))
    return out
