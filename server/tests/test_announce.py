"""Server text on players' screens: reply shapes that the real game accepted (tested 2026-11-05)."""
import asyncio, json, time
from types import SimpleNamespace
import msgpack
from pesserver import announce, httpgate, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service
from test_netsetup import _http


def _svc(tmp_path):
    svc = Service(Accounts(str(tmp_path / "a.db")))
    svc.data_dir = str(tmp_path)
    announce._STATE.clear()
    return svc


def call(svc, msgid, **req):
    return pescmds._dispatch(svc, SimpleNamespace(pes_pid=req.get("pid")), msgid, req)


def test_nothing_set_keeps_the_old_bare_replies(tmp_path):
    svc = _svc(tmp_path)
    for m in ("CMD_GET_GLOBALINFOTITLELIST", "CMD_GET_URGENT_ANNOUNCEMENT", "CMD_GET_MAINTENANCE_INFO", "CMD_GET_INFOBODY"):
        assert call(svc, m, pid=1, announce_id=5) == {"result": "NOERR"}


def test_news_list_body_and_one_box_per_player(tmp_path):
    svc = _svc(tmp_path)
    announce.set_news(svc, " Cup night ", "Line one\r\nLine two\x07")
    r = call(svc, "CMD_GET_GLOBALINFOTITLELIST")
    assert r["annnounce_info_list_num"] == 1 and pescmds.validate("CMD_GET_GLOBALINFOTITLELIST", r)
    item = msgpack.unpackb(r["announce_info_list"][0])
    assert item["title"] == "Cup night" and item["announce_type"] == "NO_BANNER_INFO"
    assert call(svc, "CMD_GET_INFOBODY", announce_id=item["announce_id"] + 1) == {"result": "NOERR"}
    body = call(svc, "CMD_GET_INFOBODY", announce_id=item["announce_id"])
    info = msgpack.unpackb(body["announce_info"])
    assert info["body"] == "Line one\nLine two" and msgpack.unpackb(info["announce_basic_info"])["announce_id"] == item["announce_id"]
    u = call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=7, online_mode="QUICK", announcement_type="PRE_MAIN")
    data = msgpack.unpackb(u["announcement_data"])
    assert u["is_announcement"] == "YES" and data["online_mode"] == "QUICK" and data["title"] == "Cup night"
    assert call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=7) == {"result": "NOERR"}           # once per player
    assert call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=8)["is_announcement"] == "YES"
    announce._STATE.clear()                                                                 # survives a restart
    assert announce.snapshot(svc)["news"]["seen"] == 2
    announce.set_news(svc, "New", "Edited")                                                 # a new message is shown again
    assert call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=7)["is_announcement"] == "YES"


def test_kick_note_comes_first_and_once(tmp_path):
    svc = _svc(tmp_path)
    announce.set_news(svc, "News", "Body")
    announce.tell(svc, 3, "You were disconnected", "Reason: spam")
    first = msgpack.unpackb(call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=3)["announcement_data"])
    assert first["title"] == "You were disconnected"
    assert msgpack.unpackb(call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=3)["announcement_data"])["title"] == "News"


def test_restart_warning_and_expiry(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    announce.set_restart(svc, 10)
    r = call(svc, "CMD_GET_MAINTENANCE_INFO")
    assert r["maintenance_warn"] == "YES" and abs(r["maintenance_time"] - (time.time() + 600)) < 5
    assert pescmds.validate("CMD_GET_MAINTENANCE_INFO", r)
    later = time.time() + 600 + announce.RESTART_GRACE + 1
    monkeypatch.setattr(announce.time, "time", lambda: later)
    assert call(svc, "CMD_GET_MAINTENANCE_INFO") == {"result": "NOERR"}
    assert announce.snapshot(svc)["restart"] is None


def test_restart_time_in_the_past_is_dropped_on_start(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / "announce.json").write_text(json.dumps({"restart": time.time() - 60}))
    assert announce.snapshot(svc)["restart"] is None


def test_admin_routes(tmp_path):
    svc = _svc(tmp_path)
    svc.admin_password = "s3cret"
    httpgate._FAILED.clear()
    closed = []
    svc.online[4] = SimpleNamespace(pes_pid=4, peer_ip="10.0.0.4", close=lambda: closed.append(4))

    async def go():
        srv = await httpgate.serve(svc, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            token = json.loads((await _http(port, "POST", "/admin/api/login", b'{"password": "s3cret"}'))[1])["token"]
            auth = [("Authorization", "Bearer " + token)]
            assert (await _http(port, "POST", "/admin/api/message", b'{"title": "", "body": "x"}', auth))[0] == 400
            code, out = await _http(port, "POST", "/admin/api/message", b'{"title": "Hi", "body": "There"}', auth)
            assert code == 200 and json.loads(out)["news"]["title"] == "Hi"
            assert (await _http(port, "POST", "/admin/api/restart", b'{"minutes": 99999}', auth))[0] == 400
            code, out = await _http(port, "POST", "/admin/api/restart", b'{"minutes": 15}', auth)
            assert code == 200 and json.loads(out)["restart"] > time.time()
            state = json.loads((await _http(port, "GET", "/admin/api/state", headers=auth))[1])
            assert state["announce"]["news"]["body"] == "There" and state["announce"]["restart"]
            assert (await _http(port, "POST", "/admin/api/kick", b'{"pid": 4, "reason": "\\u0007"}', auth))[0] == 200
            assert closed == [4]
            note = msgpack.unpackb(call(svc, "CMD_GET_URGENT_ANNOUNCEMENT", pid=4)["announcement_data"])
            assert note["body"] == "The host disconnected you from this server."
            assert (await _http(port, "POST", "/admin/api/message", b'{"clear": true}', auth))[0] == 200
            assert (await _http(port, "POST", "/admin/api/restart", b'{"minutes": 0}', auth))[0] == 200
            assert announce.snapshot(svc)["news"] is None and announce.snapshot(svc)["restart"] is None
        finally:
            srv.close()
    asyncio.run(go())
