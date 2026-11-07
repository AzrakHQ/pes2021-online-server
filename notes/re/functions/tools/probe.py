#!/usr/bin/env python3
"""Client for the mod's debug probe (server/mod/probe.c; build with `PROBE=1 sh build.sh`).

Start a game copy with the probe on, e.g.  PES2021ONLINE_PROBE=27801 PES_INSTANCE=1 tools/pes.py relaunch
Then, from a script:
    from probe import Probe
    p = Probe(27801)
    p.trace(0x141cc3cd0, "matchday_info_parser", deref="1:64")    # log calls (args, caller, thread)
    ... drive the game ...
    for e in p.poll(): print(e)
    p.u64(0x1436fced8)            # read the game's globals pointer
    p.chain(0x1436fced8, 0x30)    # [[0x1436fced8]+0x30]
Command line:  probe.py PORT <command line as in probe.c>, e.g. `probe.py 27801 read 140000000 16`.
Addresses are the exe's own VAs (0x14xxxxxxx); prefix "@" for an absolute address.
"""
import socket, struct, sys


class ProbeError(RuntimeError):
    pass


class Probe:
    def __init__(self, port, host="127.0.0.1", timeout=10):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.f = self.s.makefile("rwb", buffering=0)
        self.sink = None          # optional callable(events): sees every polled event, whoever polls

    def cmd(self, line):
        """Send one command; return the lines before the final ok/err line and the ok text."""
        self.f.write((line + "\n").encode())
        out = []
        while True:
            ln = self.f.readline()
            if not ln:
                raise ProbeError("connection closed")
            ln = ln.decode("utf-8", "replace").rstrip("\n")
            if ln.startswith("ok"):
                return out, ln[3:]
            if ln.startswith("err"):
                raise ProbeError(f"{line}: {ln[4:]}")
            out.append(ln)

    @staticmethod
    def _a(va):
        return va if isinstance(va, str) else f"{va:x}"

    def base(self):
        return int(self.cmd("base")[1], 16)

    def read(self, va, n):
        return bytes.fromhex(self.cmd(f"read {self._a(va)} {n}")[1])

    def u32(self, va):
        return struct.unpack("<I", self.read(va, 4))[0]

    def u64(self, va):
        return struct.unpack("<Q", self.read(va, 8))[0]

    def cstr(self, va, n=128):
        return self.read(va, n).split(b"\0", 1)[0].decode("utf-8", "replace")

    def write(self, va, data):
        return self.cmd(f"write {self._a(va)} {data.hex()}")

    def chain(self, va, *offs):
        """[[va]+o1]+o2 ... as an absolute address (pass it on as f"@{addr:x}")."""
        return int(self.cmd(f"ptr {self._a(va)} " + " ".join(f"{o:x}" for o in offs))[1], 16)

    def call(self, fn, *args):
        return int(self.cmd(f"call {fn:x} " + " ".join(f"{a:x}" for a in args))[1], 16)

    def gcall(self, fn, *args):
        return self.cmd(f"gcall {fn:x} " + " ".join(f"{a:x}" for a in args))

    def trace(self, va, name="", max_hits=100, deref=""):
        return self.cmd(f"trace {va:x} {name or '-'} {max_hits} {deref}".rstrip())

    def untrace(self, va):
        return self.cmd(f"untrace {va:x}")

    def traces(self):
        return self.cmd("traces")[0]

    def poll(self):
        ev = self.cmd("poll")[0]
        if self.sink and ev:
            self.sink(ev)
        return ev

    def close(self):
        try:
            self.f.write(b"bye\n")
        finally:
            self.s.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    p = Probe(int(sys.argv[1]))
    lines, rest = p.cmd(" ".join(sys.argv[2:]))
    for ln in lines:
        print(ln)
    print("ok", rest)
