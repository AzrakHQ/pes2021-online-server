#!/usr/bin/env python3
"""PES 2021 option file (EDIT00000000) → readable data, and → a Live Update CPK for online play.

  pesedit.py info EDIT                         counts, edited players, renamed teams, squad changes vs the game data
  pesedit.py livedata EDIT OUT.cpk [--version 1.10] [--label TEXT]
        Player.bin / Team.bin / PlayerAssignment.bin = the game's data (dt80) with the option file's players,
        team names and squads applied, + common/etc/version/version_wu.bin; prints the CHECK_CHEAT sums a game
        will report with it (pesdb.py model, live mode).

Why: online modes reload the player database from the game files + the Live Update and never apply edit data
(update.md), so an option file's squads reach online play only as a Live Update.

Layout (update.md; field table data/edit_player_fields.txt, mapping proven on 60 players by running the game's own
DB→edit converter 0x141f10770 through the probe):
- data = 80-byte header + count header at 0x50 (u16: players 0x60, teams 0x64, coaches 0x66, squads 0x70) +
  players (30,000 × 0x138 from 0x7c) + teams (0x24c from 0x8ed2fc) + … + squads (0x11c).
- edit player: field 0 u32 +0 = the DB id it applies to (0 = use field 11), field 11 u32 +4 = player id,
  u16 +8 nationality, names at +0x36 / +0x73 / +0xb0 / +0xed (61 B each), packed params as EDIT_FIELDS below.
"""
import argparse, os, struct, sys, tempfile, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pesdb  # noqa: E402
import pessave  # noqa: E402

PLAYERS_AT, PLAYER_SLOTS, PREC = 0x7c, 30000, 0x138
TEAMS_AT, TREC = 0x8ed2fc, 0x24c
SREC = 0x11c
STYLE_UI = (0, 1, 2, 3, 6, 9, 10, 11, 14, 12, 19, 15, 17, 4, 5, 18, 20, 21, 7, 8, 13, 16)   # table 0x142979080
SKILL_UI_ORDER = pesdb.SKILL_UI_ORDER

# edit field -> (byte offset, size, shift, mask) [the game's getters, data/edit_player_fields.txt]
def _fields():
    import re
    out = {}
    for line in open(os.path.join(HERE, "..", "data", "edit_player_fields.txt")):
        fid = int(line.split()[0]); body = line.split("|", 1)[1]
        m = re.search(r"(dword|word|byte) ptr \[rcx(?: \+ (0x[0-9a-f]+|\d+))?\]", body)
        size = {"dword": 4, "word": 2, "byte": 1}[m.group(1)]
        off = int(m.group(2), 0) if m.group(2) else 0
        sh = re.search(r"shr eax, (0x[0-9a-f]+|\d+)", body); sh = int(sh.group(1), 0) if sh else 0
        an = re.search(r"and eax, (0x[0-9a-f]+|\d+)", body)
        mask = int(an.group(1), 0) if an else (1 << (size * 8 - sh)) - 1
        out[fid] = (off, size, sh, mask)
    return out


FIELDS = _fields()

# edit field -> pesdb parameter index (identity values; 64 = playing style in UI order)
EDIT_TO_PARAM = {13: 45, 14: 0, 15: 42, 16: 46, 17: 47, 18: 14, 19: 18, 20: 17, 21: 19, 22: 21, 23: 22, 24: 20,
                 25: 23, 26: 26, 27: 27, 28: 33, 29: 37, 30: 36, 31: 38, 32: 34, 33: 35, 34: 39, 35: 15, 36: 24,
                 37: 25, 38: 16, 39: 28, 40: 29, 41: 30, 42: 31, 43: 44, 44: 32, 45: 40, 46: 41, 47: 48, 48: 49,
                 49: 43, 50: 50, 113: 51, 114: 52, 115: 53, 116: 54, 117: 57, 118: 55, 119: 56, 120: 58}
for _f, _p in zip(range(51, 64), (13, 10, 11, 12, 6, 7, 8, 9, 5, 3, 4, 2, 1)):
    EDIT_TO_PARAM[_f] = _p
for _f in range(65, 72):
    EDIT_TO_PARAM[_f] = 60 + (_f - 65)
for _f, _p in zip(range(72, 113), SKILL_UI_ORDER):
    EDIT_TO_PARAM[_f] = _p


