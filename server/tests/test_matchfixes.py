"""Fixes found by mapping the game's online code, checked against recorded traffic (2026-11-05)."""
import base64, ipaddress
from types import SimpleNamespace
import msgpack
import pytest
from pesserver import announce, events, netsetup, pescmds, pesmatch
from pesserver.accounts import Accounts
from pesserver.service import Service


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("PESSERVER_PUBLIC_ADDR", raising=False)
    monkeypatch.setitem(netsetup.STATE, "wan", "81.2.69.160")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.100")
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


def addr(ip, port, as_str=True):
    """The game's address blob, exactly as recorded: {address_family 1, sa_data base64(port, 0, ip, 0...)}."""
    sa = port.to_bytes(2, "big") + b"\0\0" + ipaddress.IPv4Address(ip).packed + b"\0" * 6
    raw = msgpack.packb({"address_family": 1, "sa_data": base64.b64encode(sa).decode()})
    return raw.decode("utf-8", "surrogateescape") if as_str else raw


def ip_of(blob):
    raw = blob.encode("utf-8", "surrogateescape") if isinstance(blob, str) else blob
    sa = base64.b64decode(msgpack.unpackb(raw)["sa_data"])
    return str(ipaddress.IPv4Address(sa[4:8])), int.from_bytes(sa[:2], "big")


def test_recorded_blob_decodes():
    rec = "\udc82\udcaeaddress_family\x01\udca7sa_data\udcb4YfMAAMCoAWQAAAAAAAA="   # host's game, as logged
    assert ip_of(rec) == ("192.168.1.100", 25075)


def test_host_lan_address_is_rewritten_only_for_a_remote_friend(svc):
    host = SimpleNamespace(pes_pid=1, peer_ip="192.168.1.100", local_ip="192.168.1.100")
    friend = SimpleNamespace(pes_pid=2, peer_ip="41.236.194.222", local_ip="192.168.1.100")
    neighbour = SimpleNamespace(pes_pid=3, peer_ip="192.168.1.12", local_ip="192.168.1.100")
    st = pesmatch._state(svc)
    st["ntl"][1] = {"host_addr": addr("192.168.1.100", 25075), "reflexive_addr": addr("192.168.1.100", 25075)}
    st["ntl"][2] = {"host_addr": addr("10.0.0.5", 2945), "reflexive_addr": addr("41.236.194.222", 2945)}
    for viewer, want in ((friend, "81.2.69.160"), (neighbour, "192.168.1.100"), (host, "192.168.1.100")):
        m = msgpack.unpackb(pesmatch._member(svc, 1, True, 0, viewer), raw=False, unicode_errors="surrogateescape")
        assert ip_of(m["reflexive_addr"]) == (want, 25075)
        assert ip_of(m["host_addr"]) == ("192.168.1.100", 25075)        # the LAN address stays for LAN tries
    # the friend's public address is never touched
    m = msgpack.unpackb(pesmatch._member(svc, 2, False, 1, host), raw=False, unicode_errors="surrogateescape")
    assert ip_of(m["reflexive_addr"]) == ("41.236.194.222", 2945)
    r = pescmds._dispatch(svc, friend, "CMD_GET_IPANDPORT_NTL", {"pid": 1})
    assert ip_of(r["reflexive_addr"])[0] == "81.2.69.160"
    # loopback (the game on the server PC itself) and bytes blobs too; junk passes through
    assert ip_of(pesmatch._visible_addr(svc, addr("127.0.0.1", 99, as_str=False), friend)) == ("81.2.69.160", 99)
    assert pesmatch._visible_addr(svc, "junk", friend) == "junk"


def test_tailscale_friend_gets_the_servers_tailscale_address(svc, monkeypatch):
    monkeypatch.setenv("PESSERVER_PUBLIC_ADDR", "100.101.102.103")
    friend = SimpleNamespace(pes_pid=2, peer_ip="100.90.1.2", local_ip="100.101.102.103")
    assert ip_of(pesmatch._visible_addr(svc, addr("192.168.1.100", 5000), friend))[0] == "100.101.102.103"


def test_peer_notices_once_each_never_to_sender(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, *a: sent.append((s.pes_pid, msgid, a[-1]["data"])))
    a, b = SimpleNamespace(pes_pid=1), SimpleNamespace(pes_pid=2)
    svc.online.update({1: a, 2: b})
    pescmds._dispatch(svc, b, "CMD_SEND_SYNC_NOTICE", {"pid_list": [1, 1, 1, 2], "data": "1,"})
    assert sent == [(1, "CMD_WATCH_SYNC_NOTICE", "1,")]


def test_goal_from_both_games_counts_once(svc):
    for pid, user in ((1, "u1"), (2, "u2")):
        svc.accounts.db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, user, user))
    pesmatch._state(svc)["rooms"][1] = {"owner": 1, "members": [1, 2], "t": 0}
    a, b = SimpleNamespace(pes_pid=1, peer_ip="10.0.0.1"), SimpleNamespace(pes_pid=2, peer_ip="10.0.0.2")
    for s in (a, b):
        events.observe(svc, s, "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 2996})
    events.observe(svc, b, "CMD_ADD_SCORE", {"goal_side": "AWAY", "gtimer": 5000})
    live = events.snapshot(svc)["live"][0]
    assert (live["home_score"], live["away_score"]) == (0, 2) and "goals_seen" not in live


def test_message_too_long_for_one_frame_is_refused(svc):
    announce._STATE.clear()
    announce.set_news(svc, "T" * 60, "a" * 600)                         # Latin text always fits
    import random
    rnd = random.Random(1)
    arabic = "".join(rnd.choice("ابتثجحخدذرزسشصضطظعغفقكلمنهوي ") for _ in range(600))
    with pytest.raises(announce.TooLong):
        title = "".join(rnd.choice("ابتثجحخدذرزسشصضطظعغفقكلمنهوي") for _ in range(60))
        announce.set_news(svc, title, arabic)                          # 1056 bytes encoded, limit 1024
    assert announce.snapshot(svc)["news"]["body"] == "a" * 600         # the old message stays
