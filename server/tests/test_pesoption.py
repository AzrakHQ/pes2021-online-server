"""Option file → Live Update (pesoption.py), on made-up tables (no game data in the repo).

The real thing was proven live (notes/re/functions/update.md): a converted option file gave the CHECK_CHEAT sums
the model predicted. Here: the layout readers, the edit → Player.bin packing, squads → PlayerAssignment, the folder
handling and the save container."""
import os
import struct

import pytest

from pesserver import cpkread, liveupdate, pesoption, savefile
from pesserver.cpkwrite import write_cpk


def _player(pid, speed=70, name=b"OLD"):
    r = bytearray(312)
    struct.pack_into("<I", r, 8, pid)
    off, sh, w, add = pesoption.PMAP[33]                     # speed
    pesoption._put(r, off, sh, w, speed - add)
    r[0xfb:0xfb + len(name)] = name
    return bytes(r)


def _team(tid, name):
    r = bytearray(0x5fc)
    struct.pack_into("<I", r, 8, tid)
    r[0x170:0x170 + len(name)] = name
    return bytes(r)


def _tables():
    pa = b"".join(struct.pack("<IIII", i + 1, p, t, f) for i, (p, t, f) in enumerate(
        [(100, 1, 9 | (1 << 21)), (200, 1, 1), (300, 2, 4)]))         # 100 = captain of team 1, shirt 10
    tactics = b"".join(struct.pack("<III", tac, tid, 0) for tac, tid in ((11, 1), (12, 1), (21, 2)))
    tf = b"".join(struct.pack("<III", tac, 0, (s << 20) | (k << 16)) for tac in (11, 12, 21) for s in range(3)
                  for k in range(11))
    return {"Player.bin": _player(100) + _player(200) + _player(300), "Team.bin": _team(1, b"ONE") + _team(2, b"TWO"),
            "PlayerAssignment.bin": pa, "Tactics.bin": tactics, "TacticsFormation.bin": tf}


def _edit_record(pid, speed, name):
    r = bytearray(pesoption.PREC)
    struct.pack_into("<I", r, 0, pid)
    struct.pack_into("<I", r, 4, pid)
    for f, (off, size, sh, mask, p) in pesoption._F.items():
        if f == 28:                                           # speed, as shown
            v = int.from_bytes(r[off:off + size], "little")
            v = (v & ~(mask << sh)) | (speed << sh)
            r[off:off + size] = v.to_bytes(size, "little")
    off, size, sh, mask, _ = pesoption._F[16]                 # height 180 (byte)
    r[off] = 180
    r[0x36:0x36 + len(name)] = name
    return bytes(r)


def _edit_data():
    d = bytearray(pesoption.TEAMS_AT + 2 * pesoption.TREC + 0x400 + 2 * pesoption.SREC + 0x100 + 2 * pesoption.FREC)
    struct.pack_into("<IIII", d, 0, 0x0e, 0x50, len(d) - 0x50, 10100)
    struct.pack_into("<H", d, 0x60, 2)                         # edited players
    struct.pack_into("<H", d, 0x64, 2)                         # teams
    struct.pack_into("<H", d, 0x70, 2)                         # squads
    struct.pack_into("<H", d, 0x74, 2)                         # formations
    d[0x7c:0x7c + pesoption.PREC] = _edit_record(200, 90, b"NEW NAME")
    d[0x7c + pesoption.PREC:0x7c + 2 * pesoption.PREC] = _edit_record(400, 60, b"NEWCOMER")
    for i, (tid, name) in enumerate(((1, b"ONE"), (2, b"TWO FC"))):
        o = pesoption.TEAMS_AT + i * pesoption.TREC
        struct.pack_into("<I", d, o, tid)
        d[o + 0x68:o + 0x68 + len(name)] = name
    sq = pesoption.TEAMS_AT + 2 * pesoption.TREC + 0x400
    for i, (tid, lst) in enumerate(((1, [(100, 10), (300, 7)]), (2, [(200, 9), (400, 22)]))):
        o = sq + i * pesoption.SREC
        struct.pack_into("<I", d, o, tid)
        for k, (p, s) in enumerate(lst):
            struct.pack_into("<I", d, o + 4 + 4 * k, p)
            struct.pack_into("<H", d, o + 0xa4 + 2 * k, s)
    fo = sq + 2 * pesoption.SREC + 0x100
    for i, tid in enumerate((1, 2)):
        o = fo + i * pesoption.FREC
        struct.pack_into("<I", d, o, tid)
        for k in range(11):                                    # tactic 1, set 0: role 5, x = 10 + k, y = 50
            d[o + 4 + k] = 5
            d[o + 4 + 11 + 2 * k], d[o + 4 + 12 + 2 * k] = 10 + k, 50
        d[o + 0x20c:o + 0x216] = bytes([1, 1, 0, 1, 1, 1, 255, 255, 255, 1])   # list index 1 takes everything
    return bytes(d)