def field(r, f):
    off, size, sh, mask = FIELDS[f]
    return (int.from_bytes(r[off:off + size], "little") >> sh) & mask


def cstr(b):
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def read(path):
    """{'header', 'counts', 'players': [rec bytes], 'teams': {id: rec}, 'squads': {team: [(player, shirt)]}}."""
    raw = open(path, "rb").read()
    d = pessave.open_save(raw)["sections"][2] if raw[:4] != b"\x0e\0\0\0" else raw
    n_players, n_teams = struct.unpack_from("<H", d, 0x60)[0], struct.unpack_from("<H", d, 0x64)[0]
    n_squads = struct.unpack_from("<H", d, 0x70)[0]
    players = [d[PLAYERS_AT + i * PREC:PLAYERS_AT + (i + 1) * PREC] for i in range(n_players)]
    teams = {}
    for i in range(n_teams):
        r = d[TEAMS_AT + i * TREC:TEAMS_AT + (i + 1) * TREC]
        teams[struct.unpack_from("<I", r, 0)[0]] = r
    # the squad table follows the team-sized tables; find it by its first record = the first team's id, then
    # the 40 ids / shirts layout (base found in a fresh file: ARG index 48 at 0x9d7b88)
    order = [struct.unpack_from("<I", d, TEAMS_AT + i * TREC)[0] for i in range(n_teams)]
    sq_at = _find_squads(d, order)
    squads = {}
    for i in range(n_squads):
        r = d[sq_at + i * SREC:sq_at + (i + 1) * SREC]
        tid = struct.unpack_from("<I", r, 0)[0]
        ids = struct.unpack_from("<40I", r, 4); shirts = struct.unpack_from("<40H", r, 0xa4)
        squads[tid] = [(p, s) for p, s in zip(ids, shirts) if p]
    return {"data": d, "players": players, "teams": teams, "squads": squads, "squads_at": sq_at}


def _find_squads(d, order):
    first = struct.pack("<I", order[0]); second = struct.pack("<I", order[1])
    i = d.find(first, TEAMS_AT + len(order) * TREC)
    while i != -1:
        if d[i + SREC:i + SREC + 4] == second and (i - PLAYERS_AT) % 4 == 0:
            return i
        i = d.find(first, i + 4)
    raise ValueError("squad table not found")


def player_id(r):
    return field(r, 0) or field(r, 11)


def apply(db, ed):
    """Apply the option file to a pesdb.DB in place (players' params, names, nationality; team names; squads).
    Returns the edited Player.bin, Team.bin and PlayerAssignment.bin bytes."""
    praw = bytearray(db.player_raw)
    index = {struct.unpack_from("<I", praw, o + 8)[0] & 0x3ffff: o for o in range(0, len(praw), 312)}
    for r in ed["players"]:
        pid = player_id(r)
        if pid not in index:                      # a new player: start from a blank record
            praw += bytes(312); index[pid] = len(praw) - 312
            struct.pack_into("<I", praw, index[pid] + 8, pid)
        o = index[pid]
        rec = bytearray(praw[o:o + 312])
        for f, p in EDIT_TO_PARAM.items():
            v = field(r, f)
            off, sh, w, add = pesdb.PMAP[p]
            if p == 59 or f == 64:
                continue
            raw = (v - add) & ((1 << w) - 1)
            cur = struct.unpack_from("<I", rec, off)[0]
            struct.pack_into("<I", rec, off, (cur & ~(((1 << w) - 1) << sh)) | (raw << sh))
        style = STYLE_UI.index(field(r, 64)) if field(r, 64) in STYLE_UI else 0
        cur = struct.unpack_from("<I", rec, 0x10)[0]
        struct.pack_into("<I", rec, 0x10, (cur & 0x07ffffff) | (style << 27))
        nat = field(r, 12)
        cur = struct.unpack_from("<I", rec, 0x1c)[0]
        struct.pack_into("<I", rec, 0x1c, (cur & ~(0x1ff << 9)) | ((nat & 0x1ff) << 9))
        for src, dst in ((0x36, 0xfb), (0x73, 0x81), (0xb0, 0xbe), (0xed, 0x44)):
            rec[dst:dst + 61] = r[src:src + 61]
        praw[o:o + 312] = rec
    traw = bytearray(db.team_raw)
    tindex = {struct.unpack_from("<I", traw, o + 8)[0]: o for o in range(0, len(traw), 0x5fc)}
    for tid, r in ed["teams"].items():
        o = tindex.get(tid)
        if o is None:
            continue
        if cstr(r[0x68:0xae]) not in ("", "-"):              # ("-": no name in this file)
            traw[o + 0x170:o + 0x170 + 70] = r[0x68:0x68 + 70]
        traw[o + 0x372:o + 0x372 + 10] = r[0xae:0xae + 10]
        struct.pack_into("<I", traw, o, struct.unpack_from("<I", r, 4)[0])      # coach
    # squads -> PlayerAssignment: list position = order slot; keep set-piece / captain bits of unchanged pairs
    old = {}
    for o in range(0, len(db.pa_raw) - 15, 16):
        _, pid, tid, f = struct.unpack_from("<IIII", db.pa_raw, o)
        old[(tid, pid)] = f
    rows = []
    for tid, lst in ed["squads"].items():
        for slot, (pid, shirt) in enumerate(lst):
            keep = old.get((tid, pid), 0) & ~0xffff          # set-piece roles, captain
            rows.append((pid, tid, keep | ((slot & 0x3f) << 10) | ((shirt - 1) & 0x3ff)))
    pa = b"".join(struct.pack("<IIII", i + 1, p, t, f) for i, (p, t, f) in enumerate(rows))
    return bytes(praw), bytes(traw), pa


