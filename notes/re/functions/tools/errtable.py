"""All error-report sites -> on-screen code (4-letter file hash + line), file, function name arg, sub-code,
and the nearest message strings. Output JSON list."""
import json, sys
import kb, pe

def tag(name):
    b = [0, 0, 0, 0]
    for i, c in enumerate(name.encode()):
        b[i % 4] += c
    return "".join(chr(x % 26 + 65) for x in b)

rows = []
for ins_va, fs, _ in kb.refs(kb.ERR_FN):
    b = pe.func_bounds(ins_va)
    if not b:
        continue
    L = list(pe.md.disasm(pe.rd(b[0], b[1] - b[0]), b[0]))
    idx = next((i for i, x in enumerate(L) if x.address == ins_va), None)
    if idx is None:
        continue
    line = code = None
    strs = []
    for x in L[max(0, idx - 40):idx]:
        if x.mnemonic == "mov" and x.op_str.startswith("r9d, "):
            try: line = int(x.op_str.split(", ")[1], 16)
            except ValueError: pass
        if x.mnemonic == "mov" and x.op_str.startswith("dword ptr [rsp + 0x20], "):
            try: code = int(x.op_str.split(", ")[1], 16)
            except ValueError: pass
        if x.mnemonic == "lea" and "rip" in x.op_str:
            t = x.address + x.size + x.operands[1].mem.disp
            try:
                s = pe.cstr(t, 200)
                if len(s) >= 2 and all(32 <= c < 127 for c in s): strs.append(s.decode())
            except Exception: pass
    files = [s for s in strs if s.startswith("D:\\")]
    fname = files[-1].split("\\")[-1] if files else None
    func = [s for s in strs if not s.startswith("D:\\")]
    # message: first non-path string after the call within 30 insns
    msg = None
    for x in L[idx + 1: idx + 40]:
        if x.mnemonic == "lea" and "rip" in x.op_str:
            t = x.address + x.size + x.operands[1].mem.disp
            try:
                s = pe.cstr(t, 200)
                if len(s) >= 3 and all(32 <= c < 127 for c in s) and not s.startswith(b"D:\\"):
                    msg = s.decode(); break
            except Exception: pass
    rows.append({"code": (tag(fname) + str(line)) if fname and line else None, "file": fname, "line": line,
                 "sub": code, "func": func[-1] if func else None, "msg": msg, "site": hex(ins_va), "fn": hex(b[0])})
rows.sort(key=lambda r: (r["file"] or "", r["line"] or 0))
json.dump(rows, open(sys.argv[1], "w"), indent=0)
print(len(rows), sum(1 for r in rows if r["code"]))
