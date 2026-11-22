"""Each player's mod version (modversion.py) for the admin page's player panel."""
import asyncio

from pesserver import events, gate, httpgate, modversion, pescmds
from test_pesrooms import svc, Sess   # noqa: F401  (fixture)

MID = "ab" * 16


class W:
    def __init__(self, ip):
        self.ip = ip

    def get_extra_info(self, k):
        return (self.ip, 5) if k == "peername" else None


def _ua(v):
    return {"user-agent": f"PES2021Online/{v}"}


def _id_line(s, line):
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(line)
        r.feed_eof()
        await gate.take_player_id(r, s)
    asyncio.run(go())


def test_hello_then_login_saves_the_version(svc):
    httpgate._mod(svc, "/mod/hello", _ua("0.8.0"), W("10.0.0.7"), "/mod/hello?n=" + "0" * 32 + "&v=4")
    s = Sess(None)
    s.peer_ip = "10.0.0.7"
    _id_line(s, f"PESID2 {MID} math1 coop1 trophy1\n".encode())
    assert s.mod_version == "0.8.0"
    pescmds._dispatch(svc, s, "CMD_LOGIN", {"username": "steam1"})
    pid = s.pes_pid
    with svc.accounts.lock:
        pescmds._db(svc).execute("UPDATE pes_players SET name='Omar' WHERE pid=?", (pid,))
    assert events.player_detail(svc, pid)["mod_version"] == "0.8.0"
    # a newer game later says so through the requests that name its install id
    httpgate._mod(svc, "/mod/bracket", _ua("0.8.1"), W("10.0.0.7"), f"/mod/bracket?id={MID}")
    assert s.mod_version == "0.8.1"
    svc.online.pop(pid)                                      # offline: from the database
    assert events.player_detail(svc, pid)["mod_version"] == "0.8.1"


def test_other_addresses_and_bad_agents_say_nothing(svc):
    httpgate._mod(svc, "/mod/hello", {"user-agent": "Mozilla/5.0"}, W("10.0.0.8"), "/mod/hello?n=" + "0" * 32)
    httpgate._mod(svc, "/mod/hello", _ua("0.8.1"), W("10.0.0.9"), "/mod/hello?n=" + "0" * 32)
    s = Sess(None)
    s.peer_ip = "10.0.0.8"
    _id_line(s, f"PESID2 {'cd' * 16} math1\n".encode())
    assert getattr(s, "mod_version", None) is None
    httpgate._mod(svc, "/mod/final", _ua("9.9.9"), W("10.0.0.8"), "/mod/final?id=" + "ef" * 16)   # nobody online


def test_server_version_and_compare(tmp_path):
    dll = tmp_path / "dinput8.dll"
    dll.write_bytes(b"MZ\0\0" + "PES2021Online/0.8.1".encode("utf-16-le") + b"\0\0rest")
    assert modversion.server_version(str(dll)) == "0.8.1"
    assert modversion.server_version(str(tmp_path / "none.dll")) is None
    assert modversion.from_headers({"user-agent": "PES2021Online/0.8.1"}) == "0.8.1"
    assert modversion.from_headers({"user-agent": "PES2021Online/0.8.1 extra"}) is None
