"""An option file (EDIT00000000) in the Live Update folder becomes a Live Update every friend's game downloads.

Why: online modes reload the player database from the game files and the Live Update and never apply edit data
(notes/re/functions/update.md, live 2026-11-22), so the squads and players of a community option file reach online
play only as a Live Update. The conversion applies the option file to the game's own tables (Player.bin,
Team.bin, PlayerAssignment.bin from download/dt80_100E_x64.cpk of a PES 2021 copy on this computer) and writes
them into a CPK next to it; liveupdate.py offers the newest CPK. Proven live: a converted file gave CHECK_CHEAT
sums equal to the model's.

The option file's layout (update.md; edit player fields proven on 60 players with the game's own converter):
- data = 80-byte header + counts at 0x50 (u16: players 0x60, teams 0x64, squads 0x70, formations 0x74) +
  30,000 player slots of 0x138 from 0x7c + teams (0x24c) from 0x8ed2fc + … + squads (0x11c: team id, 40 player ids,
  40 shirts) + … + formations (0x274, see FREC).
- edit player: u32 +0 = the player id it applies to (0: use +4), u32 +4 = player id, u16 +8 nationality, packed
  values as FIELDS, names at +0x36 (name) / +0x73 (shirt) / +0xb0 (shirt 2) / +0xed (Japanese), 61 B each.
"""
import calendar
import json
import logging
import os
import re
import struct
import tempfile
import threading
import time
import zlib

from . import cpkread, savefile

log = logging.getLogger("pesserver.pesoption")

PLAYERS_AT, PLAYER_SLOTS, PREC = 0x7c, 30000, 0x138
TEAMS_AT, TREC, SREC, FREC = 0x8ed2fc, 0x24c, 0x11c, 0x274
# formation record (FREC, one per team after the squads): +0 team id, 3 tactics of 0xa0 from +4 (each: 3 formation
# sets of 11 role codes + 11 x/y pairs = TacticsFormation.bin), +0x20c 10 squad indexes: set-piece takers and
# captain -> PlayerAssignment bits (index: bit). Both rebuild the game's own files byte for byte from a fresh file.
TAKER_BITS = {0: 19, 1: 17, 3: 18, 4: 16, 5: 20, 9: 21}
PDB = "common/etc/pesdb/"
SUMMARY = "pesserver/option.json"      # inside the CPK: what the conversion did (the game ignores the file)