def _speed(rec):
    off, sh, w, add = pesoption.PMAP[33]
    return ((struct.unpack_from("<I", rec, off)[0] >> sh) & ((1 << w) - 1)) + add


def test_apply_players_teams_squads():
    ed = pesoption.read_edit(_edit_data())
    assert [pesoption._field(r, 11) for r in ed["players"]] == [200, 400]
    assert ed["squads"] == {1: [(100, 10), (300, 7)], 2: [(200, 9), (400, 22)]}
    out, summary = pesoption.apply(_tables(), ed)
    praw, traw, pa = out["Player.bin"], out["Team.bin"], out["PlayerAssignment.bin"]
    recs = {struct.unpack_from("<I", praw, o + 8)[0]: praw[o:o + 312] for o in range(0, len(praw), 312)}
    assert _speed(recs[200]) == 90 and recs[200][0xfb:0xfb + 8] == b"NEW NAME"
    assert _speed(recs[100]) == 70                               # not in the option file: as it was
    assert 400 in recs and _speed(recs[400]) == 60 and recs[400][0x1b] == 80    # height 180 = 100 + 80
    assert traw[0x5fc + 0x170:0x5fc + 0x176] == b"TWO FC"
    rows = [struct.unpack_from("<IIII", pa, o) for o in range(0, len(pa), 16)]
    got = {(t, p): (f & 0x3ff) + 1 for _, p, t, f in rows}
    assert got == {(1, 100): 10, (1, 300): 7, (2, 200): 9, (2, 400): 22}
    flags = {(t, p): f for _, p, t, f in rows}
    assert flags[(1, 300)] >> 16 & 0x3f == 0x3f and not flags[(1, 100)] >> 16 & 0x3f    # takers + captain: index 1
    tf = out["TacticsFormation.bin"]
    trows = [struct.unpack_from("<III", tf, q) for q in range(0, len(tf), 12)]
    assert len(trows) == 99 and trows[0] == (11, 5, (50 << 8) | 10) and trows[10] == (11, 5, (10 << 16) | (50 << 8) | 20)
    assert trows[33][0] == 12 and trows[66][0] == 21 and trows[66][1] == 5
    assert summary == {"players": 2, "new_players": 1, "teams_renamed": 1, "squads_changed": 2}


def test_not_an_option_file():
    with pytest.raises(ValueError):
        pesoption.read_edit(b"\0" * 100)
    with pytest.raises(ValueError):
        savefile.data_of(b"\0" * 4096)
    assert pesoption.version_for(0) == "2.00" and pesoption.version_for(1.8e9) > pesoption.version_for(1.79e9)
    assert pesoption.version_for(1.8e9, previous=99999) == "1000.00"     # never below the last one built


@pytest.fixture(scope="module")
def option_file(tmp_path_factory):
    d = tmp_path_factory.mktemp("opt")
    raw = savefile.seal(_edit_data())
    assert savefile.data_of(raw) == _edit_data()
    game = d / "dt80.cpk"
    srcs = []
    for n, blob in _tables().items():
        p = d / n
        p.write_bytes(pesoption.wrap(blob))
        srcs.append((pesoption.PDB + n, str(p)))
    write_cpk(str(game), srcs)
    return raw, str(game)


