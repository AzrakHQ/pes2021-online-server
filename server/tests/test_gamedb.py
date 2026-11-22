"""The game data editor (gamedb.py) on made-up tables: player and team changes, transfers, sending them as a
Live Update (edits.cpk) and building it again on top of a new update under it."""
import json
import os
import struct

import pytest

from pesserver import cpkread, gamedb, liveupdate, pesoption
from pesserver.cpkwrite import write_cpk

A, B = 101, 108                                   # two clubs (web/teams.json: not national teams)


def _player(pid, speed=70, name=b"OLD"):
    r = bytearray(312)
    struct.pack_into("<I", r, 8, pid)
    off, sh, w, add = pesoption.PMAP[33]
    pesoption._put(r, off, sh, w, speed - add)
    off, sh, w, add = pesoption.PMAP[45]          # age 25
    pesoption._put(r, off, sh, w, 25 - add)
    off, sh, w, add = pesoption.PMAP[13]          # position GK (0): its best one, as in the game's data
    pesoption._put(r, off, sh, w, 2)
    r[0xfb:0xfb + len(name)] = name
    r[0x81:0x81 + 3] = r[0xbe:0xbe + 3] = b"OLD"
    return bytes(r)


def _team(tid, name):
    r = bytearray(0x5fc)
    struct.pack_into("<I", r, 8, tid)
    r[0x170:0x170 + len(name)] = name
    r[0x372:0x375] = name[:3]
    return bytes(r)


def _country(cid, name):
    r = bytearray(0x58c)
    struct.pack_into("<I", r, 0, cid << 10)
    r[0x120:0x120 + len(name)] = name
    return bytes(r)


A_IDS = [1000 + i for i in range(17)] + [0x4032f]        # 0x4032f and 0x32f: two players (the full id counts)
B_IDS = [2000 + i for i in range(17)] + [0x32f]


def _tables():
    players = b"".join(_player(p, name=f"P{p}".encode()) for p in A_IDS + B_IDS)
    rows, rid = [], 0
    for tid, ids in ((A, A_IDS), (B, B_IDS)):
        for slot, p in enumerate(ids):
            rid += 1
            rows.append(struct.pack("<IIII", rid, p, tid, (slot << 10) | slot | (1 << 21 if slot == 0 else 0)))
    return {"Player.bin": players, "Team.bin": _team(A, b"ALPHA") + _team(B, b"BRAVO"),
            "PlayerAssignment.bin": b"".join(rows), "Country.bin": _country(7, b"ATLANTIS") + _country(9, b"MU")}


@pytest.fixture
def game(tmp_path):
    srcs = []
    for n, blob in _tables().items():
        p = tmp_path / n
        p.write_bytes(pesoption.wrap(blob))
        srcs.append((pesoption.PDB + n, str(p)))
    c = tmp_path / "dt80.cpk"
    write_cpk(str(c), srcs)
    folder = tmp_path / "liveupdate"
    folder.mkdir()
    gamedb._STATE.clear()
    return str(folder), [str(c)]


def test_player_changes_and_undo(game):
    folder, cpks = game
    p = gamedb.set_player(folder, 1003, {"name": "NEW NAME", "nat": 7, "p": {"33": 99, "45": 25}}, cpks)
    assert p["name"] == "NEW NAME" and p["p"]["33"] == 99 and p["nat"] == 7
    e = gamedb.read_edits(folder)
    assert e["players"] == {"1003": {"name": "NEW NAME", "nat": 7, "p": {"33": 99}}}      # (age unchanged: dropped)
    with pytest.raises(ValueError):
        gamedb.set_player(folder, 1003, {"p": {"33": 120}}, cpks)
    with pytest.raises(ValueError):
        gamedb.set_player(folder, 1003, {"name": "x" * 61}, cpks)
    gamedb.set_player(folder, 1003, {"name": "P1003", "nat": 0, "p": {"33": 70}}, cpks)     # back as it was
    assert gamedb.read_edits(folder)["players"] == {}


def test_full_player_ids(game):
    """0x4032f is not 0x32f: a change to one leaves the other alone."""
    folder, cpks = game
    gamedb.set_player(folder, 0x4032f, {"p": {"33": 95}}, cpks)
    _, _, v = gamedb.state(folder, cpks)
    assert v.param(0x4032f, 33) == 95 and v.param(0x32f, 33) == 70
    assert [t["id"] for t in v.player(0x32f)["teams"]] == [B]


def test_transfer_and_squad_rules(game):
    folder, cpks = game
    _, _, v = gamedb.state(folder, cpks)
    a = [[s["id"], s["shirt"]] for s in v.team(A)["squad"]]
    t = gamedb.set_team(folder, A, {"name": "ALPHA FC", "squad": a + [[2005, 50]]}, cpks)
    assert t["name"] == "ALPHA FC" and t["squad"][-1]["id"] == 2005 and t["squad"][-1]["shirt"] == 50
    _, e, v = gamedb.state(folder, cpks)
    assert [x["id"] for x in v.player(2005)["teams"]] == [A]               # left his other club
    assert len(v.team(B)["squad"]) == 17 and set(e["squads"]) == {str(A), str(B)}
    assert v.team(A)["squad"][0]["captain"]                                 # roles stay with the player
    assert gamedb.count(e) == 2
    with pytest.raises(ValueError, match="number 50"):
        gamedb.set_team(folder, A, {"squad": a + [[2006, 50], [2007, 50]]}, cpks)
    with pytest.raises(ValueError, match="16 to 40"):
        gamedb.set_team(folder, A, {"squad": a[:10]}, cpks)
    with pytest.raises(ValueError, match="BRAVO would be left"):          # taking two more leaves B with 15
        gamedb.set_team(folder, A, {"squad": a + [[2005, 50], [2006, 51], [2007, 52]]}, cpks)
    gamedb.undo(folder, "team", A)
    assert gamedb.read_edits(folder)["squads"] == {str(B): gamedb.read_edits(folder)["squads"][str(B)]}