# edit field -> (byte offset, size, shift, mask, Player.bin parameter index or None)  [the game's getters,
# field table 0x141402576; notes/re/functions/data/edit_player_fields.txt]
FIELDS = [(0, 0, 4, 0, 4294967295, None), (11, 4, 4, 0, 4294967295, None), (12, 8, 2, 0, 65535, None),
          (13, 32, 4, 7, 63, 45), (14, 32, 4, 13, 31, 0), (15, 44, 4, 29, 1, 42), (16, 10, 1, 0, 255, 46),
          (17, 11, 1, 0, 255, 47), (18, 14, 2, 0, 127, 14), (19, 12, 4, 23, 127, 18), (20, 40, 4, 0, 127, 17),
          (21, 16, 4, 0, 127, 19), (22, 16, 4, 7, 127, 21), (23, 16, 4, 14, 127, 22), (24, 16, 4, 21, 127, 20),
          (25, 36, 4, 14, 127, 23), (26, 20, 4, 0, 127, 26), (27, 20, 4, 7, 127, 27), (28, 20, 4, 14, 127, 33),
          (29, 20, 4, 21, 127, 37), (30, 44, 4, 13, 127, 36), (31, 24, 4, 0, 127, 38), (32, 24, 4, 7, 127, 34),
          (33, 24, 4, 14, 127, 35), (34, 24, 4, 21, 127, 39), (35, 36, 4, 0, 127, 15), (36, 28, 4, 0, 127, 24),
          (37, 28, 4, 7, 127, 25), (38, 28, 4, 14, 127, 16), (39, 28, 4, 21, 127, 28), (40, 36, 4, 7, 127, 29),
          (41, 44, 4, 6, 127, 30), (42, 32, 4, 0, 127, 31), (43, 12, 4, 30, 3, 44), (44, 36, 4, 30, 3, 32),
          (45, 28, 4, 28, 7, 40), (46, 40, 4, 7, 3, 41), (47, 32, 4, 28, 7, 48), (48, 40, 4, 9, 3, 49),
          (49, 44, 4, 30, 1, 43), (50, 40, 4, 11, 3, 50), (51, 40, 4, 13, 3, 13), (52, 40, 4, 15, 3, 10),
          (53, 40, 4, 17, 3, 11), (54, 40, 4, 19, 3, 12), (55, 40, 4, 21, 3, 6), (56, 40, 4, 23, 3, 7),
          (57, 40, 4, 25, 3, 8), (58, 40, 4, 27, 3, 9), (59, 40, 4, 29, 3, 5), (60, 44, 4, 20, 3, 3),
          (61, 44, 4, 0, 3, 4), (62, 44, 4, 2, 3, 2), (63, 44, 4, 4, 3, 1), (64, 32, 4, 18, 31, None),
          (65, 44, 4, 31, 1, 60), (66, 48, 4, 0, 1, 61), (67, 48, 4, 1, 1, 62), (68, 48, 4, 2, 1, 63),
          (69, 48, 4, 3, 1, 64), (70, 48, 4, 4, 1, 65), (71, 48, 4, 5, 1, 66), (72, 48, 4, 6, 1, 67),
          (73, 48, 4, 7, 1, 104), (74, 48, 4, 8, 1, 68), (75, 48, 4, 9, 1, 69), (76, 48, 4, 10, 1, 70),
          (77, 48, 4, 11, 1, 105), (78, 48, 4, 12, 1, 71), (79, 48, 4, 13, 1, 72), (80, 48, 4, 14, 1, 99),
          (81, 48, 4, 15, 1, 73), (82, 50, 2, 0, 1, 74), (83, 48, 4, 17, 1, 98), (84, 48, 4, 18, 1, 106),
          (85, 48, 4, 19, 1, 75), (86, 48, 4, 20, 1, 100), (87, 48, 4, 21, 1, 101), (88, 48, 4, 22, 1, 76),
          (89, 48, 4, 23, 1, 77), (90, 51, 1, 0, 1, 78), (91, 48, 4, 25, 1, 79), (92, 48, 4, 26, 1, 107),
          (93, 48, 4, 27, 1, 80), (94, 48, 4, 28, 1, 81), (95, 48, 4, 29, 1, 82), (96, 48, 4, 30, 1, 83),
          (97, 48, 4, 31, 1, 95), (98, 52, 2, 0, 1, 84), (99, 52, 2, 1, 1, 85), (100, 52, 2, 2, 1, 102),
          (101, 52, 2, 3, 1, 86), (102, 52, 2, 4, 1, 87), (103, 52, 2, 5, 1, 96), (104, 52, 2, 6, 1, 97),
          (105, 52, 2, 7, 1, 88), (106, 52, 2, 8, 1, 89), (107, 52, 2, 9, 1, 90), (108, 52, 2, 10, 1, 103),
          (109, 52, 2, 11, 1, 91), (110, 52, 2, 12, 1, 92), (111, 52, 2, 13, 1, 93), (112, 52, 2, 14, 1, 94),
          (113, 36, 4, 21, 7, 51), (114, 16, 4, 28, 15, 52), (115, 39, 1, 0, 7, 53), (116, 20, 4, 28, 15, 54),
          (117, 24, 4, 28, 15, 57), (118, 32, 4, 23, 31, 55), (119, 36, 4, 27, 7, 56), (120, 12, 1, 0, 255, 58)]