def test_folder_converts_offers_and_cleans_up(tmp_path, option_file, monkeypatch):
    raw, game = option_file
    monkeypatch.setattr(pesoption, "game_cpks", lambda: [game])
    folder = str(tmp_path)
    src = tmp_path / "EDIT00000000"
    src.write_bytes(raw)
    pesoption.refresh(folder)
    out = tmp_path / "option-EDIT00000000.cpk"
    st = pesoption.STATE[folder]["EDIT00000000"]
    assert out.is_file() and st["summary"]["squads_changed"] == 2 and st["cpk"] == "option-EDIT00000000.cpk"
    v1 = int(liveupdate._version(str(out)))
    assert v1 > 199                                              # higher than Konami's 1.xx
    pa = pesoption.unwrap(cpkread.read_file(str(out), pesoption.PDB + "PlayerAssignment.bin"))
    assert len(pa) == 4 * 16
    mtime = out.stat().st_mtime
    pesoption.STATE.clear()                                      # a restart: the summary comes from the CPK
    pesoption.refresh(folder)                                    # up to date: not built again
    assert out.stat().st_mtime == mtime
    assert pesoption.STATE[folder]["EDIT00000000"]["summary"]["players"] == 2
    # replaced by a file with an OLDER timestamp (an unzipped download): rebuilt, and the version still goes up
    src.write_bytes(raw)
    os.utime(src, (1e9, 1e9))
    pesoption.refresh(folder)
    v2 = int(liveupdate._version(str(out)))
    assert v2 > v1
    # someone's own option-*.cpk (not made from an option file of ours) is never removed
    mine = tmp_path / "option-squads-2026.cpk"
    mine.write_bytes(out.read_bytes())
    foreign = tmp_path / "option-EDIT00000009.cpk"
    write_cpk(str(foreign), [("common/etc/version/version_wu.bin", str(mine))])
    src.unlink()
    pesoption.refresh(folder)
    assert not out.exists() and mine.exists() and foreign.exists()
    assert "EDIT00000000" not in pesoption.STATE[folder]


def test_broken_option_file_is_reported_then_forgotten(tmp_path, option_file, monkeypatch):
    _, game = option_file
    monkeypatch.setattr(pesoption, "game_cpks", lambda: [game])
    bad = tmp_path / "EDIT00000001"
    bad.write_bytes(b"x" * 5000)
    calls = []
    real = pesoption.build
    monkeypatch.setattr(pesoption, "build", lambda *a, **k: calls.append(1) or real(*a, **k))
    pesoption.refresh(str(tmp_path))
    st = pesoption.STATE[str(tmp_path)]["EDIT00000001"]
    assert "save" in st["error"] and not (tmp_path / "option-EDIT00000001.cpk").exists()
    pesoption.refresh(str(tmp_path))                             # unchanged: not tried again every pass
    assert len(calls) == 1
    bad.unlink()
    pesoption.refresh(str(tmp_path))
    assert "EDIT00000001" not in pesoption.STATE[str(tmp_path)]


def test_no_game_on_this_computer(tmp_path, option_file, monkeypatch):
    raw, _ = option_file
    monkeypatch.setattr(pesoption, "game_cpks", lambda: [])
    (tmp_path / "EDIT00000000").write_bytes(raw)
    pesoption.refresh(str(tmp_path))
    assert "isn't on this computer" in pesoption.STATE[str(tmp_path)]["EDIT00000000"]["error"]
    assert not pesoption.is_option_file("EDIT00000000.bak") and not pesoption.is_option_file("EDITOR")


def test_ids_with_the_flag_bit_are_kept():
    """Real squads hold player ids above 0x3ffff (bit 18 is a flag): they must stay in PlayerAssignment
    (dropping them changed entry_check_sum on the real data)."""
    flagged = 0x40000 | 300
    ed = {"players": [], "teams": {}, "squads": {2: [(flagged, 4)]}, "forms": {}}
    out, _ = pesoption.apply(_tables(), ed)
    rows = [struct.unpack_from("<IIII", out["PlayerAssignment.bin"], o) for o in range(0, len(out["PlayerAssignment.bin"]), 16)]
    assert (flagged, 2) in {(p, t) for _, p, t, _ in rows}


