"""mmap-based helpers for PES2021.exe: sections, RTTI vtables, disasm, xrefs."""
import mmap, struct, re, sys, os
import numpy as np
from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_IMM, X86_OP_MEM

EXE = os.environ.get("PES_EXE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "..", "game", "files", "PES2021.exe")
BASE = 0x140000000

_f = open(EXE, 'rb')
data = mmap.mmap(_f.fileno(), 0, access=mmap.ACCESS_READ)
pe = struct.unpack_from('<I', data, 0x3c)[0]
nsec = struct.unpack_from('<H', data, pe + 6)[0]
optsz = struct.unpack_from('<H', data, pe + 20)[0]
secs = {}
for i in range(nsec):
    o = pe + 24 + optsz + i * 40
    name = data[o:o + 8].rstrip(b'\0').decode()
    vs, va, rs, ro = struct.unpack_from('<IIII', data, o + 8)
    secs[name] = (va, vs, ro, rs)

def va2off(va):
    rva = va - BASE
    for n, (sva, svs, sro, srs) in secs.items():
        if sva <= rva < sva + max(svs, srs):
            return sro + (rva - sva)
    raise ValueError(f"va {va:#x} not mapped")

def off2va(off):
    for n, (sva, svs, sro, srs) in secs.items():
        if sro <= off < sro + srs:
            return BASE + sva + (off - sro)
    raise ValueError

def sec_range(n):
    sva, svs, sro, srs = secs[n]
    return BASE + sva, BASE + sva + svs

def in_sec(n, va):
    lo, hi = sec_range(n)
    return lo <= va < hi

def rd(va, n):
    o = va2off(va)
    return data[o:o + n]

def u8(va): return data[va2off(va)]
def u16(va): return struct.unpack_from('<H', data, va2off(va))[0]
def u32(va): return struct.unpack_from('<I', data, va2off(va))[0]
def u64(va): return struct.unpack_from('<Q', data, va2off(va))[0]

def cstr(va, maxlen=256):
    o = va2off(va)
    e = data.find(b'\0', o, o + maxlen)
    if e < 0: e = o + maxlen
    return data[o:e]

def find_all(pat, sec=None):
    """byte pattern search; returns VAs"""
    out = []
    if sec:
        sva, svs, sro, srs = secs[sec]
        lo, hi = sro, sro + srs
    else:
        lo, hi = 0, len(data)
    p = data.find(pat, lo, hi)
    while p >= 0:
        out.append(off2va(p))
        p = data.find(pat, p + 1, hi)
    return out

# ---- RTTI ----
def mangle(name):
    parts = name.split('::')
    return ('.?AV' + '@'.join(reversed(parts)) + '@@').encode()

def type_descriptor(name):
    for s in ('.data', '.rdata'):
        hits = find_all(mangle(name) + b'\0', s)
        if hits:
            return hits[0] - 16
    return None

def vftables(name):
    """Return list of (vftable_va, offset_in_class, [method vas])"""
    td = type_descriptor(name)
    if td is None: return []
    td_rva = td - BASE
    res = []
    sva, svs, sro, srs = secs['.rdata']
    for col_name_off in find_all(struct.pack('<I', td_rva), '.rdata'):
        col = col_name_off - 12
        if u32(col) != 1: continue
        off_in_class = u32(col + 4)
        vt_ptrs = find_all(struct.pack('<Q', col), '.rdata')
        for p in vt_ptrs:
            vt = p + 8
            methods = []
            while True:
                m = u64(vt + 8 * len(methods))
                if not in_sec('.trace', m): break
                methods.append(m)
                if len(methods) > 200: break
            res.append((vt, off_in_class, methods))
    return res

# ---- disasm ----
md = Cs(CS_ARCH_X86, CS_MODE_64)
md.detail = True

def disasm(va, n=0x200, stop_at_ret=True):
    code = rd(va, n)
    out = []
    for ins in md.disasm(code, va):
        out.append(ins)
        if stop_at_ret and ins.mnemonic in ('ret',) :
            break
    return out

def fmt(ins):
    s = f"{ins.address:#x}: {ins.mnemonic} {ins.op_str}"
    # annotate rip-relative targets / string refs
    for op in ins.operands:
        tgt = None
        if op.type == X86_OP_MEM and op.mem.base == 0x29 or (op.type == X86_OP_MEM and ins.reg_name(op.mem.base) == 'rip'):
            tgt = ins.address + ins.size + op.mem.disp
        elif op.type == X86_OP_IMM and ins.mnemonic.startswith(('call', 'j')):
            tgt = op.imm
        if tgt is not None:
            try:
                if in_sec('.rdata', tgt) or in_sec('.data', tgt):
                    b = rd(tgt, 48)
                    if b[:1] != b'\0' and all(32 <= c < 127 for c in b[:min(6, len(b.split(b'\0')[0]))]) and len(b.split(b'\0')[0]) >= 3:
                        s += f"   ; \"{cstr(tgt, 60).decode('latin1')}\""
                    else:
                        s += f"   ; -> {tgt:#x} = {u64(tgt):#x}"
                else:
                    s += f"   ; -> {tgt:#x}"
            except Exception:
                pass
    return s

def dump(va, n=0x400, stop_at_ret=True):
    for ins in disasm(va, n, stop_at_ret):
        print(fmt(ins))

# ---- function bounds via .pdata ----
_pdata = None
def pdata():
    global _pdata
    if _pdata is None:
        sva, svs, sro, srs = secs['.pdata']
        arr = np.frombuffer(data, dtype="<u4", count=svs // 4, offset=sro).reshape(-1, 3).astype(np.int64)
        _pdata = arr
    return _pdata

def func_bounds(va):
    arr = pdata()
    rva = va - BASE
    idx = np.searchsorted(arr[:, 0], rva, side='right') - 1
    if idx < 0: return None
    s, e, _ = arr[idx]
    if s <= rva < e: return BASE + int(s), BASE + int(e)
    return None

def func_start(va):
    b = func_bounds(va)
    return b[0] if b else None

def dump_func(va, limit=0x2000):
    b = func_bounds(va)
    if b:
        s, e = b
        for ins in md.disasm(rd(s, min(e - s, limit)), s):
            print(fmt(ins))
        print(f"; func {s:#x}-{e:#x}")
    else:
        dump(va)

# ---- xrefs (rip-relative disp32 and call rel32) ----
_D = None
def _xref_index():
    global _D
    if _D is None:
        sva, svs, sro, srs = secs['.trace']
        buf = np.frombuffer(data, dtype=np.uint8, count=srs, offset=sro)
        # int32 little endian at each byte offset
        d = (buf[0:-3].astype(np.int64) | (buf[1:-2].astype(np.int64) << 8)
             | (buf[2:-1].astype(np.int64) << 16) | (buf[3:].astype(np.int64) << 24))
        d = np.where(d >= 2**31, d - 2**32, d)
        # target = BASE + sva + i + 4 + disp  => disp + i == target - BASE - sva - 4
        _D = d + np.arange(len(d), dtype=np.int64)
    return _D

def xrefs(target):
    D = _xref_index()
    sva = secs['.trace'][0]
    key = target - BASE - sva - 4
    idx = np.nonzero(D == key)[0]
    return [BASE + sva + int(i) for i in idx]  # position of disp32 field

def xref_insns(target):
    """Return list of (insn_va, func_start) for instructions referencing target."""
    out = []
    for p in xrefs(target):
        # find instruction start: try backing up 1..8 bytes
        found = None
        for back in range(1, 12):
            s = p - back
            code = rd(s, 16)
            ins = next(md.disasm(code, s), None)
            if ins is None: continue
            if ins.address + ins.size == p + 4 or (ins.address + ins.size == p + 4 + 1) or (ins.address + ins.size == p + 4 + 4):
                # check it really references target
                ok = False
                for op in ins.operands:
                    if op.type == X86_OP_MEM and ins.reg_name(op.mem.base) == 'rip' and ins.address + ins.size + op.mem.disp == target:
                        ok = True
                    if op.type == X86_OP_IMM and op.imm == target and ins.mnemonic.startswith(('call', 'j')):
                        ok = True
                if ok:
                    found = ins
                    break
        if found:
            out.append((found.address, func_start(found.address), fmt(found)))
    return out

def absrefs(target):
    """8-byte pointer refs in .rdata/.data"""
    return find_all(struct.pack('<Q', target), '.rdata') + find_all(struct.pack('<Q', target), '.data')