_F = {f[0]: f[1:] for f in FIELDS}
# Player.bin parameter -> (dword offset, shift, width, add)  [notes/re/functions/tools/pesdb.py PMAP]
PMAP = [(52, 18, 4, 0), (56, 22, 2, 0), (56, 30, 2, 0), (56, 24, 2, 0), (56, 28, 2, 0), (56, 16, 2, 0),
        (48, 30, 2, 0), (56, 8, 2, 0), (56, 18, 2, 0), (56, 12, 2, 0), (56, 20, 2, 0), (36, 30, 2, 0),
        (56, 26, 2, 0), (40, 30, 2, 0), (44, 18, 6, 40), (32, 19, 6, 40), (40, 6, 6, 40), (44, 0, 6, 40),
        (32, 25, 6, 40), (52, 0, 6, 40), (48, 12, 6, 40), (32, 7, 6, 40), (48, 18, 6, 40), (36, 0, 6, 40),
        (36, 24, 6, 40), (48, 0, 6, 40), (28, 26, 6, 40), (40, 12, 6, 40), (44, 12, 6, 40), (32, 13, 6, 40),
        (40, 0, 6, 40), (36, 12, 6, 40), (56, 14, 2, 0), (36, 18, 6, 40), (48, 6, 6, 40), (44, 24, 6, 40),
        (44, 6, 6, 40), (40, 24, 6, 40), (36, 6, 6, 40), (40, 18, 6, 40), (52, 22, 3, 0), (56, 10, 2, 0),
        (64, 2, 1, 0), (60, 29, 1, 0), (56, 6, 2, 0), (48, 24, 6, 15), (24, 24, 8, 100), (32, 0, 7, 30),
        (56, 3, 3, 0), (44, 30, 2, 0), (60, 0, 2, 0), (52, 25, 3, 0), (52, 10, 4, 0), (56, 0, 3, 0),
        (52, 14, 4, 0), (12, 27, 5, 0), (52, 28, 3, 0), (52, 6, 4, 0), (28, 18, 8, 0), (16, 27, 5, 0),
        (60, 9, 1, 0), (64, 19, 1, 0), (64, 14, 1, 0), (60, 30, 1, 0), (64, 17, 1, 0), (52, 31, 1, 0),
        (64, 8, 1, 0), (64, 7, 1, 0), (60, 5, 1, 0), (60, 17, 1, 0), (60, 2, 1, 0), (64, 16, 1, 0),
        (64, 13, 1, 0), (60, 15, 1, 0), (64, 15, 1, 0), (64, 1, 1, 0), (64, 12, 1, 0), (60, 25, 1, 0),
        (60, 31, 1, 0), (60, 27, 1, 0), (60, 4, 1, 0), (60, 3, 1, 0), (60, 13, 1, 0), (64, 3, 1, 0),
        (60, 8, 1, 0), (60, 10, 1, 0), (64, 9, 1, 0), (64, 10, 1, 0), (60, 11, 1, 0), (60, 24, 1, 0),
        (64, 5, 1, 0), (64, 18, 1, 0), (60, 12, 1, 0), (64, 4, 1, 0), (60, 6, 1, 0), (64, 0, 1, 0),
        (60, 21, 1, 0), (60, 22, 1, 0), (60, 26, 1, 0), (60, 20, 1, 0), (60, 14, 1, 0), (60, 19, 1, 0),
        (60, 16, 1, 0), (60, 23, 1, 0), (64, 11, 1, 0), (32, 31, 1, 0), (64, 6, 1, 0), (60, 7, 1, 0)]
STYLE_UI = (0, 1, 2, 3, 6, 9, 10, 11, 14, 12, 19, 15, 17, 4, 5, 18, 20, 21, 7, 8, 13, 16)   # table 0x142979080


def _field(r, f):
    off, size, sh, mask, _ = _F[f]
    return (int.from_bytes(r[off:off + size], "little") >> sh) & mask