def test_tactic_settings_follow_the_game_plan_order():
    """A tactic's settings (after its formation sets) go into Tactics.bin's word; the option file's tactic 1 is the
    game plan without bit 24, whatever the file order (37 real teams list their second plan first)."""
    tables = _tables()
    keep = (0x15 << 12) | (1 << 24)                              # bits 12..18 aren't in the option file: kept
    tables["Tactics.bin"] = struct.pack("<III", 11, 1, keep) + struct.pack("<III", 12, 1, 0) + \
        struct.pack("<III", 21, 2, 0)
    ed = pesoption.read_edit(_edit_data())
    rec = bytearray(ed["forms"][1])
    s = 4 + 0x63                                                 # tactic 1's settings
    rec[s:s + 7] = bytes([1, 0, 1, 0, 0, 0, 1])                  # toggles -> bits 25, 28, 26
    rec[s + 41], rec[s + 42], rec[s + 43], rec[s + 44], rec[s + 45] = 7, 2, 4, 10, 3
    rec[4 + 0xa0 + 0x63 + 41] = 0                                # tactic 2: a broken level stays in range
    for k in range(11):                                          # tactic 2's formation: role 9
        rec[4 + 0xa0 + k] = 9
    ed["forms"][1] = bytes(rec)
    out, _ = pesoption.apply(tables, ed)
    words = {tac: w for tac, _, w in (struct.unpack_from("<III", out["Tactics.bin"], q) for q in range(0, 36, 12))}
    want = (1 << 25) | (1 << 28) | (1 << 26) | (9 << 0) | (6 << 4) | (3 << 8) | (1 << 19) | (2 << 21)
    assert words[12] == want                                     # tactic 12 (no bit 24) = the option file's tactic 1
    assert words[11] & keep == keep and (words[11] >> 4) & 15 == 0
    tf = out["TacticsFormation.bin"]
    roles = {}
    for q in range(0, len(tf), 12):
        tac, role, v = struct.unpack_from("<III", tf, q)
        if v >> 20 == 0:                                         # formation set 0
            roles.setdefault(tac, set()).add(role)
    assert roles[11] == {9} and roles[12] == {5}


def test_a_fresh_option_file_changes_nothing():
    """Names the game keeps in another field (a language's, the unlicensed short name) aren't renames; rows keep
    their ids and order."""
    tables = _tables()
    t = bytearray(tables["Team.bin"])
    t[0x5fc + 0x170:0x5fc + 0x176] = b"TWO\0\0\0"
    t[0x5fc + 0x5b6:0x5fc + 0x5bc] = b"TWO FC"                   # the option file's name, in another field
    tables["Team.bin"] = bytes(t)
    pa = b"".join(struct.pack("<IIII", rid, p, tid, f) for rid, p, tid, f in
                  ((900, 300, 1, 6 | 1 << 10), (901, 100, 1, 9), (902, 200, 2, 8), (903, 400, 2, 21 | 1 << 10)))
    tables["PlayerAssignment.bin"] = pa
    ed = pesoption.read_edit(_edit_data())
    ed["forms"] = {}
    out, summary = pesoption.apply(tables, ed)
    assert summary["teams_renamed"] == 0 and out["Team.bin"] == tables["Team.bin"]
    assert out["PlayerAssignment.bin"] == pa


def _crilayla(data, copy_at=None):
    """CRILAYLA-compress data (literals only, plus one 3-byte copy ending at output index copy_at), the way
    cpkread.crilayla reads it: bits backwards from the end, output written backwards, the first 0x100 bytes kept."""
    head, body = data[:0x100], data[0x100:]
    bits = []
    w = len(data) - 1
    while w >= 0x100:
        if copy_at is not None and w == copy_at:
            dist = 18                                            # copies data[w + 21], [w + 20], [w + 19]: the repeat
            assert data[w:w - 3:-1] == data[w + dist + 3:w + dist:-1]
            bits += [1] + [int(b) for b in f"{dist:013b}"] + [0, 0]
            w -= 3
        else:
            bits += [0] + [int(b) for b in f"{data[w]:08b}"]
            w -= 1
    bits += [0] * (-len(bits) % 8)
    stream = bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))[::-1]
    return b"CRILAYLA" + struct.pack("<II", len(body), len(stream)) + stream + head


def test_crilayla():
    data = bytes(range(256)) + b"0123456789ABCabcdefgh" * 3 + b"XYZ"
    at = 0x100 + 30
    packed = _crilayla(data, copy_at=at)
    assert cpkread.crilayla(packed) == data
    with pytest.raises(ValueError):
        cpkread.crilayla(packed[:-0x101])
    huge = packed[:8] + struct.pack("<I", 0xffffffff) + packed[12:]     # a damaged size: refused, nothing reserved
    with pytest.raises(ValueError):
        cpkread.crilayla(huge)
    with pytest.raises(ValueError):
        cpkread.crilayla(packed, expect=len(data) + 1)


