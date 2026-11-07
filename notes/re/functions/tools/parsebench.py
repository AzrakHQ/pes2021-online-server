#!/usr/bin/env python3
"""parsebench.py — run the game's own reply parser on our bytes, inside the running game (probe build).

  parsebench.py PORT CMD SRC [SRC2]        parse SRC (and SRC2), print the header fields and, with two sources,
                                           every 8-byte word of the parsed object that differs
  parsebench.py PORT CMD SRC --words       print every non-zero word of the parsed object
  … --set PATH=JSON --del PATH             edit the LAST source first (testserver/replypatch.py paths:
                                           "a.b.0.c", through nested blobs; JSON may use {"__blob__": …})

SRC: "last" = our server's last reply to CMD (logs/replies.jsonl, written by testserver/replypatch.py),
     "last~N" = the Nth before it, "hex:84a5…" = msgpack hex, "json:{…}" = a reply as JSON (with the server's
     {"__blob__": …} notation), or a file of raw msgpack.
Offsets are printed relative to the parser's `this` (Result+0x118), the same offsets the KB parser notes use.

How (notes/re/functions/parsebench.md): every step is a gcall on the game thread (the game allocator needs it):
operator new(size) → the Result class ctor → a fake 0x30-byte buffer {data, 0, 0, len, len, 0} →
0x14184d960(Result, buffer, 1), the function the client uses for every reply → read → scalar deleting dtor.
Class, size, ctor, dtor and parser of all 398 reply classes: result_table.json. The probe catches a crash
inside a gcall ("FAULT"), but after a fault the game is tainted (leaked memory, maybe a held lock): restart it.
"""
import json, os, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from probe import Probe  # noqa: E402

NEW = 0x140227920        # operator new(size)
DELETE = 0x140227930     # operator delete(ptr)
PARSE = 0x14184d960      # (Result*, Buffer*, mode): mode 1 = msgpack; 0 = ok, 0xffffff37 = empty body
TABLE = json.load(open(os.path.join(HERE, "result_table.json")))
LOGS = os.path.join(os.environ.get("PES_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(HERE))))), "logs")


class Fault(Exception):
    pass


def gcall(p, fn, *args, timeout=10.0):
    p.gcall(fn, *args)
    ok, bad = f"gcall {fn:x} -> ", f"gcall {fn:x} FAULT"
    end = time.time() + timeout
    while time.time() < end:
        for e in p.poll():
            if e.startswith(ok):
                return int(e[len(ok):], 16)
            if e.startswith(bad):
                raise Fault(e)
        time.sleep(0.03)
    raise TimeoutError(f"no result for gcall {fn:#x} (is the game's window thread running?)")


def sstr(p, a):
    raw = p.read(f"@{a:x}", 32)
    size, cap = struct.unpack_from("<QQ", raw, 16)
    if size > 4096 or cap < size:
        return None
    if cap < 16:
        return raw[:size].decode("utf-8", "replace")
    ptr = struct.unpack_from("<Q", raw, 0)[0]
    try:
        return p.read(f"@{ptr:x}", size).decode("utf-8", "replace") if size else ""
    except Exception:  # noqa: BLE001
        return None


def source(cmd, src):
    import msgpack
    sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "server"))
    if src.startswith("hex:"):
        return bytes.fromhex(src[4:])
    if src.startswith("json:"):
        from pesserver import pescmds
        body = pescmds._unjson(json.loads(src[5:]))
        body.setdefault("msgid", cmd)
        return msgpack.packb(body, use_bin_type=True)
    if src.startswith("last"):
        back = int(src[5:]) if src.startswith("last~") else 0
        rows = []
        for line in open(os.path.join(LOGS, "replies.jsonl")):
            if f'"{cmd}"' in line:
                rows.append(json.loads(line))
        if len(rows) <= back:
            sys.exit(f"no reply #{back} to {cmd} in {LOGS}/replies.jsonl")
        body = msgpack.unpackb(bytes.fromhex(rows[-1 - back]["body_hex"]), raw=False, strict_map_key=False)
        # the wire map starts with msgid and rqid (our codec), as the game receives it
        return msgpack.packb({"msgid": cmd, **body}, use_bin_type=True)
    return open(src, "rb").read()


def edit(body, edits):
    import msgpack
    sys.path.insert(0, os.path.join(HERE, "testserver"))
    os.environ.setdefault("PATCH_CTL", os.devnull)
    import replypatch
    obj = msgpack.unpackb(body, raw=False, strict_map_key=False)
    for op, arg in edits:
        if op == "set":
            path, _, v = arg.partition("=")
            obj = replypatch.edit(obj, path, "set", replypatch.value(json.loads(v)))
        else:
            obj = replypatch.edit(obj, arg, "del")
    return msgpack.packb(obj, use_bin_type=True)


