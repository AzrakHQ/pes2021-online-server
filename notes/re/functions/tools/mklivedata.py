#!/usr/bin/env python3
"""Build a test Live Update CPK: common/etc/version/version_wu.bin + edited pesdb tables, and print the
CHECK_CHEAT sums a game should report with it (pesdb.py model).

usage: mklivedata.py OUT.cpk [--player ID:PARAM:DELTA]... [--team-short TEAM:TEXT]... [--shirt TEAM:PLAYER:N]...
                             [--version 1.01]
  example: mklivedata.py www/livedata_102.cpk --player 7511:33:1 --team-short 108:BRX --shirt 108:7511:11 --version 1.02
Prints size and SHA-256 (lowercase) for the CMD_GET_DLC_FILELIST entry. Test-only; never publish game data.
"""
import argparse, hashlib, os, struct, sys, tempfile, zlib
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                     # pesdb.py, cpk.py and cpkwrite.py live next to this file
import pesdb  # noqa: E402
from cpkwrite import write_cpk  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--player", action="append", default=[])
ap.add_argument("--team-short", action="append", default=[])
ap.add_argument("--shirt", action="append", default=[])
ap.add_argument("--version", default="1.01")
a = ap.parse_args()


def wrap(data):
    z = zlib.compress(bytes(data), 9)
    return b"\xff\x10\x81WESYS" + struct.pack("<II", len(z), len(data)) + z


files = {"common/etc/version/version_wu.bin":
         b"".join(f.encode().ljust(10, b"\0") for f in (a.version, "1.01.00", "lu-test"))}

if a.player:
    raw, _ = pesdb.load("Player.bin"); d = bytearray(raw)
    for spec in a.player:
        pid, param, delta = map(int, spec.split(":"))
        o, s, w, add = pesdb.PMAP[param]
        for i in range(0, len(d), 312):
            if struct.unpack_from("<I", d, i + 8)[0] == pid:
                v = struct.unpack_from("<I", d, i + o)[0]; old = ((v >> s) & ((1 << w) - 1)) + add; new = old + delta
                assert 0 <= new - add < (1 << w)
                struct.pack_into("<I", d, i + o, (v & ~(((1 << w) - 1) << s)) | ((new - add) << s))
                print(f"player {pid} param {param}: {old} -> {new}"); break
        else:
            sys.exit(f"player {pid} not found")
    files["common/etc/pesdb/Player.bin"] = wrap(d)

if a.team_short:
    raw, _ = pesdb.load("Team.bin"); d = bytearray(raw)
    for spec in a.team_short:
        tid, txt = spec.split(":", 1); tid = int(tid)
        for i in range(0, len(d), 0x5fc):
            if struct.unpack_from("<I", d, i + 8)[0] == tid:
                old = pesdb.cstr(d[i + 0x372:i + 0x37c]); d[i + 0x372:i + 0x37c] = txt.encode().ljust(10, b"\0")
                print(f"team {tid} short: {old} -> {txt}"); break
        else:
            sys.exit(f"team {tid} not found")
    files["common/etc/pesdb/Team.bin"] = wrap(d)

if a.shirt:
    raw, _ = pesdb.load("PlayerAssignment.bin"); d = bytearray(raw)
    for spec in a.shirt:
        tid, pid, n = map(int, spec.split(":"))
        for o in range(0, len(d) - 15, 16):
            _, p, t, f = struct.unpack_from("<IIII", d, o)
            if p == pid and t == tid:
                nf = (f & ~0x3ff) | (n - 1); struct.pack_into("<I", d, o + 12, nf)
                print(f"assignment team {tid} player {pid}: flags {f:#x} -> {nf:#x} (shirt {(f & 0x3ff) + 1} -> {n})"); break
        else:
            sys.exit(f"assignment {tid}/{pid} not found")
    files["common/etc/pesdb/PlayerAssignment.bin"] = wrap(d)

with tempfile.TemporaryDirectory() as t:
    for rel, b in files.items():
        p = os.path.join(t, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(b)
    write_cpk(a.out, [(r, os.path.join(t, r)) for r in files])
b = open(a.out, "rb").read()
print(f"wrote {a.out}: size {len(b)} sha256 {hashlib.sha256(b).hexdigest()}")
db = pesdb.DB([os.path.abspath(a.out)] + pesdb.CPKS)
print("expected param_check_sum (live mode):", db.param_check_sum(live_mode=True))
print("expected entry_check_sum:", db.entry_check_sum())
