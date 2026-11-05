"""Light static-RE helpers on top of pe.py with a cached, mmap'd xref index (shared by all processes).

  python kb.py build                 build the xref cache once (~1.5 GB peak, ~1 min)
  python kb.py str <text>            find string literals containing text + who references them
  python kb.py rtti <substr>         list RTTI classes containing substr (+ vtables)
  python kb.py fn <va>               dump whole function (by .pdata bounds)
  python kb.py callers <va>          functions that call / reference va
  python kb.py refs <va>             instructions that reference va (rip-relative or call/jmp)
"""
import os, re, struct, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pe  # noqa: E402

CACHE = os.environ.get("PES_XREF_CACHE") or os.path.join(HERE, "xref_cache")


def build():
    sva, svs, sro, srs = pe.secs[".trace"]
    buf = np.frombuffer(pe.data, dtype=np.uint8, count=srs, offset=sro)
    d = (buf[0:-3].astype(np.int64) | (buf[1:-2].astype(np.int64) << 8)
         | (buf[2:-1].astype(np.int64) << 16) | (buf[3:].astype(np.int64) << 24))
    d = np.where(d >= 2**31, d - 2**32, d)
    pos = np.arange(len(d), dtype=np.int64)
    tgt = d + pos + (pe.BASE + sva + 4)
    del d
    img_hi = pe.BASE + 0x1c000000
    m = (tgt >= pe.BASE) & (tgt < img_hi)
    tgt, pos = tgt[m], pos[m]
    o = np.argsort(tgt, kind="stable")
    os.makedirs(CACHE, exist_ok=True)
    np.save(os.path.join(CACHE, "tgt.npy"), tgt[o])
    np.save(os.path.join(CACHE, "pos.npy"), (pos[o] + pe.BASE + sva).astype(np.int64))
    print("xref entries:", len(o))


_T = _P = None


def _idx():
    global _T, _P
    if _T is None:
        _T = np.load(os.path.join(CACHE, "tgt.npy"), mmap_mode="r")
        _P = np.load(os.path.join(CACHE, "pos.npy"), mmap_mode="r")
    return _T, _P


def raw_xrefs(target):
    T, P = _idx()
    a, b = np.searchsorted(T, target), np.searchsorted(T, target, side="right")
    return [int(x) for x in P[a:b]]


pe.xrefs = raw_xrefs  # make pe.xref_insns use the cache


def refs(target):
    return pe.xref_insns(target)


def callers(target):
    out = {}
    for ins_va, fs, txt in refs(target):
        out.setdefault(fs, []).append(ins_va)
    return out


def strings(text, secs=(".rdata", ".data")):
    pat = text.encode() if isinstance(text, str) else text
    res = []
    for s in secs:
        sva, svs, sro, srs = pe.secs[s]
        p = pe.data.find(pat, sro, sro + srs)
        while p >= 0:
            st = p
            while st > sro and 32 <= pe.data[st - 1] < 127:
                st -= 1
            e = pe.data.find(b"\0", p)
            res.append((pe.off2va(st), pe.data[st:e].decode("latin1")))
            p = pe.data.find(pat, e, sro + srs)
    return sorted(set(res))


def rtti(sub):
    out = []
    for s in (".data", ".rdata"):
        sva, svs, sro, srs = pe.secs[s]
        for m in re.finditer(rb"\.\?AV([A-Za-z0-9_@]*" + re.escape(sub.encode()) + rb"[A-Za-z0-9_@]*)@@\0",
                             pe.data[sro:sro + srs]):
            parts = m.group(1).decode().split("@")
            out.append("::".join(reversed(parts)))
    return sorted(set(out))


SCHEMAS = os.environ.get("PES_SCHEMAS") or os.path.join(HERE, "..", "..", "..", "..", "server", "pesserver", "schemas.json")


def camel(cmdname):
    import json
    s = json.load(open(SCHEMAS))
    if cmdname in s:
        return s[cmdname]["class"], s[cmdname]
    c = "".join(w.capitalize() for w in cmdname.replace("CMD_", "").split("_"))
    return c, None


_ROOT = None


def _roots():
    """chunk start rva -> root function start rva (UNW_FLAG_CHAININFO chains in .pdata)."""
    global _ROOT
    if _ROOT is None:
        par = {}
        for s, e, u in pe.pdata():
            s, u = int(s), int(u)
            if not s:
                continue
            try:
                if (pe.u8(pe.BASE + u) >> 3) & 4:
                    cnt = pe.u8(pe.BASE + u + 2)
                    par[s] = pe.u32(pe.BASE + u + 4 + ((cnt + 1) & ~1) * 2)
            except Exception:
                pass
        _ROOT = {}
        for s in par:
            x, n = s, 0
            while x in par and n < 20:
                x, n = par[x], n + 1
            _ROOT[s] = x
    return _ROOT


def chunks(va):
    """All (start, end) code chunks of the function containing va (compilers split big functions)."""
    b = pe.func_bounds(va)
    if not b:
        return []
    r = _roots()
    root = r.get(b[0] - pe.BASE, b[0] - pe.BASE)
    return sorted((pe.BASE + int(s), pe.BASE + int(e)) for s, e, u in pe.pdata()
                  if int(s) == root or r.get(int(s)) == root)


