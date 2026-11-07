#!/usr/bin/env python3
"""Minimal CRI CPK writer (uncompressed, TOC-only) shaped like PES 2021's own CPKs.

Layout copied from download/dt80_100E_x64.cpk (CPKMC2.49.32):
  0x000  'CPK ' chunk: u32 0, u64 len, masked @UTF 'CpkHeader' (44 cols, same flags as official)
  0x7fa  '(c)CRI'
  0x800  'TOC ' chunk: u32 0, u64 len, masked @UTF 'CpkTocInfo'
         cols DirName str, FileName str, FileSize u32, ExtractSize u32, FileOffset u64, ID u32,
         UserString const str '<NULL>'
  ContentOffset = align(0x800 + TocSize, 0x800); files aligned to 0x800; FileOffset is relative to
  TocOffset (0x800). Rows sorted by upper-cased full path (as official). EnabledPackedSize =
  EnabledDataSize = 2*sum(FileSize) (official quirk). @UTF tables masked with the standard CRI XOR.

usage: cpkwrite.py out.cpk SRC_DIR            (every file under SRC_DIR, path relative to it)
       cpkwrite.py out.cpk inpath=cpkpath ...  (explicit mapping)
"""
import os, struct, sys

ALIGN = 0x800
# type codes
U8, I8, U16, I16, U32, I32, U64, I64, F32, F64, STR, DATA = range(12)
FMT = {U8: 'B', I8: 'b', U16: 'H', I16: 'h', U32: 'I', I32: 'i', U64: 'Q', I64: 'q', F32: 'f', F64: 'd'}
ZERO, CONST, ROW = 0x10, 0x30, 0x50


def mask(b):
    out = bytearray(len(b)); m = 0x655f
    for i, c in enumerate(b):
        out[i] = c ^ (m & 0xff); m = (m * 0x4115) & 0xffffffff
    return bytes(out)


def utf_table(name, columns, rows):
    """columns: list of (name, storage, type, const_value); rows: list of dicts."""
    strings = bytearray(b'<NULL>\0')
    stridx = {'<NULL>': 0}
    data = bytearray()

    def s(x):
        if x not in stridx:
            stridx[x] = len(strings); strings.extend(x.encode('utf-8') + b'\0')
        return stridx[x]

    tname = s(name)

    def cell(t, v):
        if t in FMT:
            return struct.pack('>' + FMT[t], v)
        if t == STR:
            return struct.pack('>I', s(v))
        if t == DATA:
            o = len(data); data.extend(v); return struct.pack('>II', o, len(v))
        raise ValueError(t)

    schema = bytearray()
    for cname, st, t, const in columns:
        schema += struct.pack('>BI', st | t, s(cname))
        if st == CONST:
            schema += cell(t, const)
    rowbuf = bytearray()
    rowlen = 0
    for i, r in enumerate(rows):
        start = len(rowbuf)
        for cname, st, t, const in columns:
            if st == ROW:
                rowbuf += cell(t, r[cname])
        rowlen = len(rowbuf) - start
    rows_off = 0x18 + len(schema)            # offsets relative to byte 8 of the @UTF table
    str_off = rows_off + len(rowbuf)
    strpad = (-(str_off + len(strings))) % 8
    data_off = str_off + len(strings) + strpad
    size = data_off + len(data)
    hdr = struct.pack('>4sIIIIIHHI', b'@UTF', size, rows_off, str_off, data_off, tname,
                      len(columns), rowlen, len(rows))
    t = hdr + schema + rowbuf + strings + bytes(strpad) + data
    assert len(t) == size + 8
    return t


def chunk(magic, table):
    return magic + struct.pack('<IQ', 0, len(table)) + mask(table)


def align(x, a=ALIGN):
    return (x + a - 1) // a * a


