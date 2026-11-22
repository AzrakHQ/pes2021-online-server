#!/usr/bin/env python3
"""Read and write PES 2021 save files (SYSTEM00000000, EDIT00000000, LIVE00000000, ...).

  pessave.py info FILE             header, sizes, type, title, owner SID, digest check
  pessave.py data FILE OUT         section 2 (the data) to OUT
  pessave.py pack TEMPLATE DATA OUT   TEMPLATE's container with section 2 replaced by DATA (digests redone)

The container (dbgaps.md §3b, static 0x14140e0f0 / 0x14140f470):
- 0x000..0x13f header: 4 SHA-512 digests (one per section) + 64 bytes kept in clear (+0x100). Decrypted with the
  MT19937 XOR stream keyed by raw[0x100:0x140] XOR SHA-512(title as UTF-16LE).
- section key k = XOR of the 5 header blocks (4 digests + the clear block).
- 0x140..0x20f info block (0xd0 bytes), key k with each u64 XOR 0xd0: section sizes at +0x40 (2), +0x44 (1),
  +0x48 (0), +0x4c (3, in UTF-16 chars); type name and title.
- then sections 0..3, section i with key k (each u64 XOR i): 0 display title (384 B), 1 PNG, 2 the data,
  3 the owner's Windows SID (UTF-16).
The stream: init_by_array(16 LE u32 of the key); s0..s3 = 4 outputs; per dword out = in ^ s0^s1^s2^s3^m with m
the next output, then s0=ror(s1,15), s1=rol(s2,11), s2=rol(s3,7), s3=ror(m,13).
"""
import hashlib, struct, sys

TITLES = ("eFootball PES 2021 SEASON UPDATE", "eFootball Winning Eleven 2021 SEASON UPDATE", "eFootball PES 2020")


class MT:
    def __init__(self, key):
        mt = [0] * 624
        mt[0] = 19650218
        for i in range(1, 624):
            mt[i] = (1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & 0xffffffff
        i, j, n = 1, 0, len(key)
        for _ in range(max(624, n)):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525)) + key[j] + j) & 0xffffffff
            i += 1; j += 1
            if i >= 624: mt[0] = mt[623]; i = 1
            if j >= n: j = 0
        for _ in range(623):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941)) - i) & 0xffffffff
            i += 1
            if i >= 624: mt[0] = mt[623]; i = 1
        mt[0] = 0x80000000
        self.mt, self.idx = mt, 624

    def next(self):
        if self.idx >= 624:
            mt = self.mt
            for k in range(624):
                y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7fffffff)
                mt[k] = mt[(k + 397) % 624] ^ (y >> 1) ^ (0x9908b0df if y & 1 else 0)
            self.idx = 0
        y = self.mt[self.idx]; self.idx += 1
        y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680; y ^= (y << 15) & 0xefc60000; y ^= y >> 18
        return y & 0xffffffff


def _rol(x, n): return ((x << n) | (x >> (32 - n))) & 0xffffffff
def _ror(x, n): return ((x >> n) | (x << (32 - n))) & 0xffffffff


def crypt(data, key64):
    """The game's symmetric XOR stream (0x14140e0f0) keyed by 64 bytes."""
    try:
        import numpy  # noqa: F401  (speed: not needed for correctness)
    except ImportError:
        pass
    m = MT(list(struct.unpack("<16I", key64)))
    s = [m.next() for _ in range(4)]
    n4 = len(data) // 4
    words = struct.unpack(f"<{n4}I", data[:n4 * 4])
    out = bytearray(len(data))
    o = []
    for w in words:
        r = m.next()
        o.append(w ^ s[0] ^ s[1] ^ s[2] ^ s[3] ^ r)
        s = [_ror(s[1], 15), _rol(s[2], 11), _rol(s[3], 7), _ror(r, 13)]
    out[:n4 * 4] = struct.pack(f"<{n4}I", *o)
    if len(data) % 4:
        r = m.next()
        ks = struct.pack("<I", s[0] ^ s[1] ^ s[2] ^ s[3] ^ r)
        for i in range(len(data) - n4 * 4):
            out[n4 * 4 + i] = data[n4 * 4 + i] ^ ks[i]
    return bytes(out)


