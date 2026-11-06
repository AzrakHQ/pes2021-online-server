"""TURN relay fallback (notes/re/functions/net.md): list, credentials, certificate, address forwarding."""
import asyncio, base64, hashlib, hmac
from types import SimpleNamespace

import pytest
from cryptography import x509

from pesserver import netsetup, pescmds, pesmatch, turn
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


@pytest.fixture
def coturn_up(tmp_path, monkeypatch):
    """turn.STATE as after start(), with a stand-in process that is 'running'."""
    d = tmp_path / "turn"; d.mkdir()
    der = turn._cert(str(d), ["192.168.1.100", "81.2.69.160", "127.0.0.1"])
    monkeypatch.setattr(turn, "STATE", {"server": object(), "dir": str(d),
                                        "secret": "s3cret", "cert_der": der, "ips": ()})


def test_no_turn_server_keeps_the_list_empty(svc, monkeypatch):
    monkeypatch.setattr(turn, "STATE", {**turn.STATE, "server": None})
    assert pescmds._dispatch(svc, None, "CMD_GET_TURN_SERVER_LIST", {"pid": "1"}) == {"result": "NOERR"}
    monkeypatch.setenv("TURN", "0")
    assert turn.ports() == []
    assert asyncio.run(turn.start(svc, svc.data_dir, "192.168.1.100", None)) is False


def test_list_has_address_credentials_and_certificate(svc, coturn_up):
    svc.online[2] = SimpleNamespace(pes_pid=2, peer_ip="41.236.194.222", local_ip="192.168.1.100")   # friend
    svc.online[1] = SimpleNamespace(pes_pid=1, peer_ip="192.168.1.12", local_ip="192.168.1.100")     # LAN
    r = pescmds._dispatch(svc, None, "CMD_GET_TURN_SERVER_LIST", {"pid": "2", "turn_account_param": "2",
                                                                  "turn_time_limit": "60"})
    (entry,) = r["turn_server_list"]
    assert entry["address"] == "81.2.69.160" and entry["port"] and entry["tls_port"] == turn.TLS_PORT
    assert pescmds._dispatch(svc, None, "CMD_GET_TURN_SERVER_LIST", {"pid": "1"})["turn_server_list"][0]["address"] \
        == "192.168.1.100"
    # coturn use-auth-secret: password = base64(HMAC-SHA1(secret, username)), username "<expiry>:<pid>"
    exp, pid = r["id"].split(":")
    assert pid == "2" and int(exp) > 0
    assert r["pw"] == base64.b64encode(hmac.new(b"s3cret", r["id"].encode(), hashlib.sha1).digest()).decode()
    der = base64.b64decode(r["server_certificate"])
    assert len(der) == r["server_certificate_data_size"] <= 0x800          # the game's buffer is 0x800 bytes
    ips = {str(i) for i in x509.load_der_x509_certificate(der).extensions
           .get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)}
    assert ips == {"192.168.1.100", "81.2.69.160", "127.0.0.1"}


def test_certificate_is_kept_until_the_addresses_change(tmp_path):
    a = turn._cert(str(tmp_path), ["192.168.1.100"])
    assert turn._cert(str(tmp_path), ["192.168.1.100"]) == a
    assert turn._cert(str(tmp_path), ["192.168.1.100", "81.2.69.160"]) != a


def test_relay_addresses_reach_the_opponent_in_either_order(svc, monkeypatch):
    sent = []
    monkeypatch.setattr(pesmatch, "_send_later", lambda s, msgid, rq, k, sq, body: sent.append((s.pes_pid, msgid, body["address"])))
    a, b = SimpleNamespace(pes_pid=1), SimpleNamespace(pes_pid=2)
    svc.online.update({1: a, 2: b})
    pesmatch._state(svc)["rooms"][1] = {"owner": 1, "members": [1, 2], "t": 0}
    blob_a, blob_b = "\udc80A-relay", "\udc81B-relay"                     # opaque bytes, kept exactly
    pescmds._dispatch(svc, a, "CMD_SEND_TURN_ADDRESS_DATA", {"address": blob_a, "address_data_size": 8})
    assert sent == [(2, "CMD_WATCH_TURN_ADDRESS_DATA", blob_a)]
    sent.clear()
    pescmds._dispatch(svc, b, "CMD_SEND_TURN_ADDRESS_DATA", {"address": blob_b, "address_data_size": 8})
    # B's address to A, and A's again to B (A's first push may have beaten B's watch)
    assert sent == [(1, "CMD_WATCH_TURN_ADDRESS_DATA", blob_b), (2, "CMD_WATCH_TURN_ADDRESS_DATA", blob_a)]
