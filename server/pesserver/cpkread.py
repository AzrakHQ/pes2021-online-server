"""Minimal reader for CRI CPK archives (the game's data and Live Update files): table of contents and one file.
Copied from notes/re/functions/tools/cpk.py, where the format is described."""
import struct


def unmask(b):
    out = bytearray(len(b)); m = 0x655f
    for i, c in enumerate(b):
        out[i] = c ^ (m & 0xff); m = (m * 0x4115) & 0xffffffff
    return bytes(out)

def read_utf(raw):
    if raw[:4] != b'@UTF':
        raw = unmask(raw)
    assert raw[:4] == b'@UTF', raw[:4]
    size, rows_off, str_off, data_off, name_off, ncols, row_len, nrows = struct.unpack('>IIIIIHHI', raw[4:32])
    t = raw[8:8 + size]
    strings = t[str_off:data_off]
    def s(o):
        e = strings.index(b'\0', o); return strings[o:e].decode('utf-8', 'replace')
    cols = []; p = 24
    fmt = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'Q', 7: 'q', 8: 'f', 9: 'd'}
    def rd(p, typ):
        if typ in fmt:
            n = struct.calcsize('>' + fmt[typ]); return struct.unpack_from('>' + fmt[typ], t, p)[0], p + n
        if typ == 0xa:
            return s(struct.unpack_from('>I', t, p)[0]), p + 4
        if typ == 0xb:
            o, l = struct.unpack_from('>II', t, p); return (data_off + o, l), p + 8
        raise ValueError(typ)
    for _ in range(ncols):
        flags = t[p]; p += 1
        name = s(struct.unpack_from('>I', t, p)[0]); p += 4
        st, typ = flags & 0xf0, flags & 0x0f
        const = None
        if st == 0x30:
            const, p = rd(p, typ)
        cols.append((name, st, typ, const))
    rows = []
    for r in range(nrows):
        p = rows_off + r * row_len; row = {}
        for name, st, typ, const in cols:
            if st == 0x50:
                row[name], p = rd(p, typ)
            elif st == 0x30:
                row[name] = const
            else:
                row[name] = None
        rows.append(row)
    return rows

def toc(path):
    with open(path, 'rb') as f:
        h = f.read(16); assert h[:4] == b'CPK ', h
        size = struct.unpack('<Q', h[8:16])[0]
        hdr = read_utf(f.read(size))[0]
        tocoff = hdr.get('TocOffset'); contentoff = hdr.get('ContentOffset')
        if not tocoff:
            return hdr, []
        f.seek(tocoff); h = f.read(16); size = struct.unpack('<Q', h[8:16])[0]
        rows = read_utf(f.read(size))
        base = min(tocoff, contentoff) if contentoff else tocoff
        for r in rows:
            r['_abs'] = base + r['FileOffset']
        return hdr, rows

def name(r):
    d = r.get('DirName') or ''
    return (d + '/' if d else '') + (r.get('FileName') or '')


def crilayla(src, expect=None):
    """Decompress a CRILAYLA block (how CPK tools compress files; patch makers' CPKs use it). Layout: 'CRILAYLA',
    u32 size, u32 offset of the 0x100-byte header kept in clear (at 0x10 + offset, after the compressed data). The
    data is read as a bit stream backwards from its end and written backwards: 1 bit = a copy (13-bit distance - 3,
    length 3 + 2/3/5/8-bit steps then 8-bit steps while all ones), 0 = a literal byte."""
    if src[:8] != b'CRILAYLA' or len(src) < 0x110:
        raise ValueError('not CRILAYLA data')
    size, hoff = struct.unpack_from('<II', src, 8)
    if 0x10 + hoff + 0x100 > len(src):
        raise ValueError('CRILAYLA data cut short')
    # (checked before the output is made: a damaged header must not make us reserve gigabytes; a long copy
    # adds at most 255 bytes per 8 input bits, so 256 bytes of output per input byte is the most there can be)
    if (expect is not None and 0x100 + size != expect) or size > 256 * len(src) + 0x1000:
        raise ValueError('bad CRILAYLA size')
    out = bytearray(0x100 + size)
    out[:0x100] = src[0x10 + hoff:0x10 + hoff + 0x100]
    pos = 0x10 + hoff - 1                       # next input byte (backwards)
    pool = nbits = 0
    w = 0x100 + size - 1                        # next output byte (backwards)
    end = 0x100

    def bits(n):
        nonlocal pos, pool, nbits
        while nbits < n:
            if pos < 0x10:
                raise ValueError('CRILAYLA data cut short')
            pool = (pool << 8) | src[pos]
            pos -= 1
            nbits += 8
        nbits -= n
        v = (pool >> nbits) & ((1 << n) - 1)
        pool &= (1 << nbits) - 1
        return v

    while w >= end:
        if bits(1):
            src_at = w + bits(13) + 3
            n = 3
            for width in (2, 3, 5, 8):
                step = bits(width)
                n += step
                if step != (1 << width) - 1:
                    break
            else:
                while True:
                    step = bits(8)
                    n += step
                    if step != 0xff:
                        break
            if src_at >= len(out) or w - n + 1 < end:
                raise ValueError('bad CRILAYLA data')
            for _ in range(n):
                out[w] = out[src_at]
                w -= 1
                src_at -= 1
        else:
            out[w] = bits(8)
            w -= 1
    return bytes(out)


def read_entry(path, r):
    """Bytes of the file of TOC row r (decompressed if the CPK compresses it)."""
    with open(path, 'rb') as f:
        f.seek(r['_abs'])
        data = f.read(r['FileSize'])
    if len(data) != r['FileSize']:
        raise ValueError(f'{name(r)}: the CPK is cut short')
    if r.get('FileSize') != r.get('ExtractSize'):
        data = crilayla(data, r.get('ExtractSize'))
        if len(data) != r.get('ExtractSize'):
            raise ValueError(f'{name(r)}: unpacked to {len(data)} bytes, not {r.get("ExtractSize")}')
    return data


def read_file(path, inner):
    """Bytes of one file inside the CPK (decompressed if the CPK compresses it), or None."""
    _, rows = toc(path)
    for r in rows:
        if name(r) == inner:
            return read_entry(path, r)
    return None