def bench(p, cmd, body):
    """Parse body with the game's reader for cmd; returns (header, snapshot bytes of the parsed object)."""
    t = TABLE[cmd]
    size, ctor, dtor = int(t["size"][0], 16), int(t["ctor"], 16), int(t["dtor"], 16)
    R = gcall(p, NEW, size)
    D = F = 0
    try:
        for o in range(0, size, 0x400):                 # zero first: fields the ctor leaves alone stay 0
            p.write(f"@{R + o:x}", bytes(min(0x400, size - o)))
        gcall(p, ctor, R)
        D = gcall(p, NEW, max(len(body), 1))
        for i in range(0, len(body), 1024):
            p.write(f"@{D + i:x}", body[i:i + 1024])
        F = gcall(p, NEW, 0x30)
        p.write(f"@{F:x}", struct.pack("<6Q", D, 0, 0, len(body), len(body), 0))
        rc = gcall(p, PARSE, R, F, 1) & 0xffffffff
        snap = b"".join(p.read(f"@{R + o:x}", min(0x400, size - o)) for o in range(0, size, 0x400))
        head = {"class": t["result_class"], "size": hex(size), "rc": hex(rc), "msgid": sstr(p, R + 0x140),
                "rqid": p.u32(f"@{R + 0x160:x}"), "result": sstr(p, R + 0x1b0), "body_len": len(body)}
        return head, snap, R
    finally:
        try:
            gcall(p, dtor, R, 1)
            D and gcall(p, DELETE, D)
            F and gcall(p, DELETE, F)
        except Fault as e:
            print("free:", e, file=sys.stderr)


def words(snap):
    return [(o - 0x118, struct.unpack_from("<Q", snap, o)[0]) for o in range(0x118, len(snap) - 7, 8)]


def text(*vs):
    """'ab..' rendering when a word holds printable text (inline std::string bytes)."""
    out = []
    for v in vs:
        b = struct.pack("<Q", v).rstrip(b"\0")
        if len(b) >= 2 and all(32 <= c < 127 for c in b):
            out.append(repr(b.decode()))
        else:
            out.append("")
    return "   " + " -> ".join(out) if any(out) else ""


def show(w):
    off, v = w
    s = f"this+{off:#05x}: {v:#018x}"
    lo, hi = v & 0xffffffff, v >> 32
    if v and v < 1 << 40:
        s += f"  (u32 {lo}, {hi})"
    return s + text(v)


def main():
    a = sys.argv[1:]
    if len(a) < 3:
        sys.exit(__doc__)
    edits = []
    for i, x in enumerate(a):
        if x in ("--set", "--del") and i + 1 < len(a):
            edits.append((x[2:], a[i + 1]))
    skip = {i + 1 for i, x in enumerate(a) if x in ("--set", "--del")}
    port, cmd = int(a[0]), a[1]
    srcs = [x for i, x in enumerate(a[2:], 2) if not x.startswith("--") and i not in skip]
    if cmd not in TABLE:
        sys.exit(f"{cmd}: no reply class in result_table.json")
    p = Probe(port)
    p.poll()
    runs = []
    for n, s in enumerate(srcs):
        body = source(cmd, s)
        if edits and n == len(srcs) - 1:
            body = edit(body, edits)
            s += " (edited)"
        try:
            head, snap, R = bench(p, cmd, body)
        except Fault as e:
            print(json.dumps({"src": s[:60], "FAULT": str(e)}))
            print("the game is tainted now: restart it before trusting more results")
            return 1
        print(json.dumps({"src": s[:60], **head}))
        runs.append((snap, R))
    if "--words" in a:
        for w in words(runs[0][0]):
            if w[1]:
                print(" ", show(w))
    if len(runs) == 2:
        (s1, r1), (s2, r2) = runs
        diff = [(w1, w2) for w1, w2 in zip(words(s1), words(s2)) if w1[1] != w2[1]
                and not (abs(w1[1] - r1) < 0x10000 and abs(w2[1] - r2) < 0x10000)]   # self-pointers
        print(f"{len(diff)} words differ:")
        for w1, w2 in diff:
            print(f"  this+{w1[0]:#05x}: {w1[1]:#018x} -> {w2[1]:#018x}{text(w1[1], w2[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