def _put(rec, off, sh, width, value):
    cur = struct.unpack_from("<I", rec, off)[0]
    m = ((1 << width) - 1) << sh
    struct.pack_into("<I", rec, off, (cur & ~m) | ((value << sh) & m))


def _cstr(b):
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def read_edit(data):
    """{'players': [record], 'teams': {id: record}, 'squads': {team: [(player, shirt)]}} of an option file's data."""
    if len(data) < TEAMS_AT or struct.unpack_from("<I", data, 0)[0] != 0x0e:
        raise ValueError("not an option file")
    n_players, n_teams, n_squads = (struct.unpack_from("<H", data, o)[0] for o in (0x60, 0x64, 0x70))
    if n_players > PLAYER_SLOTS or n_teams * TREC + TEAMS_AT > len(data) or n_squads * SREC > len(data):
        raise ValueError("not an option file this server can read")
    players = [data[PLAYERS_AT + i * PREC:PLAYERS_AT + (i + 1) * PREC] for i in range(n_players)]
    order, teams = [], {}
    for i in range(n_teams):
        r = data[TEAMS_AT + i * TREC:TEAMS_AT + (i + 1) * TREC]
        tid = struct.unpack_from("<I", r, 0)[0]
        order.append(tid)
        teams[tid] = r
    squads, forms = {}, {}
    if n_squads and len(order) > 1:
        at = _table_at(data, order, SREC, TEAMS_AT + len(order) * TREC)
        for i in range(n_squads):
            r = data[at + i * SREC:at + (i + 1) * SREC]
            ids = struct.unpack_from("<40I", r, 4)
            shirts = struct.unpack_from("<40H", r, 0xa4)
            squads[struct.unpack_from("<I", r, 0)[0]] = [(p, s) for p, s in zip(ids, shirts) if p]
        n_forms = struct.unpack_from("<H", data, 0x74)[0]
        if n_forms and n_forms * FREC <= len(data):
            fat = _table_at(data, order, FREC, at + n_squads * SREC)
            for i in range(n_forms):
                r = data[fat + i * FREC:fat + (i + 1) * FREC]
                forms[struct.unpack_from("<I", r, 0)[0]] = r
    return {"players": players, "teams": teams, "squads": squads, "forms": forms}


def _table_at(data, order, size, start):
    """A per-team table after `start`: records in team order (fresh file: squads 0x9d4648, formations 0xa09880)."""
    want = [struct.pack("<I", t) for t in order[:3]]
    i = data.find(want[0], start)
    while i != -1:
        if all(data[i + k * size:i + k * size + 4] == w for k, w in enumerate(want)):
            return i
        i = data.find(want[0], i + 4)
    raise ValueError("a team table is missing in the option file")


def unwrap(d):
    """A pesdb table inside the game's CPK: 'WESYS' header + zlib (or stored)."""
    if len(d) >= 16 and d[:2] == b"\xff\x10" and d[3:8] == b"WESYS":
        cs, us = struct.unpack_from("<II", d, 8)
        out = zlib.decompress(d[16:16 + cs]) if d[2] & 0x80 else d[16:16 + us]
        if len(out) != us:
            raise ValueError("damaged table")
        return out
    return d


def wrap(data):
    z = zlib.compress(bytes(data), 9)
    return b"\xff\x10\x81WESYS" + struct.pack("<II", len(z), len(data)) + z


