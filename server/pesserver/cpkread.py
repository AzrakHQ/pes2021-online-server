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


def read_file(path, inner):
    """Bytes of one stored (uncompressed) file inside the CPK, or None."""
    _, rows = toc(path)
    for r in rows:
        if name(r) == inner and r.get('FileSize') == r.get('ExtractSize'):
            with open(path, 'rb') as f:
                f.seek(r['_abs'])
                return f.read(r['FileSize'])
    return None
