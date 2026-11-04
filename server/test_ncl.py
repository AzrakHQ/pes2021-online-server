"""Offline protocol tests: real captured client requests through the NCL codec, and every command
handler's reply checked against the schema extracted from the client's own parsers."""
import asyncio, glob, os
import pytest
from pesserver import ncl, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service, Session

CAPS = os.path.join(os.path.dirname(__file__), "..", "notes", "re", "captures")


def _svc():
    return Service(Accounts(":memory:"), public_ip="127.0.0.1")


@pytest.mark.parametrize("cap", sorted(glob.glob(CAPS + "/*.bin")))
def test_real_capture_roundtrip(cap):
    async def go():
        r = asyncio.StreamReader(); r.feed_data(open(cap, "rb").read()); r.feed_eof()
        codec = ncl.NclCodec()
        msg = await codec.read(r)
        assert msg.name == "CMD_GET_SVRLIST" and msg.body["svrtype"] == "GATE"
        reply = await _svc().handle(Session("127.0.0.1", None), msg)
        kind, seq, obj = ncl.decode_frame(codec.encode(reply))
        assert kind == 0x60 and obj["result"] == "NOERR" and obj["server_num"] == len(obj["svrlist"])
    asyncio.run(go())


def test_every_handler_matches_schema():
    svc = _svc(); sess = Session("127.0.0.1", None)
    req = {"patch_version": "1.01.00", "dlc_version": "1.00", "username": "u1", "name": "Tester",
           "psid_low": 1, "steamid": "1"}
    order = ["CMD_LOGIN", "CMD_CREATE_PLAYER", "CMD_GET_PLAYER_DATA"]
    for name in order + [n for n in pescmds.HANDLERS if n not in order]:
        resp = pescmds.HANDLERS[name](svc, sess, dict(req))
        assert pescmds.validate(name, resp), name


def test_schema_defaults_are_typed():
    d = pescmds.default_response("CMD_GET_PLAYER_DATA")
    assert d["pid"] == 0 and d["has_data"] == "" and isinstance(d["pname"], bytes)


def test_non_utf8_str_from_client_is_tolerated():
    import msgpack
    raw = b"\x82\xa5msgid\xafCMD_TEST_BINARY\xa4blob\xa4\x82\xff\x00\x01"   # str field holding raw bytes
    plain = ncl.struct.pack(">IIB", len(raw), len(raw), 0) + raw
    plain += b"\0" * (-len(plain) % 8)
    obj, _ = ncl.unpack_plain(plain)
    assert obj["msgid"] == "CMD_TEST_BINARY" and obj["blob"].encode("utf-8", "surrogateescape") == b"\x82\xff\x00\x01"


def test_matchmaking_pairs_two_sessions_via_long_poll():
    import asyncio
    from pesserver import pesmatch
    from pesserver.protocol import Message

    async def go():
        svc = _svc()
        sent = []
        sessions = []
        for i, user in enumerate(("u1", "u2")):
            async def send(msg, i=i):
                sent.append((i, msg))
            s = Session("127.0.0.1", send)
            sessions.append(s)
            await svc.handle(s, Message("CMD_LOGIN", 1, {"username": user, "_kind": 0x60, "_seq": 1}))
            await svc.handle(s, Message("CMD_CREATE_PLAYER", 2, {"name": f"P{i}", "_kind": 0x60, "_seq": 1}))
            await svc.handle(s, Message("CMD_SEND_IPANDPORT_NTL", 3,
                                        {"host_addr": b"\x82\xadaddress_family\x02\xa7sa_data\xa4abcd", "_kind": 0x60, "_seq": 1}))
        r = await svc.handle(sessions[0], Message("CMD_WATCH_MATCHING", 4, {"rqid": 4, "_kind": 0x60, "_seq": 1}))
        assert r.body.get("_defer")
        await svc.handle(sessions[0], Message("CMD_START_MATCHING", 5, {"_kind": 0x60, "_seq": 1}))
        await svc.handle(sessions[1], Message("CMD_START_MATCHING", 5, {"_kind": 0x60, "_seq": 1}))
        await asyncio.sleep(0)
        assert sorted(i for i, m in sent) == [0, 1]              # pushed to both players
        body = sent[0][1].body
        assert body["player_num"] == 2 and len(body["member"]) == 2 and body["matching_state"] == "FINISH"
        assert pescmds.validate("CMD_WATCH_MATCHING", {k: v for k, v in body.items() if not k.startswith("_")})
    asyncio.run(go())