def game_tables(cpks):
    """{name: bytes} of Player.bin, Team.bin and PlayerAssignment.bin: the first CPK that has each wins."""
    if not cpks:
        raise ValueError("PES 2021 isn't on this computer (its data is needed to build the update)")
    out = {}
    for c in cpks:
        if not os.path.isfile(c):
            continue
        _, rows = cpkread.toc(c)
        by = {cpkread.name(r): r for r in rows}
        for n in ("Player.bin", "Team.bin", "PlayerAssignment.bin", "Tactics.bin", "TacticsFormation.bin"):
            r = by.get(PDB + n)
            if n not in out and r is not None and r.get("FileSize") == r.get("ExtractSize"):
                with open(c, "rb") as f:
                    f.seek(r["_abs"])
                    out[n] = unwrap(f.read(r["FileSize"]))
    missing = {"Player.bin", "Team.bin", "PlayerAssignment.bin"} - set(out)
    if missing:
        raise ValueError("the game's data is missing " + ", ".join(sorted(missing)))
    return out


def game_cpks():
    """The data of a PES 2021 copy on this computer: its season update first, then the base data."""
    from . import gamesetup
    for g in gamesetup._games():
        dl = os.path.join(g["dir"], "download", "dt80_100E_x64.cpk")
        if os.path.isfile(dl):
            return [dl, os.path.join(g["dir"], "Data", "dt10_x64.cpk")]
    return []


def apply(tables, ed):
    """The game's tables with the option file applied: (Player.bin, Team.bin, PlayerAssignment.bin, summary)."""
    praw = bytearray(tables["Player.bin"])
    index = {struct.unpack_from("<I", praw, o + 8)[0] & 0x3ffff: o for o in range(0, len(praw) - 311, 312)}
    added = 0
    for r in ed["players"]:
        full = _field(r, 0) or _field(r, 11)
        pid = full & 0x3ffff                       # (bit 18 is a flag, as in Player.bin)
        if not pid:
            continue
        if pid not in index:                       # a player the option file made: a new record
            index[pid] = len(praw)
            praw += bytes(312)
            struct.pack_into("<I", praw, index[pid] + 8, full)
            added += 1
        o = index[pid]
        rec = bytearray(praw[o:o + 312])
        for f, (_, _, _, _, p) in _F.items():
            if p is not None:
                off, sh, width, add = PMAP[p]
                _put(rec, off, sh, width, (_field(r, f) - add) & ((1 << width) - 1))
        style = _field(r, 64)
        _put(rec, 16, 27, 5, STYLE_UI.index(style) if style in STYLE_UI else 0)
        _put(rec, 0x1c, 9, 9, _field(r, 12))       # nationality
        for src, dst in ((0x36, 0xfb), (0x73, 0x81), (0xb0, 0xbe), (0xed, 0x44)):
            rec[dst:dst + 61] = r[src:src + 61]
        praw[o:o + 312] = rec
    traw = bytearray(tables["Team.bin"])
    tindex = {struct.unpack_from("<I", traw, o + 8)[0]: o for o in range(0, len(traw) - 0x5fb, 0x5fc)}
    renamed = 0
    for tid, r in ed["teams"].items():
        o = tindex.get(tid)
        name = _cstr(r[0x68:0xae])
        if o is None or name in ("", "-"):         # ("-": no name of its own in the file)
            continue
        if _cstr(traw[o + 0x170:o + 0x170 + 70]) != name:
            renamed += 1
        traw[o + 0x170:o + 0x170 + 70] = r[0x68:0x68 + 70]       # the English name, the short name
        traw[o + 0x372:o + 0x372 + 10] = r[0xae:0xae + 10]
        struct.pack_into("<I", traw, o, struct.unpack_from("<I", r, 4)[0])      # the coach
    pa = tables["PlayerAssignment.bin"]
    old, base = {}, {}
    for o in range(0, len(pa) - 15, 16):
        _, pid, tid, f = struct.unpack_from("<IIII", pa, o)
        old[(tid, pid)] = f
        base.setdefault(tid, set()).add(pid)
    squads, forms = ed["squads"] or {}, ed.get("forms") or {}
    rows, changed = [], 0
    for tid in [t for t in base if t not in squads] + list(squads):    # teams the file has no squad for: as they were
        if tid in squads:
            lst = squads[tid]
            changed += {p for p, _ in lst} != base.get(tid, set())
            takers = forms[tid][0x20c:0x216] if tid in forms else None
            for slot, (pid, shirt) in enumerate(lst):
                if not pid:                                                # (ids above 0x3ffff are real: a flag bit)
                    continue
                if takers is not None:                                     # set-piece takers, captain
                    roles = sum(1 << b for i, b in TAKER_BITS.items() if takers[i] == slot)
                else:
                    roles = old.get((tid, pid), 0) & ~0xffff
                rows.append((pid, tid, roles | ((slot & 0x3f) << 10) | ((shirt - 1) & 0x3ff)))
        else:
            rows += [(pid, tid, f) for (t, pid), f in old.items() if t == tid]
    pa_out = b"".join(struct.pack("<IIII", i + 1, p, t, f) for i, (p, t, f) in enumerate(rows))
    out = {"Player.bin": bytes(praw), "Team.bin": bytes(traw), "PlayerAssignment.bin": pa_out}
    tf = _formations(tables, forms)
    if tf is not None:
        out["TacticsFormation.bin"] = tf
    return out, {"players": len(ed["players"]), "new_players": added, "teams_renamed": renamed,
                 "squads_changed": changed}