def write_cpk(out_path, files, tvers='CPKMC2.49.32, DLL3.24.00'):
    """files: list of (cpk_path, local_path)."""
    files = sorted(files, key=lambda f: f[0].upper())
    names = [f[0] for f in files]
    assert len(set(n.upper() for n in names)) == len(names), 'duplicate paths'
    sizes = [os.path.getsize(f[1]) for f in files]
    toc_cols = [('DirName', ROW, STR, None), ('FileName', ROW, STR, None), ('FileSize', ROW, U32, None),
                ('ExtractSize', ROW, U32, None), ('FileOffset', ROW, U64, None), ('ID', ROW, U32, None),
                ('UserString', CONST, STR, '<NULL>')]

    def toc_rows(content_off):
        rows, pos = [], content_off
        for i, (p, sz) in enumerate(zip(names, sizes)):
            d, _, f = p.rpartition('/')
            rows.append(dict(DirName=d, FileName=f, FileSize=sz, ExtractSize=sz, FileOffset=pos - ALIGN, ID=i))
            pos = align(pos + sz)
        return rows, pos

    # TOC size does not depend on offsets (fixed-width cells), so build once to size it
    toc_tab = utf_table('CpkTocInfo', toc_cols, toc_rows(ALIGN)[0])  # any offset >= ALIGN (cells are fixed width)
    toc_size = 16 + len(toc_tab)
    content_off = align(ALIGN + toc_size)
    rows, end = toc_rows(content_off)
    toc_bytes = chunk(b'TOC ', utf_table('CpkTocInfo', toc_cols, rows))
    assert len(toc_bytes) == toc_size
    total = sum(sizes)
    H = [
        ('UpdateDateTime', ROW, U64, 0), ('FileSize', ZERO, U64, None), ('ContentOffset', ROW, U64, content_off),
        ('ContentSize', ROW, U64, end - content_off), ('TocOffset', ROW, U64, ALIGN), ('TocSize', ROW, U64, toc_size),
        ('TocCrc', ZERO, U32, None), ('HtocOffset', ZERO, U64, None), ('HtocSize', ZERO, U64, None),
        ('EtocOffset', ZERO, U64, None), ('EtocSize', ZERO, U64, None), ('ItocOffset', ZERO, U64, None),
        ('ItocSize', ZERO, U64, None), ('ItocCrc', ZERO, U32, None), ('GtocOffset', ZERO, U64, None),
        ('GtocSize', ZERO, U64, None), ('GtocCrc', ZERO, U32, None), ('HgtocOffset', ZERO, U64, None),
        ('HgtocSize', ZERO, U64, None), ('EnabledPackedSize', ROW, U64, 2 * total),
        ('EnabledDataSize', ROW, U64, 2 * total), ('TotalDataSize', ZERO, U64, None), ('Tocs', ZERO, U32, None),
        ('Files', ROW, U32, len(files)), ('Groups', ROW, U32, 0), ('Attrs', ROW, U32, 0),
        ('TotalFiles', ZERO, U32, None), ('Directories', ZERO, U32, None), ('Updates', ZERO, U32, None),
        ('Version', ROW, U16, 7), ('Revision', ROW, U16, 14), ('Align', ROW, U16, ALIGN), ('Sorted', ROW, U16, 1),
        ('EnableFileName', ROW, U16, 1), ('EID', ZERO, U16, None), ('CpkMode', ROW, U32, 1),
        ('Tvers', ROW, STR, tvers), ('Comment', ZERO, STR, None), ('Codec', ROW, U32, 0), ('DpkItoc', ROW, U32, 0),
        ('EnableTocCrc', ROW, U16, 0), ('EnableFileCrc', ROW, U16, 0), ('CrcMode', ROW, U32, 0),
        ('CrcTable', ROW, DATA, b''),
    ]
    cols = [(n, st, t, None) for n, st, t, v in H]
    hdr = chunk(b'CPK ', utf_table('CpkHeader', cols, [{n: v for n, st, t, v in H}]))
    assert len(hdr) <= ALIGN - 6
    with open(out_path, 'wb') as o:
        o.write(hdr + bytes(ALIGN - 6 - len(hdr)) + b'(c)CRI')
        o.write(toc_bytes)
        o.write(bytes(content_off - ALIGN - toc_size))
        for (p, local), sz, r in zip(files, sizes, rows):
            assert o.tell() == r['FileOffset'] + ALIGN
            with open(local, 'rb') as f:
                while True:
                    b = f.read(1 << 20)
                    if not b:
                        break
                    o.write(b)
            o.write(bytes(align(o.tell()) - o.tell()))
    return out_path


def collect(src):
    out = []
    for root, _, fs in os.walk(src):
        for fn in fs:
            lp = os.path.join(root, fn)
            out.append((os.path.relpath(lp, src).replace(os.sep, '/'), lp))
    return out


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out, args = sys.argv[1], sys.argv[2:]
    if len(args) == 1 and os.path.isdir(args[0]):
        fl = collect(args[0])
    else:
        fl = [(b, a) for a, b in (x.split('=', 1) for x in args)]
    write_cpk(out, fl)
    for p, l in sorted(fl, key=lambda f: f[0].upper()):
        print(f'  {p}  <- {l}')
    print('wrote', out, os.path.getsize(out), 'bytes,', len(fl), 'files')