def fn_strings(fs, limit=0x6000):
    out = []
    for s, e in chunks(fs):
        out += _chunk_strings(s, e, limit)
    return out


def _chunk_strings(s, e, limit):
    out = []
    for ins in pe.md.disasm(pe.rd(s, min(e - s, limit)), s):
        if "rip" in ins.op_str and ins.mnemonic in ("lea", "mov"):
            for op in ins.operands:
                if op.type == pe.X86_OP_MEM and ins.reg_name(op.mem.base) == "rip":
                    t = ins.address + ins.size + op.mem.disp
                    try:
                        if pe.in_sec(".rdata", t) or pe.in_sec(".data", t):
                            k = pe.cstr(t, 200)
                            if len(k) >= 3 and all(32 <= c < 127 for c in k):
                                out.append((ins.address, k.decode()))
                    except Exception:
                        pass
    return out


def exact_string(s):
    """VAs of the NUL-terminated literal s (preceded by NUL)."""
    return [v + 1 for v in pe.find_all(b"\0" + s.encode() + b"\0", ".rdata")]


def cmdinfo(cmdname):
    cls, sch = camel(cmdname)
    print(f"### {cmdname}  (class {cls})")
    for suffix in ("Option", "Result", "HttpCodec"):
        n = f"commandautomake::Cmd{cls}{suffix}"
        vts = pe.vftables(n)
        print(f"  {n}: " + "; ".join(f"vt {v:#x}@+{off} [{', '.join(hex(m) for m in ms[:5])}]" for v, off, ms in vts))
        if suffix == "Option":
            for v, off, ms in vts:
                if off == 0 and len(ms) > 3 and ms[3] != 0x140ca1a50:
                    keys = [k for _, k in fn_strings(ms[3]) if k.endswith("=") or k.isidentifier()]
                    print(f"    HTTP request serializer {ms[3]:#x}: {keys}")
                if off == 280 and len(ms) > 1 and ms[1] != 0x140ca1a50:
                    keys = [k for _, k in fn_strings(ms[1]) if k.isidentifier()]
                    print(f"    gate (msgpack) request packer {ms[1]:#x}: {keys}")
    if sch:
        print(f"  reply parser {sch['parser']}: {sch['fields']}")
    for lit in (f"Cmd{cls}", cmdname):
        for va in exact_string(lit):
            for fs, sites in callers(va).items():
                strs = [k for _, k in fn_strings(fs) if not k.startswith("D:\\")]
                src = sorted({k.split("\\")[-1] for _, k in fn_strings(fs) if k.startswith("D:\\")})
                print(f"  used by fn {fs and hex(fs)} ({lit} @ {', '.join(hex(x) for x in sites)}) src={src}")
                print(f"     strings: {strs[:40]}")


ERR_FN = 0x1411546b0


def errsites():
    """Every call to the error reporter: (file, line, code arg) + the next message string."""
    import json
    rows = []
    for ins_va, fs, txt in refs(ERR_FN):
        b = pe.func_bounds(ins_va)
        if not b:
            continue
        L = list(pe.md.disasm(pe.rd(max(b[0], ins_va - 0x80), ins_va - max(b[0], ins_va - 0x80) + 0x80), max(b[0], ins_va - 0x80)))
        line = code = None
        for x in L:
            if x.address >= ins_va:
                break
            if x.mnemonic == "mov" and x.op_str.startswith("r9d, 0x"):
                line = int(x.op_str.split(", ")[1], 16)
            if x.mnemonic == "mov" and x.op_str.startswith("dword ptr [rsp + 0x20], "):
                try:
                    code = int(x.op_str.split(", ")[1], 16)
                except ValueError:
                    pass
        strs = fn_strings(fs)
        before = [k for a, k in strs if a < ins_va and k.startswith("D:\\")]
        after = [k for a, k in strs if a > ins_va and not k.startswith("D:\\")]
        src = before[-1].split("\\")[-1] if before else None
        rows.append({"site": hex(ins_va), "fn": fs and hex(fs), "file": src, "line": line, "code": code,
                     "msg": after[0] if after else None})
    return rows


def main():
    cmd, *a = sys.argv[1:]
    if cmd == "build":
        build()
    elif cmd == "cmd":
        for c in a:
            cmdinfo(c)
    elif cmd == "errs":
        import json
        print(json.dumps(errsites(), indent=0))
    elif cmd == "str":
        for va, s in strings(a[0]):
            print(f"{va:#x} {s!r}")
            for iva, fs, txt in refs(va):
                print(f"    ref {iva:#x} in fn {fs and hex(fs)}")
    elif cmd == "rtti":
        for n in rtti(a[0]):
            vts = pe.vftables(n)
            print(n, [(hex(v), off, [hex(m) for m in ms[:6]]) for v, off, ms in vts])
    elif cmd == "fn":
        pe.dump_func(int(a[0], 16), int(a[1], 16) if len(a) > 1 else 0x3000)
    elif cmd == "callers":
        for fs, sites in callers(int(a[0], 16)).items():
            print(f"fn {fs and hex(fs)}: {[hex(s) for s in sites]}")
    elif cmd == "refs":
        for iva, fs, txt in refs(int(a[0], 16)):
            print(f"{fs and hex(fs)}  {txt}")


if __name__ == "__main__":
    main()