def wrap(data):
    z = zlib.compress(bytes(data), 9)
    return b"\xff\x10\x81WESYS" + struct.pack("<II", len(z), len(data)) + z


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("info", "livedata"))
    ap.add_argument("edit")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--version", default="1.10")
    ap.add_argument("--label", default="option file")
    a = ap.parse_args()
    ed = read(a.edit)
    db = pesdb.DB()
    if a.cmd == "info":
        print(f"edited players {len(ed['players'])}, teams {len(ed['teams'])}, squads {len(ed['squads'])} "
              f"(squad table at {ed['squads_at']:#x})")
        for r in ed["players"][:40]:
            print(f"  player {player_id(r)} {cstr(r[0x36:0x36 + 61])!r}")
        names = {t["id"]: t for t in db.teams}
        ren = [(tid, cstr(r[0x68:0xae])) for tid, r in ed["teams"].items() if tid in names and cstr(r[0x68:0xae]) != names[tid]["name"]]
        print(f"renamed teams: {len(ren)}", ren[:10])
        base = {}
        for x in db.assign:
            base.setdefault(x["team"], []).append(x["player"])
        moved = sum(1 for tid, lst in ed["squads"].items() if sorted(p for p, _ in lst) != sorted(base.get(tid, [])))
        print(f"squads that differ from the game data: {moved}")
        return
    praw, traw, pa = apply(db, ed)
    files = {"common/etc/version/version_wu.bin":
             b"".join(f.encode().ljust(10, b"\0") for f in (a.version, "1.01.00", a.label[:9])),
             "common/etc/pesdb/Player.bin": wrap(praw), "common/etc/pesdb/Team.bin": wrap(traw),
             "common/etc/pesdb/PlayerAssignment.bin": wrap(pa)}
    from cpkwrite import write_cpk
    with tempfile.TemporaryDirectory() as tmp:
        lst = []
        for name, data in files.items():
            p = os.path.join(tmp, name.replace("/", "_"))
            open(p, "wb").write(data); lst.append((name, p))
        write_cpk(a.out, lst)
    # expected sums from the converted tables
    db.player_raw, db.team_raw, db.pa_raw = praw, traw, pa
    db.players = [pesdb.decode_player(praw[o:o + 312]) for o in range(0, len(praw), 312)]
    db.assign = pesdb.decode_assignments(pa)
    db.teams = [pesdb.decode_team(traw[o:o + 0x5fc]) for o in range(0, len(traw), 0x5fc)]
    tby = {t["id"]: t for t in db.teams}
    for t in db.teams:
        t["roster"] = []
    for x in db.assign:
        if x["team"] in tby:
            tby[x["team"]]["roster"].append(x)
    for t in db.teams:
        t["roster"].sort(key=lambda x: x["order"])
    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes)")
    print("expected param_check_sum (live mode):", db.param_check_sum(live_mode=True))
    print("expected entry_check_sum:", db.entry_check_sum())


if __name__ == "__main__":
    main()
