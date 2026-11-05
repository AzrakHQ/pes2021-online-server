#!/usr/bin/env python3
"""Decode PES 2021 error codes like 'QVHE1459' / 'MMGC_0002' -> source file name.
The 4 letters are a hash of the source file basename (0x141154020): chars summed into 4 buckets by
index % 4, each bucket % 26 + 'A'.  usage: [PES_EXE=path/PES2021.exe] errcode.py CODE [CODE...]"""
import os, re, sys
EXE = os.environ.get("PES_EXE") or next((p for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), *([".."] * k), "game", "files", "PES2021.exe") for k in (2, 3)) if os.path.exists(p)), "PES2021.exe")

def tag(name):
    b = [0, 0, 0, 0]
    for i, c in enumerate(name.encode()):
        b[i % 4] += c
    return "".join(chr(x % 26 + 65) for x in b)

data = open(EXE, "rb").read()
paths = set(m.group().decode("latin1") for m in re.finditer(rb"[A-Za-z]:\\[\x20-\x7e]{4,200}?\.(?:cpp|h|hpp|c)", data))
by = {}
for p in paths:
    by.setdefault(tag(re.split(r"[\\/]", p)[-1]), set()).add(p)
for code in sys.argv[1:]:
    t = code[:4].upper()
    hits = sorted(by.get(t, []))
    print(f"{code}: " + (" | ".join(hits[:4]) if hits else "no source path matched"))
