"""Usage: python classmap.py out.json  (~2 min).
All RTTI classes in PES2021.exe with their base classes and primary vtable (MSVC x64 RTTI).
COL = {u32 sig=1, u32 offset, u32 cdOffset, i32 rvaTypeDesc, i32 rvaClassHier, i32 rvaSelf}."""
import json, sys, struct, collections
import numpy as np
import pe
B = pe.BASE
va, vs, ro, rs = pe.secs[".rdata"]
buf = np.frombuffer(pe.data, dtype="<u4", count=rs // 4, offset=ro)
# candidate COLs: sig==1 at index i, self rva at i+5 equal to its own rva
idx = np.nonzero(buf[:-5] == 1)[0]
own = (va + idx * 4).astype(np.uint64)
cols = idx[buf[idx + 5] == own]
def tname(rva):
    s = pe.cstr(B + rva + 16, 300)
    if not s.startswith(b".?A"):
        return None
    n = s[4:].decode("latin1")
    n = n[:-2] if n.endswith("@@") else n
    return "::".join(reversed(n.split("@")))
classes = {}
for i in cols:
    col = va + int(i) * 4
    sig, off, cdo, td, ch, me = struct.unpack_from("<IIIiii", pe.data, ro + int(i) * 4)
    name = tname(td)
    if not name:
        continue
    vt = [p + 8 for p in pe.find_all(struct.pack("<Q", B + col), ".rdata")]
    c = classes.setdefault(name, {"vtables": [], "bases": []})
    c["vtables"] += [hex(v) for v in vt]
    if off == 0 and not c["bases"]:
        attr, nb, arr = struct.unpack_from("<III", pe.data, pe.va2off(B + ch) + 4)
        for k in range(1, min(nb, 64)):
            bcd = pe.u32(B + arr + 4 * k)
            bn = tname(pe.u32(B + bcd))
            if bn:
                c["bases"].append(bn)
json.dump(classes, open(sys.argv[1], "w"), indent=0, sort_keys=True)
ns = collections.Counter(n.split("::")[0] if "::" in n else "(global)" for n in classes)
print(len(cols), "COLs;", len(classes), "classes with vtables;", sum(1 for c in classes.values() if c["bases"]), "with bases")
print(ns.most_common(15))