def _formations(tables, forms):
    """TacticsFormation.bin with the option file's formations: a team's tactics, in Tactics.bin order, take the
    option file's tactics 1..3 (3 sets x 11 rows {tactic, role, set<<20 | slot<<16 | y<<8 | x})."""
    t, tf = tables.get("Tactics.bin"), tables.get("TacticsFormation.bin")
    if not forms or t is None or tf is None:
        return None
    team_tactics, new = {}, {}
    for q in range(0, len(t) - 11, 12):
        tac, tid, _ = struct.unpack_from("<III", t, q)
        team_tactics.setdefault(tid, []).append(tac)
    for tid, tacs in team_tactics.items():
        r = forms.get(tid)
        if r is None:
            continue
        for j, tac in enumerate(tacs[:3]):
            b = r[4 + j * 0xa0:4 + (j + 1) * 0xa0]
            new[tac] = [(b[s * 33 + k], (s << 20) | (k << 16) | (b[s * 33 + 12 + 2 * k] << 8) | b[s * 33 + 11 + 2 * k])
                        for s in range(3) for k in range(11)]
    out, done = bytearray(), set()
    for q in range(0, len(tf) - 11, 12):
        tac = struct.unpack_from("<I", tf, q)[0]
        if tac not in new:
            out += tf[q:q + 12]
        elif tac not in done:                     # (a tactic's 33 rows, where its first row was)
            done.add(tac)
            out += b"".join(struct.pack("<III", tac, role, v) for role, v in new[tac])
    return bytes(out)


