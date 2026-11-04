#!/usr/bin/env python3
"""Bulk-extract every NCL command's RESPONSE schema from PES2021.exe (1.01.00), statically.

Each command has an RTTI class `Cmd<Name>Result@commandautomake`; vtable slot 2 is its parser. The
msgpack variant unpacks the message with 0x141c190a0, then loops over map entries comparing keys with
memcmp (0x1415a1726) against string literals. For each key we classify how the value is consumed:

  LIST  value is an array (stride 0x18) whose elements are msgpack blobs, each unpacked again
  OBJ   value is a single msgpack blob, unpacked again (nested object)
  NUM   value read as a number (int, or double via cvttsd2si)
  STR   value copied as a string (ptr, len)

Output: JSON {CMD_NAME: {"class": ..., "parser": "0x...", "fields": {key: TYPE}}}. Fields of nested
objects appear in the same flat map (the top-level loop ignores keys it doesn't compare, so sending
extra keys is harmless). Usage: python3 extract_schemas.py out.json   (needs capstone + numpy)
"""
import json, re, sys
sys.path.insert(0, __import__("os").path.join(__import__("os").path.dirname(__file__), "keyex"))
import pe  # noqa: E402

UNPACK, MEMCMP, STRCOPY = 0x141c190a0, 0x1415a1726, 0x140409430


def class_names():
    out = set()
    for sec in (".data", ".rdata"):
        sva, svs, sro, srs = pe.secs[sec]
        buf = bytes(pe.data[sro:sro + srs])
        for m in re.finditer(rb"\.\?AVCmd([A-Za-z0-9_]+?)Result@commandautomake@@\0", buf):
            out.add(m.group(1).decode())
    return sorted(out)


def parse_fns(cls):
    fns = []
    for vt, off, methods in pe.vftables(f"commandautomake::Cmd{cls}Result"):
        if len(methods) > 2:
            fns.append(methods[2])
    return fns


def calls(ins_list, target):
    return any(i.mnemonic == "call" and i.op_str == hex(target) for i in ins_list)


def nested_kind(blk):
    """At the first nested unpack in the block, look at how (ptr, size) were loaded: a single nested
    object comes straight from the value object ([v+0x28] ptr, [v+0x20] size); list elements are
    0x18-byte msgpack objects iterated in an array ([e+0x10] ptr, [e+8] size)."""
    for j, x in enumerate(blk):
        if x.mnemonic == "call" and x.op_str == hex(UNPACK):
            pre = " ".join(f"{y.mnemonic} {y.op_str}" for y in blk[max(0, j - 7):j])
            if re.search(r"mov rdx, qword ptr \[\w+ \+ 0x10\]", pre):
                return "LIST"
            if re.search(r"mov rdx, qword ptr \[\w+ \+ 0x28\]", pre):
                return "OBJ"
            return "OBJ?"
    return None


def keys_and_types(fva):
    b = pe.func_bounds(fva)
    if not b:
        return None
    s, e = b
    L = list(pe.disasm(s, e - s, stop_at_ret=False))
    if not calls(L, UNPACK):
        return None
    # key compare sites: lea rdx,[rip+str] followed shortly by call memcmp
    sites = []
    for i, ins in enumerate(L):
        if ins.mnemonic != "lea" or "rip" not in ins.op_str:
            continue
        op = ins.operands[1]
        t = ins.address + ins.size + op.mem.disp
        try:
            k = pe.cstr(t, 64)
        except Exception:
            continue
        if not (1 <= len(k) < 64 and all(32 <= c < 127 for c in k)) or k == b"NotImplement":
            continue
        if any(x.mnemonic == "call" and x.op_str == hex(MEMCMP) for x in L[i + 1:i + 4]):
            sites.append((i, k.decode()))
    fields = {}
    for n, (i, key) in enumerate(sites):
        end = sites[n + 1][0] if n + 1 < len(sites) else min(i + 80, len(L))
        blk = L[i:end]
        txt = "\n".join(f"{x.mnemonic} {x.op_str}" for x in blk)
        kind = nested_kind(blk)
        if kind:
            t = kind
        elif "cvttsd2si" in txt or re.search(r"cmp e\w+, 4\b", txt):
            t = "NUM"
        elif calls(blk, STRCOPY):
            t = "STR"
        else:
            t = "?"
        fields.setdefault(key, t)
    return fields


def to_cmd(cls, known):
    norm = {k.replace("CMD_", "").replace("_", "").lower(): k for k in known}
    return norm.get(cls.lower()) or "CMD_" + re.sub(r"(?<!^)(?=[A-Z])", "_", cls).upper()


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "schemas.json"
    exe_strings = bytes(pe.data)
    known = sorted({m.decode() for m in re.findall(rb"\0(CMD_[A-Z0-9_]{3,60})\0", exe_strings)})
    res = {}
    for cls in class_names():
        for f in parse_fns(cls):
            kt = keys_and_types(f)
            if kt:
                name = to_cmd(cls, known)
                b = pe.func_bounds(f)
                res.setdefault(name, {"class": cls, "parser": hex(f), "parser_end": hex(b[1]), "fields": kt})
                break
    json.dump(res, open(out_path, "w"), indent=1, sort_keys=True)
    print(f"{len(res)} command schemas -> {out_path}")


if __name__ == "__main__":
    main()
