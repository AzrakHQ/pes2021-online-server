"""Friendly Match Lobby rooms (pesrooms.py): the flow seen live on 2026-11-06, and the rules that keep the game
alive (no RELOAD pushes, no player-list push before a member's first ENTRY_GAME, room list echoes game_env)."""
import asyncio
from types import SimpleNamespace

import msgpack
import pytest

from pesserver import netsetup, pescmds, pesmatch, pesrooms
from pesserver.accounts import Accounts
from pesserver.service import Service
from test_matchfixes import addr


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("PESSERVER_PUBLIC_ADDR", raising=False)
    monkeypatch.setitem(netsetup.STATE, "wan", "81.2.69.160")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.100")
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def unb(b):
    raw = b.encode("utf-8", "surrogateescape") if isinstance(b, str) else b
    return msgpack.unpackb(raw, raw=False, unicode_errors="surrogateescape")


class Sess(SimpleNamespace):
    def __init__(self, pid):
        super().__init__(pes_pid=pid, peer_ip="192.168.1.100", local_ip="192.168.1.100", got=[], ever=[])

    async def send(self, msg):
        self.got.append((msg.name, msg.body))
        self.ever.append((msg.name, msg.body))

    def pushes(self, name=None):
        out = [(n, b) for n, b in self.got if name is None or n == name]
        self.got.clear()
        return out


ENV = msgpack.packb({"cpuLevel": "HARDEST", "gametime": 10, "injury": "YES", "ball_type": 22, "exGame": "NO",
                     "pkOnOff": "NO", "substitution": 3, "exSubstitution": "YES", "limitTime": "MIDDLE"},
                    use_bin_type=True).decode("utf-8", "surrogateescape")     # as the gate decodes it