def test_graphics_only_patch_survives_a_restart(tmp_path):
    """A patch with no database makes only extra-<name>; after a restart its note comes back from that file
    (reviewer: getmtime of the missing patch-<name> made every pass fail)."""
    folder = tmp_path / "lu"
    folder.mkdir()
    flag = tmp_path / "flag"
    flag.write_bytes(b"\x89PNG flag")
    write_cpk(str(folder / "flags.cpk"), [("common/render/symbol/flag/e_100.png", str(flag))])
    pesoption.refresh(str(folder))
    assert (folder / "extra-flags.cpk").is_file() and not (folder / "patch-flags.cpk").exists()
    pesoption.STATE.clear()
    pesoption.refresh(str(folder))
    assert pesoption.STATE[str(folder)]["flags.cpk"]["summary"]["graphics"] == 1


def _patch_cpk(path, tmp, with_version=False):
    player = pesoption.wrap(_player(100) + _player(500, speed=99))
    team = pesoption.wrap(_team(1, b"ONE") * 3)
    p1, p2, p3 = tmp / "pl", tmp / "tm", tmp / "face"
    packed = _crilayla(player.ljust(0x180, b"\0") if len(player) < 0x180 else player)
    p1.write_bytes(packed)
    p2.write_bytes(team)
    face = b"DDS " + bytes(range(256)) + b"face" * 40
    p3.write_bytes(_crilayla(face))
    files = [(pesoption.PDB + "Player.bin", str(p1), 0x180 if len(player) < 0x180 else len(player)),
             (pesoption.PDB + "Team.bin", str(p2)), ("Asset/model/character/face/real/1/face.dds", str(p3), len(face))]
    if with_version:
        files.append((pesoption.VERSION_FILE, str(p3)))
    write_cpk(str(path), files)
    return player


def test_patch_cpk_becomes_a_live_update(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    folder = tmp_path / "lu"
    folder.mkdir()
    player = _patch_cpk(folder / "SmokePatch 2026.cpk", work)
    _patch_cpk(folder / "already-a-live-update.cpk", work, with_version=True)
    (folder / "kits only.cpk").write_bytes(b"not a cpk")
    pesoption.refresh(str(folder))
    out = folder / "patch-SmokePatch 2026.cpk"
    st = pesoption.STATE[str(folder)]
    assert out.is_file() and set(st) == {"SmokePatch 2026.cpk"}
    assert st["SmokePatch 2026.cpk"]["summary"]["kind"] == "patch"
    assert st["SmokePatch 2026.cpk"]["summary"]["db_teams"] == 3
    got = cpkread.read_file(str(out), pesoption.PDB + "Player.bin")
    assert got[:len(player)] == player                           # the patch's table as it was (unpacked)
    assert cpkread.read_file(str(out), "Asset/model/character/face/real/1/face.dds") is None
    assert int(liveupdate._version(str(out))) > 199
    # ... and its other files (graphics) become the Extra Update, copied still compressed
    extra = folder / "extra-SmokePatch 2026.cpk"
    assert st["SmokePatch 2026.cpk"]["summary"]["graphics"] == 1
    assert cpkread.read_file(str(extra), "Asset/model/character/face/real/1/face.dds") == b"DDS " + bytes(range(256)) + b"face" * 40
    assert cpkread.read_file(str(extra), pesoption.PDB + "Player.bin") is None
    assert liveupdate._version(str(extra), "xu") == liveupdate._version(str(out)) and liveupdate._version(str(extra)) is None
    xu = cpkread.read_file(str(extra), liveupdate.EXTRA_VERSION_FILE)
    assert xu[20:30].rstrip(b"\0") == b"1.00"                    # the data pack it needs (else dropped at start)
    assert not (folder / "patch-already-a-live-update.cpk").exists()
    mtime = out.stat().st_mtime
    pesoption.refresh(str(folder))                               # unchanged: not built again
    assert out.stat().st_mtime == mtime
    extra.unlink()                                               # one half gone: made again
    pesoption.refresh(str(folder))
    assert extra.exists()
    (folder / "SmokePatch 2026.cpk").unlink()
    pesoption.refresh(str(folder))
    assert not out.exists() and not extra.exists()
    assert str(folder) in pesoption.STATE and not pesoption.STATE[str(folder)]