def _xor(a, b): return bytes(x ^ y for x, y in zip(a, b))


def _digest(sec):
    """A section's header digest: SHA-512, or 64 zero bytes for an empty section (LIVE has no PNG)."""
    return hashlib.sha512(sec).digest() if sec else bytes(64)


def _subkey(k, v):
    return b"".join(struct.pack("<Q", struct.unpack_from("<Q", k, i)[0] ^ v) for i in range(0, 64, 8))


def open_save(raw, title=None):
    """{'digests', 'clear', 'k', 'info', 'sizes', 'sections', 'ok', 'title'} of a save file's bytes."""
    for t in ([title] if title else TITLES):
        th = hashlib.sha512(t.encode("utf-16-le")).digest()
        k0 = _xor(raw[0x100:0x140], th)
        hdr = crypt(raw[:0x140], k0)
        digests = [hdr[i * 64:(i + 1) * 64] for i in range(4)]
        clear = raw[0x100:0x140]
        k = clear
        for d in digests:
            k = _xor(k, d)
        info = crypt(raw[0x140:0x210], _subkey(k, 0xd0))
        s2, s1, s0, s3c = struct.unpack_from("<4I", info, 0x40)
        sizes = [s0, s1, s2, s3c * 2]
        if sum(sizes) + 0x210 > len(raw):
            continue
        secs, p = [], 0x210
        for i, n in enumerate(sizes):
            secs.append(crypt(raw[p:p + n], _subkey(k, i))); p += n
        ok = [_digest(s) == d for s, d in zip(secs, digests)]
        if all(ok):
            return {"title": t, "digests": digests, "clear": clear, "k": k, "info": info, "sizes": sizes,
                    "sections": secs, "ok": ok, "tail": raw[p:]}
    raise ValueError("not a PES 2021 save, or a title this tool doesn't know")


def pack(sv, data):
    """A new container like sv (open_save result) with section 2 = data."""
    secs = list(sv["sections"]); secs[2] = data
    digests = [_digest(s) for s in secs]
    clear = sv["clear"]
    k = clear
    for d in digests:
        k = _xor(k, d)
    th = hashlib.sha512(sv["title"].encode("utf-16-le")).digest()
    hdr = crypt(b"".join(digests) + clear, _xor(clear, th))[:0x100] + clear
    info = bytearray(sv["info"])
    struct.pack_into("<4I", info, 0x40, len(secs[2]), len(secs[1]), len(secs[0]), len(secs[3]) // 2)
    out = hdr + crypt(bytes(info), _subkey(k, 0xd0))
    for i, s in enumerate(secs):
        out += crypt(s, _subkey(k, i))
    return out + sv.get("tail", b"")


def _utf16(b):
    return b.decode("utf-16-le", "replace").split("\0", 1)[0]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "info":
        raw = open(sys.argv[2], "rb").read()
        sv = open_save(raw)
        info = sv["info"]
        print("title key:", sv["title"])
        print("sizes (title, png, data, sid):", sv["sizes"], "file", len(raw), "tail", len(sv["tail"]))
        print("digests ok:", sv["ok"])
        print("info strings:", [s for s in (_utf16(info[i:i + 0x40]) for i in range(0, 0x40, 0x40))],
              info[0x50:0xd0].split(b"\0")[0][:40], _utf16(info[0x50:0xd0]))
        print("display title:", _utf16(sv["sections"][0][:384]))
        print("owner SID:", _utf16(sv["sections"][3]))
        print("data starts:", sv["sections"][2][:64].hex())
    elif cmd == "data":
        sv = open_save(open(sys.argv[2], "rb").read())
        open(sys.argv[3], "wb").write(sv["sections"][2])
        print("wrote", len(sv["sections"][2]), "bytes")
    elif cmd == "pack":
        sv = open_save(open(sys.argv[2], "rb").read())
        out = pack(sv, open(sys.argv[3], "rb").read())
        open(sys.argv[4], "wb").write(out)
        assert open_save(out)["ok"] == [True] * 4
        print("wrote", len(out), "bytes")
    else:
        print(__doc__)