def test_send_offer_take_back(game, monkeypatch):
    folder, cpks = game
    gamedb.set_player(folder, 1001, {"p": {"33": 88}}, cpks)
    st = gamedb.status(folder)
    assert st["unsent"] and st["changes"] == 1 and st["sent"] is None
    s = gamedb.build(folder, cpks)
    out = os.path.join(folder, gamedb.OUT)
    P = pesoption.unwrap(cpkread.read_file(out, pesoption.PDB + "Player.bin"))
    o = A_IDS.index(1001) * 312
    assert pesoption.unwrap(cpkread.read_file(out, pesoption.PDB + "Player.bin"))[o:o + 312] != _tables()["Player.bin"][o:o + 312]
    assert gamedb._param(P, o, 33) == 88
    assert liveupdate._version(out) == s["version"].replace(".", "")
    st = gamedb.status(folder)
    assert not st["unsent"] and st["sent"]["changes"] == 1
    gamedb.set_player(folder, 1002, {"p": {"33": 77}}, cpks)
    assert gamedb.status(folder)["unsent"]
    gamedb.take_back(folder)
    assert gamedb.read_edits(folder)["players"] == {"1001": {"p": {"33": 88}}} and not gamedb.status(folder)["unsent"]


def test_on_top_of_another_update(game):
    """A Live Update in the folder (an option file's, a patch's...) is the data edited; edits.cpk carries its other
    files too, and is built again (with what was sent) when that update changes."""
    folder, cpks = game
    tmp = os.path.dirname(folder)
    t = _tables()
    t["Player.bin"] = t["Player.bin"].replace(b"P1001", b"Q1001")
    srcs = []
    for n in ("Player.bin", "Team.bin", "PlayerAssignment.bin"):
        p = os.path.join(tmp, "b-" + n)
        open(p, "wb").write(pesoption.wrap(t[n]))
        srcs.append((pesoption.PDB + n, p))
    ver = os.path.join(tmp, "ver")
    open(ver, "wb").write(b"300.00".ljust(10, b"\0") + b"1.01.00".ljust(10, b"\0") + b"x".ljust(10, b"\0"))
    other = os.path.join(tmp, "other")
    open(other, "wb").write(b"a face")
    base = os.path.join(folder, "option-EDIT00000000.cpk")
    write_cpk(base, srcs + [(pesoption.VERSION_FILE, ver), ("Asset/face.dds", other)])
    _, _, v = gamedb.state(folder, cpks)
    assert v.pname(1001) == "Q1001"
    gamedb.set_player(folder, 1001, {"p": {"33": 90}}, cpks)
    s1 = gamedb.build(folder, cpks)
    out = os.path.join(folder, gamedb.OUT)
    assert int(s1["version"].replace(".", "")) > 30000                     # above the update under it
    assert cpkread.read_file(out, "Asset/face.dds") == b"a face"
    assert gamedb.refresh(folder, cpks) is None                            # nothing changed under it
    gamedb.set_player(folder, 1002, {"p": {"33": 60}}, cpks)               # (not sent)
    write_cpk(base, srcs + [(pesoption.VERSION_FILE, ver)])                # a new update under it
    os.utime(base, (os.path.getmtime(out) + 5,) * 2)
    s2 = gamedb.refresh(folder, cpks)
    assert s2 and int(s2["version"].replace(".", "")) > int(s1["version"].replace(".", ""))
    assert s2["edits"]["players"] == {"1001": {"p": {"33": 90}}}            # what was sent, not the draft
    assert cpkread.read_file(out, "Asset/face.dds") is None


def test_admin_api(game):
    folder, cpks = game
    st, r = gamedb.admin_api(folder, "/admin/api/data/info", b"{}", cpks)
    assert st.startswith("200") and r["meta"]["abilities"][0]["name"] == "Offensive Awareness"
    assert [c[1] for c in r["countries"]] == ["ATLANTIS", "MU"]
    st, r = gamedb.admin_api(folder, "/admin/api/data/find", json.dumps({"q": "p100"}).encode(), cpks)
    assert r["players"][0]["name"].startswith("P100")
    st, r = gamedb.admin_api(folder, "/admin/api/data/find", json.dumps({"q": "alp"}).encode(), cpks)
    assert r["teams"][0][:2] == [A, "ALPHA"]
    st, r = gamedb.admin_api(folder, "/admin/api/data/player/save",
                             json.dumps({"id": 1000, "p": {"33": 91}}).encode(), cpks)
    assert r["player"]["p"]["33"] == 91 and r["st"]["edited"]["players"] == [[1000, "P1000"]]
    st, r = gamedb.admin_api(folder, "/admin/api/data/send", b"{}", cpks)
    assert st.startswith("200") and r["sent"]["version"] and not r["st"]["unsent"]
    st, r = gamedb.admin_api(folder, "/admin/api/data/player", json.dumps({"id": 1}).encode(), cpks)
    assert st.startswith("404")