def test_lobby_flow(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        st = pesmatch._state(svc)
        st["ntl"][1] = {"host_addr": addr("192.168.1.100", 25075), "reflexive_addr": addr("192.168.1.100", 25075)}
        st["ntl"][2] = {"host_addr": addr("192.168.1.100", 26000), "reflexive_addr": addr("192.168.1.100", 26000)}
        d = lambda s, m, r=None: pescmds._dispatch(svc, s, m, r or {})       # noqa: E731
        pause = lambda: asyncio.sleep(0.5)                                     # noqa: E731

        bl = d(a, "CMD_GET_BLOCKLIST")
        assert bl["count"] == 1 and unb(bl["bklist"][0])["block_id"] == 1
        for s in (a, b):
            r = d(s, "CMD_REQWATCHL_ROOMLIST")
            assert r["list_type"] == "RELOAD" and r["list_num"] == 0

        rid = d(a, "CMD_CREATEJOIN_ROOM", {"name": "Room A", "match_type": "OC_FREE", "max_players": 2,
                                           "is_invite_limit": "NO", "password": ""})["room_id"]
        assert d(a, "CMD_SET_GAMEENV", {"game_env": ENV})["result"] == "NOERR"
        await pause()
        for s in (a, b):                                # ADD, then UPDATE with the owner's real settings
            p = s.pushes("CMD_REQWATCHL_ROOMLIST")
            assert [x["list_type"] for _, x in p] == ["ADD", "UPDATE"]
            e = unb(p[-1][1]["list_data"][0])
            assert e["room_id"] == rid and unb(e["gameenv"])["limitTime"] == "MIDDLE" and e["status"] == "WAITING"
            assert unb(e["owner_pname"])["psn_user_id"] and isinstance(unb(unb(e["owner_pname"])["psn_user_id"]), dict)

        pl = d(a, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", {"room_id": rid})
        assert pl["list_type"] == "RELOAD" and unb(pl["list_data"][0])["is_owner"] == "YES"
        await pause()
        assert a.pushes("CMD_REQWATCHL_ROOMPLAYERLIST_NTL") == []     # nothing before the owner's ENTRY_GAME
        d(a, "CMD_ENTRY_GAME", {"entry": "FALSE", "side": "HOME"})
        await pause()
        assert [x["list_type"] for _, x in a.pushes("CMD_REQWATCHL_ROOMPLAYERLIST_NTL")] == ["UPDATE"]

        # the friend looks at the details, tries a wrong room, then joins
        assert d(b, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", {"room_id": rid})["list_num"] == 1
        assert d(b, "CMD_JOIN_ROOM_NTL", {"room_id": 99})["result"] == "ERR_ROOMNOTFOUND"
        j = d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid, "is_invited": "NO", "password": ""})
        assert j["result"] == "NOERR" and j["room_stat"] == "WAITING" and unb(j["room_owner"])["pid"] == 1
        await pause()
        upd = a.pushes("CMD_REQWATCHL_ROOMPLAYERLIST_NTL")
        assert [x["list_type"] for _, x in upd] == ["UPDATE"] and upd[0][1]["list_num"] == 2
        assert b.pushes("CMD_REQWATCHL_ROOMPLAYERLIST_NTL") == []     # B hasn't sent ENTRY_GAME yet
        assert d(b, "CMD_REQWATCHL_ROOMPLAYERLIST_NTL", {"room_id": rid})["list_num"] == 2
        d(b, "CMD_ENTRY_GAME", {"entry": "FALSE", "side": "AWAY"})

        # a third player finds the room full
        c = Sess(3); svc.online[3] = c
        assert d(c, "CMD_JOIN_ROOM_NTL", {"room_id": rid})["result"] == "ERR_ROOMISFULL"

        assert d(a, "CMD_SET_GAMEMEMBER")["result"] == "NOERR"         # nobody entered: no start
        await pause()
        assert not a.pushes("CMD_WATCH_ROOMSTATE")
        for s, side in ((a, "HOME"), (b, "AWAY")):
            assert d(s, "CMD_ENTRY_GAME", {"entry": "TRUE", "side": side})["entryflag"] == "TRUE"
        await pause()
        eg = a.pushes("CMD_WATCH_ENTRY_GAME")[-1][1]
        assert eg["entry_count"] == 2
        assert d(b, "CMD_SET_GAMEMEMBER")["result"] == "NOERR"         # not the owner: ignored
        d(a, "CMD_SET_GAMEMEMBER")
        await pause()
        for s in (a, b):
            names = [n for n, _ in s.got]
            assert "CMD_WATCH_DECIDE_GAMEENV" in names
            rs = [x for n, x in s.got if n == "CMD_WATCH_ROOMSTATE"]
            assert rs[-1]["state"] == "SETENV" and rs[-1]["owner_pid"] == 1
            s.pushes()
        # the match session finds the room
        m1, m2 = d(a, "CMD_GET_MATCHID")["match_id"], d(b, "CMD_GET_MATCHID")["match_id"]
        assert m1 and m1 == m2
        assert d(a, "CMD_SET_GAMEMEMBER")["result"] == "NOERR"         # the owner repeats it: just an ack
        assert d(c, "CMD_JOIN_ROOM_NTL", {"room_id": rid})["result"] in ("ERR_ROOMISFULL", "ERR_ROOMSTATNOTMATCH")

        # after the match: both leave the game -> back in the room
        d(a, "CMD_LEAVE_GAME", {"pid": 1})
        assert pesrooms._st(svc)["rooms"][rid]["state"] == "SETENV"
        d(b, "CMD_LEAVE_GAME", {"pid": 2})
        await pause()
        assert [x["state"] for _, x in b.pushes("CMD_WATCH_ROOMSTATE")] == ["WAITING"]
        assert pesrooms._st(svc)["rooms"][rid]["state"] == "WAITING"

        # the owner leaves: B is appointed; then B leaves: the room is gone from the list
        d(a, "CMD_LEAVE_ROOM", {"error_code": ""})
        await pause()
        rs = b.pushes()
        assert any(n == "CMD_WATCH_ROOMSTATE" and x["event"] == "AppointOwner" and x["owner_pid"] == 2 for n, x in rs)
        assert any(n == "CMD_REQWATCHL_ROOMPLAYERLIST_NTL" and x["list_type"] == "DEL" for n, x in rs)
        d(b, "CMD_LEAVE_ROOM")
        await pause()
        assert pesrooms._st(svc)["rooms"] == {}
        assert [x["list_type"] for _, x in a.pushes("CMD_REQWATCHL_ROOMLIST")][-1] == "DEL"

        # never a pushed RELOAD (it ends the game when its cache isn't empty)
        for s in (a, b, c):
            assert not any(x.get("list_type") == "RELOAD" for _, x in s.ever)
        assert len(a.ever) > 10
    asyncio.run(go())


def test_password_disconnect_and_hidden_fields(svc):
    async def go():
        a, b = Sess(1), Sess(2)
        svc.online.update({1: a, 2: b})
        d = lambda s, m, r=None: pescmds._dispatch(svc, s, m, r or {})       # noqa: E731
        rid = d(a, "CMD_CREATEJOIN_ROOM", {"name": "Locked", "password": "1234"})["room_id"]
        e = unb(d(b, "CMD_REQWATCHL_ROOMLIST")["list_data"][0])
        assert e["is_passwd"] == "YES" and unb(e["gameenv"])["limitTime"] != "NOSET"    # NOSET hides a room
        assert e["is_invite_limit"] == "NO" and e["gamer"]                               # either would hide it too
        assert d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid, "password": "0000"})["result"] == "ERR_PASSWDINCORRECT"
        assert d(b, "CMD_JOIN_ROOM_NTL", {"room_id": rid, "password": "1234"})["result"] == "NOERR"
        # a new room for the owner takes them out of the old one (one room per player)
        rid2 = d(a, "CMD_CREATEJOIN_ROOM", {"name": "Second"})["room_id"]
        assert pesrooms.room_of(svc, 1)["id"] == rid2 and pesrooms.room_of(svc, 2)["id"] == rid
        # disconnect = leave + unsubscribe
        pesrooms.disconnected(svc, a)
        assert rid2 not in pesrooms._st(svc)["rooms"] and 1 not in pesrooms._st(svc)["subs"]
        # outside a room SET_GAMEMEMBER stays a plain NOERR (Quick Match / co-op owners send it too)
        assert pescmds._dispatch(svc, Sess(9), "CMD_SET_GAMEMEMBER", {})["result"] == "NOERR"
    asyncio.run(go())