def version_for(t, previous=0):
    """The update's version as 'x.yy': hours since 2025 at build time t, always above `previous` (as an integer,
    e.g. 16562) and above Konami's 1.xx. Games download only a higher version than the one they have, so every
    build must go up."""
    h = max(200, int((t - calendar.timegm((2025, 1, 1, 0, 0, 0))) // 3600), previous + 1)
    return f"{h // 100}.{h % 100:02d}"


def build(option_path, out_path, cpks=None, previous=0):
    """Convert an option file into a Live Update CPK at out_path. Returns the summary (+ version, source)."""
    cpks = cpks if cpks is not None else game_cpks()
    if not cpks:                                  # (before the slow part: decrypting takes seconds)
        raise ValueError("PES 2021 isn't on this computer (its data is needed to build the update)")
    st = os.stat(option_path)
    data = savefile.data_of(open(option_path, "rb").read())
    ed = read_edit(data)
    tables = game_tables(cpks)
    out, summary = apply(tables, ed)
    ver = version_for(time.time(), previous)
    summary = {**summary, "version": ver, "source": [st.st_mtime, st.st_size]}
    files = {SUMMARY: json.dumps(summary).encode(), "common/etc/version/version_wu.bin":
             b"".join(f.encode().ljust(10, b"\0") for f in (ver, "1.01.00", "option"))}
    files.update({PDB + n: wrap(blob) for n, blob in out.items()})
    from .cpkwrite import write_cpk
    with tempfile.TemporaryDirectory() as tmp:
        lst = []
        for name, blob in files.items():
            p = os.path.join(tmp, name.replace("/", "_"))
            with open(p, "wb") as f:
                f.write(blob)
            lst.append((name, p))
        part = out_path + ".part"
        write_cpk(part, lst)
        os.replace(part, out_path)
    return summary


# ---- the Live Update folder --------------------------------------------------------------------------------
STATE = {}                 # folder -> {option file name: {"file", "cpk", "error" | "summary", "t"}} (admin page)
_FAILED = {}               # (folder, name) -> ((mtime, size), when): a file that failed is tried again on change
RETRY = 600                # s before a failed file is tried again anyway (e.g. PES installed meanwhile)
HIGH = ".option-version"   # in the folder: the highest version built so far (versions must only go up)
_LOCK = threading.Lock()
_NAME = re.compile(r"EDIT\d{8}")


def is_option_file(name):
    return bool(_NAME.fullmatch(name))


def out_name(option_name):
    return f"option-{option_name}.cpk"


def _summary_of(cpk):
    """The summary a CPK of ours carries, or None (not ours, or damaged)."""
    try:
        raw = cpkread.read_file(cpk, SUMMARY)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - a damaged or foreign file
        return None


def _high(folder):
    try:
        with open(os.path.join(folder, HIGH)) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def refresh(folder):
    """Convert each option file in the folder whose CPK is missing or was made from another file; drop the CPKs
    (and the page's notes) of option files that are gone. Called by liveupdate's thread."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    states = STATE.setdefault(folder, {})
    for n in names:
        src = os.path.join(folder, n)
        if not is_option_file(n) or not os.path.isfile(src):
            continue
        dst = os.path.join(folder, out_name(n))
        try:
            st = os.stat(src)
        except OSError:
            continue
        key = [st.st_mtime, st.st_size]
        made = _summary_of(dst) if os.path.isfile(dst) else None
        if made is not None and made.get("source") == key:
            if n not in states:                    # (made before this run: its summary is inside)
                states[n] = {"file": n, "cpk": out_name(n), "summary": made, "t": os.path.getmtime(dst)}
            continue
        failed = _FAILED.get((folder, n))
        if failed and failed[0] == tuple(key) and time.time() - failed[1] < RETRY:
            continue
        with _LOCK:
            t = time.time()
            try:
                summary = build(src, dst, previous=_high(folder))
                v = int(summary["version"].replace(".", ""))
                with open(os.path.join(folder, HIGH), "w") as f:
                    f.write(str(v))
                states[n] = {"file": n, "cpk": out_name(n), "summary": summary, "t": t}
                _FAILED.pop((folder, n), None)
                log.info("option file %s -> %s: %s", n, os.path.basename(dst), summary)
            except Exception as e:  # noqa: BLE001 - a broken or foreign file: say so on the admin page
                states[n] = {"file": n, "cpk": out_name(n), "error": str(e) or e.__class__.__name__, "t": t}
                _FAILED[(folder, n)] = (tuple(key), t)
                log.warning("option file %s not converted: %s", n, e)
    for n in list(states):                         # an option file that went away: its note goes too
        if n not in names:
            states.pop(n, None)
    for n in names:                                # ... and its CPK (only ours: named for one, with our summary)
        if n.startswith("option-") and n.endswith(".cpk") and is_option_file(n[7:-4]) and n[7:-4] not in names:
            path = os.path.join(folder, n)
            if _summary_of(path) is not None:
                try:
                    os.remove(path)
                    log.info("option file %s removed: %s removed", n[7:-4], n)
                except OSError:
                    pass