def test_replay_real_matching_pair():
    """Two real recorded CMD_START_MATCHING / SEND_IPANDPORT_NTL requests (binary sa_data) must pair."""
    import asyncio, json, os
    from pesserver.protocol import Message
    rx = os.path.join(os.path.dirname(__file__), "..", "logs", "rx.jsonl")
    if not os.path.exists(rx):
        pytest.skip("no recorded traffic")
    def dec(v):
        if isinstance(v, dict):
            return bytes.fromhex(v["__bin__"]) if set(v) == {"__bin__"} else {k: dec(x) for k, x in v.items()}
        return [dec(x) for x in v] if isinstance(v, list) else v
    recs = [json.loads(l) for l in open(rx)]
    ntl = [dec(r["body"]) for r in recs if r["dir"] == "rx" and r["msgid"] == "CMD_SEND_IPANDPORT_NTL"]
    sm = [dec(r["body"]) for r in recs if r["dir"] == "rx" and r["msgid"] == "CMD_START_MATCHING"]
    if not ntl or not sm:
        pytest.skip("matching not recorded yet")

    async def go():
        svc = _svc(); out = []
        for i, u in enumerate(("a", "b")):
            async def send(m, i=i): out.append((i, m))
            s = Session("127.0.0.1", send)
            await svc.handle(s, Message("CMD_LOGIN", 1, {"username": u, "_kind": 0x60, "_seq": 1}))
            await svc.handle(s, Message("CMD_SEND_IPANDPORT_NTL", 2, {**ntl[0], "_kind": 0x60, "_seq": 1}))
            r = await svc.handle(s, Message("CMD_START_MATCHING", 3, {**sm[0], "_kind": 0x60, "_seq": 1}))
            assert r.body["result"] == "NOERR"
            if i == 0:
                await svc.handle(s, Message("CMD_WATCH_MATCHING", 4, {"rqid": 4, "_kind": 0x60, "_seq": 1}))
        await asyncio.sleep(0)
        from pesserver import ncl
        codec = ncl.NclCodec()
        wire = codec.encode(out[0][1])          # must encode the binary sa_data blobs
        assert ncl.decode_frame(wire)[2]["player_num"] == 2
    asyncio.run(go())


def test_sync_notice_is_relayed_to_listed_pids():
    import asyncio
    from pesserver.protocol import Message
    from pesserver import pesmatch  # noqa: F401

    async def go():
        svc = _svc(); out = []
        ss = []
        for i, u in enumerate(("a", "b")):
            async def send(m, i=i): out.append((i, m))
            s = Session("127.0.0.1", send); ss.append(s)
            await svc.handle(s, Message("CMD_LOGIN", 1, {"username": u, "_kind": 0x60, "_seq": 1}))
        p1 = ss[1].pes_pid
        await svc.handle(ss[0], Message("CMD_SEND_SYNC_NOTICE", 5, {"pid_list": [p1], "data": "1,", "_kind": 0x60, "_seq": 1}))
        await asyncio.sleep(0)
        assert [(i, m.name, m.body["data"]) for i, m in out] == [(1, "CMD_WATCH_SYNC_NOTICE", "1,")]
    asyncio.run(go())


def test_http_webapi_replies_msgpack_and_gateinfo_text(tmp_path, monkeypatch):
    """The web API must answer form POSTs with a raw msgpack map (JSON makes the client fail with
    C_GKCC_0008_200); GateInfo.php answers the NTL text format."""
    import asyncio, msgpack
    from pesserver import httpgate
    monkeypatch.chdir(tmp_path)  # no dev-only files around

    async def post(port, path, body):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Type: application/x-www-form-urlencoded\r\n"
                f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await w.drain()
        data = await r.read(); w.close()
        return data.split(b"\r\n\r\n", 1)[1]

    async def go():
        srv = await httpgate.serve(_svc(), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            rep = msgpack.unpackb(await post(port, "/u5", b"msgid=CMD_AUTH_STEAM&rqid=3&urltype=AUTH_STEAM"), raw=False)
            assert rep["msgid"] == "CMD_AUTH_STEAM" and rep["rqid"] == 3 and rep["result"] == "NOERR"
            txt = await post(port, "/ntl/api/GateInfo.php", b"x=1")
            assert txt.startswith(b"STATUS: 200") and b"API_STATUS: 0" in txt
            assert await post(port, "/admin/push", b"") == b"ok"   # dev hook is off by default
        finally:
            srv.close()
    asyncio.run(go())
