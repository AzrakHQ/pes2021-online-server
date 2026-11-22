"""Small features: data check in rooms, room chat, the host's Live Update (2026-11-06)."""
import asyncio, hashlib, os, shutil, struct, tempfile

import msgpack
import pytest

from pesserver import liveupdate, pescmds, pesmatch, pesrooms
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)


def test_room_refuses_a_friend_with_different_data(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        a.pes_sums = {"param": "AAA", "entry": "E1"}
        b.pes_sums = {"param": "AAA", "entry": "E2"}          # same stats, different rosters/shirt numbers
        rid = pescmds._dispatch(svc, a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
        assert pescmds._dispatch(svc, b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})["result"] == "ERR_DIFFDCVERSION"
        b.pes_sums = {"param": "AAA", "entry": "E1"}
        assert pescmds._dispatch(svc, b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})["result"] == "NOERR"
        # Quick Match uses the same rule: both sums
        assert pesmatch._compatible(a, b)
        b.pes_sums = {"param": "AAA", "entry": "E9"}
        assert not pesmatch._compatible(a, b)
    asyncio.run(go())


def test_room_chat(svc):
    async def go():
        a, b, c = Sess(1), Sess(2), Sess(3)
        svc.online.update({1: a, 2: b, 3: c})
        rid = pescmds._dispatch(svc, a, "CMD_CREATEJOIN_ROOM", {"name": "R"})["room_id"]
        pescmds._dispatch(svc, b, "CMD_JOIN_ROOM_NTL", {"room_id": rid})
        await asyncio.sleep(0.5)
        for s in (a, b, c):
            s.pushes()
        assert pescmds._dispatch(svc, b, "CMD_SEND_TEXTCHAT", {"channel": "ROOM", "statement": "gl hf",
                                                                 "list_flg": "CHAT_SEND", "list_pid": []})["result"] == "NOERR"
        await asyncio.sleep(0.1)
        for s in (a, b):
            (n, body), = s.pushes("CMD_WATCH_TEXTCHAT")
            assert body["from_pid"] == 2 and body["statement"] == "gl hf" and body["channel"] == "ROOM"
        assert c.pushes("CMD_WATCH_TEXTCHAT") == []             # not in the room
    asyncio.run(go())


def _cpk(path, version="1.02"):
    from cpkwrite_for_tests import write_cpk
    d = tempfile.mkdtemp()
    p = os.path.join(d, "version_wu.bin")
    open(p, "wb").write(b"".join(f.encode().ljust(10, b"\0") for f in (version, "1.01.00", "test")))
    write_cpk(path, [("common/etc/version/version_wu.bin", p)])
    shutil.rmtree(d)


def test_live_update_from_the_data_folder(svc, tmp_path):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "notes", "re", "functions", "tools"))
    pytest.importorskip("cpkwrite")
    import cpkwrite
    sys.modules["cpkwrite_for_tests"] = cpkwrite
    sess = Sess(1)
    r = pescmds._dispatch(svc, sess, "CMD_GET_DLC_FILELIST", {"pid": "0"})
    assert r["dlcfile_list_count"] == 0                       # nothing published: as before
    os.makedirs(liveupdate.folder(svc))
    cpk = os.path.join(liveupdate.folder(svc), "Roster_Nov.cpk")
    _cpk(cpk, "1.02")
    r = pescmds._dispatch(svc, sess, "CMD_GET_DLC_FILELIST", {"pid": "0"})
    (e,) = r["dlcfile_list"]
    assert e["dlc_version"] == "102" and e["size"] == os.path.getsize(cpk)
    assert e["hash"] == hashlib.sha256(open(cpk, "rb").read()).hexdigest()
    assert e["url"].endswith("/liveupdate/Roster_Nov.cpk")
    assert liveupdate.file_for(svc, "roster_nov.cpk") == cpk and liveupdate.file_for(svc, "../x.cpk") is None
    # a CPK without the version file is not offered
    open(os.path.join(liveupdate.folder(svc), "broken.cpk"), "wb").write(b"CPK junk")
    os.utime(os.path.join(liveupdate.folder(svc), "broken.cpk"), None)
    assert pescmds._dispatch(svc, sess, "CMD_GET_DLC_FILELIST", {})["dlcfile_list"][0]["name"] == "Roster_Nov.cpk"


def test_extra_update_from_the_data_folder(svc, monkeypatch):
    """A CPK with version_xu.bin is the Extra Update: listed in extra_file_list with an https address on the game's
    own NTL name (its downloader only does HTTPS; live 2026-11-22), next to the Live Update and served the same."""
    from pesserver.cpkwrite import write_cpk
    monkeypatch.setattr(liveupdate, "_CACHE", {})
    monkeypatch.setattr(liveupdate, "_LAST", {})
    monkeypatch.delenv("PESSERVER_HTTP_PORT", raising=False)
    sess = Sess(1)
    r = pescmds._dispatch(svc, sess, "CMD_GET_DLC_FILELIST", {"pid": "0"})
    assert "extra_file_list" not in r                        # (count and list must agree: none sent at all)
    d = liveupdate.folder(svc)
    os.makedirs(d)
    tmp = tempfile.mkdtemp()
    xu, flag = os.path.join(tmp, "xu"), os.path.join(tmp, "flag")
    open(xu, "wb").write(b"".join(f.encode().ljust(10, b"\0") for f in ("1.02", "1.01.00", "1.00", "test")))
    open(flag, "wb").write(b"\x89PNG")
    write_cpk(os.path.join(d, "Flags 2026.cpk"), [("common/etc/version/version_xu.bin", xu),
                                                 ("common/render/symbol/flag/e_100.png", flag)])
    r = pescmds._dispatch(svc, sess, "CMD_GET_DLC_FILELIST", {"pid": "0"})
    (x,) = r["extra_file_list"]
    assert r["extra_file_list_count"] == 1 and x["dlc_version"] == "102" and x["name"] == "Flags_2026.cpk"
    assert x["url"] == "https://ntl.service.konami.net:8080/liveupdate/Flags_2026.cpk"
    assert all(e["name"] != "Flags_2026.cpk" for e in (r["dlcfile_list"] or []))    # not a Live Update
    assert liveupdate.file_for(svc, "flags_2026.cpk") == os.path.join(d, "Flags 2026.cpk")
    shutil.rmtree(tmp)
