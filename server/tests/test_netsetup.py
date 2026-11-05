"""Zero-config networking, the player pages and the Proton CA installer."""
import asyncio, importlib.util, os, subprocess, sys
from types import SimpleNamespace
import pytest
from pesserver import httpgate, netsetup
from pesserver.accounts import Accounts
from pesserver.service import Service

WEB = os.path.join(os.path.dirname(httpgate.__file__), "web")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("PESSERVER_PUBLIC_ADDR", raising=False)
    monkeypatch.setitem(netsetup.STATE, "wan", "203.0.113.7")
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.50")


def test_advertised_addr_per_client():
    svc = SimpleNamespace(public_ip=None)
    lan = SimpleNamespace(peer_ip="192.168.1.20", local_ip="192.168.1.50")
    vpn = SimpleNamespace(peer_ip="100.101.1.2", local_ip="100.64.0.9")     # Tailscale (CGNAT range)
    wan = SimpleNamespace(peer_ip="8.8.4.4", local_ip="192.168.1.50")  # from the internet via router
    assert netsetup.advertised_addr(svc, lan) == "192.168.1.50"
    assert netsetup.advertised_addr(svc, vpn) == "100.64.0.9"
    assert netsetup.advertised_addr(svc, wan) == "203.0.113.7"
    assert netsetup.advertised_addr(SimpleNamespace(public_ip="10.9.9.9"), wan) == "10.9.9.9"   # pinned


def test_igd_description_and_ssdp_parsing():
    xml = b"""<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><deviceList>
      <device><serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
      <controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></root>"""
    assert netsetup.parse_igd_description(xml, "http://192.168.1.1:5000/rootDesc.xml") == (
        "http://192.168.1.1:5000/ctl/IPConn", "urn:schemas-upnp-org:service:WANIPConnection:1")
    assert netsetup.parse_ssdp_location(b"HTTP/1.1 200 OK\r\nLOCATION: http://h/d.xml\r\n\r\n") == "http://h/d.xml"


def test_banner(monkeypatch, tmp_path):
    monkeypatch.setitem(netsetup.STATE, "mapped", ["80/tcp"])
    b = netsetup.banner("pw-1", color=False)
    assert "Admin page   http://192.168.1.50/admin" in b and "203.0.113.7" not in b
    monkeypatch.setenv("ADMIN_REMOTE", "1")
    assert "from anywhere: http://203.0.113.7/admin" in netsetup.banner(color=False)
    monkeypatch.delenv("ADMIN_REMOTE")
    assert "pw-1" in b and "download the one file" in b
    assert "join" not in b and "irm " not in b and "trycloudflare" not in b
    assert "Password" not in netsetup.banner(color=False)
    assert netsetup.game_addrs() == ["192.168.1.50", "203.0.113.7"]
    assert "certificate" not in b
    flag = tmp_path / "ca-replaced"; flag.write_text("1")
    monkeypatch.setenv("PESSERVER_CA_REPLACED", str(flag))
    assert "New server certificate" in netsetup.banner(color=False)


def test_join_pages_follow_user_agent_and_host(tmp_path):
    async def get(port, path, ua):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.1\r\nHost: pes.example:{port}\r\nUser-Agent: {ua}\r\n\r\n".encode())
        await w.drain(); data = await r.read(); w.close()
        return data.split(b"\r\n\r\n", 1)[1].decode()

    async def go():
        srv = await httpgate.serve(Service(Accounts(str(tmp_path / "a.db"))), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            ps = await get(port, "/join", "Mozilla/5.0 (Windows NT 10.0) WindowsPowerShell/5.1")
            assert '$PesBase = "http://pes.example:%d"' % port in ps and "@@" not in ps
            assert '"192.168.1.50 203.0.113.7".Split' in ps        # address candidates for the hosts file
            assert "$PesUninstall = 0 -eq 1" in ps
            sh = await get(port, "/leave", "curl/8.5.0")
            assert sh.startswith("#!/bin/bash") and 'SERVER="pes.example"' in sh and "UNINSTALL=1" in sh
            page = await get(port, "/", "Mozilla/5.0 Firefox/130")
            assert "<html" in page and 'href="/mod/dinput8.dll"' in page and "irm " not in page
            assert (await get(port, "/install-ca-proton.py", "curl/8")).startswith("#!/usr/bin/env python3")
        finally:
            srv.close()
    asyncio.run(go())


def test_join_sh_is_valid_bash():
    subprocess.run(["bash", "-n", os.path.join(WEB, "join.sh")], check=True)


def _ca_module():
    spec = importlib.util.spec_from_file_location("ica", os.path.join(WEB, "install-ca-proton.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_proton_ca_install_and_remove(tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "PES2021 Private Server CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    ca = tmp_path / "ca.crt"; ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pfx = tmp_path / "pfx"; pfx.mkdir()
    original = "WINE REGISTRY Version 2\n;; All keys relative to REGISTRY\\\\Machine\n\n[Software\\\\A] 1\n\"x\"=dword:00000001\n"
    (pfx / "system.reg").write_text(original)
    m = _ca_module()
    monkeypatch.setattr(m, "game_running", lambda: False)
    thumb = cert.fingerprint(hashes.SHA1()).hex().upper()
    for _ in range(2):                                    # idempotent: re-running doesn't duplicate
        monkeypatch.setattr(sys, "argv", ["x", "--ca", str(ca), str(pfx)]); m.main()
    text = (pfx / "system.reg").read_text()
    assert text.count(thumb) == 1 and '"Blob"=hex:20,00,00,00,01,00,00,00,' in text
    assert "\n[Software\\\\Microsoft\\\\SystemCertificates\\\\Root\\\\Certificates\\\\%s] " % thumb in text
    assert all(len(l) <= 80 for l in text.splitlines() if not l.startswith("["))
    monkeypatch.setattr(sys, "argv", ["x", "--remove", "--ca", str(ca), str(pfx)]); m.main()
    assert (pfx / "system.reg").read_text().strip() == original.strip()
    # Joining another server replaces the previous server's CA.
    monkeypatch.setattr(sys, "argv", ["x", "--ca", str(ca), str(pfx)]); m.main()
    key2 = ec.generate_private_key(ec.SECP256R1())
    cert2 = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key2.public_key())
             .serial_number(2).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
             .sign(key2, hashes.SHA256()))
    ca.write_bytes(cert2.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(sys, "argv", ["x", "--ca", str(ca), str(pfx)]); m.main()
    text = (pfx / "system.reg").read_text()
    assert thumb not in text and cert2.fingerprint(hashes.SHA1()).hex().upper() in text
    assert "[Software\\\\A] 1" in text


def _http(port, method, path, body=b"", headers=()):
    async def go():
        r, w = await asyncio.open_connection("127.0.0.1", port)
        h = "".join(f"{k}: {v}\r\n" for k, v in headers)
        w.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\n{h}Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await w.drain(); data = await r.read(); w.close()
        head, _, out = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), out
    return go()


def test_admin_api_needs_password_and_tracks_a_match(tmp_path):
    import json
    from pesserver import events, pescmds
    svc = Service(Accounts(str(tmp_path / "a.db")))
    svc.admin_password = "s3cret"
    pescmds._db(svc)
    for pid, user, name in ((1, "u1", "Alpha"), (2, "u2", "Bravo")):
        svc.accounts.db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(?,?,?,0)", (pid, user, name))
    from pesserver import pesmatch
    pesmatch._state(svc)["rooms"][1] = {"owner": 1, "members": [1, 2], "t": 0}
    a, b = SimpleNamespace(pes_pid=1, peer_ip="10.0.0.1"), SimpleNamespace(pes_pid=2, peer_ip="10.0.0.2")
    closed = []
    b.close = lambda: closed.append(2)
    svc.online.update({1: a, 2: b})
    for s_ in (a, b):
        for ph in ("MATCHMENU", "1ST"):
            events.observe(svc, s_, "CMD_CHANGE_GAMEPHASE", {"phase": ph})
    events.observe(svc, a, "CMD_ADD_GOAL", {"side": "AWAY"})
    snap = events.snapshot(svc)
    assert snap["live"][0]["phase"] == "1st half" and snap["live"][0]["away_score"] == 1
    assert {p["status"] for p in snap["online"]} == {"In a match"}
    for s_ in (a, b):
        events.observe(svc, s_, "CMD_SEND_GAME_RESULT", {"home_score": 2, "away_score": 1, "abnormalend_reason": "NOERR"})
    snap = events.snapshot(svc)
    assert len(snap["results"]) == 1 and snap["results"][0]["home_score"] == 2       # saved once
    alpha = next(p for p in snap["players"] if p["name"] == "Alpha")
    assert (alpha["played"], alpha["won"]) == (1, 1)
    assert any("Full time: Alpha 2-1 Bravo" in f["text"] for f in snap["feed"])

    async def go():
        srv = await httpgate.serve(svc, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            assert (await _http(port, "GET", "/admin"))[0] == 200
            assert (await _http(port, "GET", "/admin/api/state"))[0] == 401
            assert (await _http(port, "GET", "/admin/api/state", headers=[("Authorization", "Bearer nope")]))[0] == 401
            code, out = await _http(port, "GET", "/admin/api/state", headers=[("Authorization", "Bearer s3cret")])
            assert code == 200 and json.loads(out)["results"][0]["away_name"] == "Bravo"
            code, _ = await _http(port, "POST", "/admin/api/kick", b'{"pid": 2}', [("Authorization", "Bearer s3cret")])
            assert code == 200 and closed == [2]
        finally:
            srv.close()
    asyncio.run(go())


def test_admin_password_is_created_once(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    pw = netsetup.admin_password(str(tmp_path))
    assert len(pw) == 14 and netsetup.admin_password(str(tmp_path)) == pw
    monkeypatch.setenv("ADMIN_PASSWORD", "mine")
    assert netsetup.admin_password(str(tmp_path)) == "mine"


def test_abandoned_pairing_is_not_left_live(tmp_path):
    from pesserver import events, pescmds, pesmatch
    svc = Service(Accounts(str(tmp_path / "a.db"))); pescmds._db(svc)
    a, b = SimpleNamespace(pes_pid=1), SimpleNamespace(pes_pid=2)
    rooms = pesmatch._state(svc)["rooms"]
    rooms[1] = {"owner": 1, "members": [1, 2], "t": 0}
    events.observe(svc, a, "CMD_CHANGE_GAMEPHASE", {"phase": "DATASYNC"})
    events.observe(svc, b, "CMD_LEAVE_ROOM", {})                 # left before kick-off
    assert events.snapshot(svc)["live"] == []
    events.observe(svc, a, "CMD_CHANGE_GAMEPHASE", {"phase": "MATCHMENU"})   # stale room 1 again
    rooms[2] = {"owner": 1, "members": [1, 2], "t": 1}           # re-paired
    events.observe(svc, a, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST"})
    live = events.snapshot(svc)["live"]
    assert [m["room"] for m in live] == [2] and live[0]["phase"] == "1st half"


def test_proton_ca_remove_all_needs_no_server(tmp_path, monkeypatch):
    """The standalone leave.sh removes every PES2021 CA without the server's ca.crt."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime
    name = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "PES2021 Private Server CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    pfx = tmp_path / "pfx"; pfx.mkdir()
    original = "WINE REGISTRY Version 2\n\n[Software\\\\A] 1\n\"x\"=dword:00000001\n"
    (pfx / "system.reg").write_text(original)
    m = _ca_module(); monkeypatch.setattr(m, "game_running", lambda: False)
    for serial in (1, 2):                                   # two different servers joined over time
        k = ec.generate_private_key(ec.SECP256R1())
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(serial).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
             .sign(k, hashes.SHA256()))
        ca = tmp_path / f"ca{serial}.crt"; ca.write_bytes(c.public_bytes(serialization.Encoding.PEM))
        text = (pfx / "system.reg").read_text()
        (pfx / "system.reg").write_text(text.rstrip("\n") + "\n\n" + m.reg_block(c.fingerprint(hashes.SHA1()).hex().upper(),
                                        __import__("struct").pack("<III", 32, 1, len(c.public_bytes(serialization.Encoding.DER))) + c.public_bytes(serialization.Encoding.DER)))
    assert (pfx / "system.reg").read_text().count("Root\\\\Certificates") == 2
    monkeypatch.setattr(sys, "argv", ["x", "--remove-all", str(pfx)]); m.main()
    assert (pfx / "system.reg").read_text().strip() == original.strip()


def test_mod_identity_note_hello_and_dll(tmp_path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from pesserver import identity
    identity.init(str(tmp_path))
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity.public_hex()))
    assert (tmp_path / "identity.key").stat().st_mode & 0o777 == 0o600
    identity.init(str(tmp_path))                                        # same key after a restart
    assert Ed25519PublicKey.from_public_bytes(bytes.fromhex(identity.public_hex())) is not None

    def check(text):
        sig, body = text.split("\n", 1)
        pub.verify(base64.b64decode(sig), body.encode())               # raises if forged
        return body

    marker = identity.EMBED_MARKER
    dll = tmp_path / "dinput8.dll"
    dll.write_bytes(b"MZ..head.." + marker + b"\0" * (identity.EMBED_SIZE - len(marker)) + b"..tail")
    monkeypatch.setattr(httpgate, "MOD_DLL", str(dll))
    async def get(port, path, host="pes.example.org"):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nX-Forwarded-Proto: https\r\n\r\n".encode())
        await w.drain(); data = await r.read(); w.close()
        head, body = data.split(b"\r\n\r\n", 1)
        return head.decode(), body

    async def go():
        srv = await httpgate.serve(Service(Accounts(str(tmp_path / "a.db"))), "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        try:
            head, body = await get(port, "/mod/note")
            lines = check(body.decode()).split("\n")
            assert lines[0] == "PES2021ONLINE-NOTE 1" and lines[2] == "-"
            assert lines[3:5] == ["192.168.1.50", "203.0.113.7"]
            head, body = await get(port, "/mod/hello?n=00112233aabbccdd")
            assert check(body.decode()) == f"PES2021ONLINE-HELLO 1\n{identity.public_hex()}\n00112233aabbccdd\n"
            head, body = await get(port, "/mod/hello?n=not-hex")
            assert "400" in head
            head, body = await get(port, "/mod/dinput8.dll")
            assert 'filename="dinput8.dll"' in head and len(body) == dll.stat().st_size
            area = body[body.find(marker) + len(marker):][:identity.EMBED_SIZE - len(marker)].rstrip(b"\0").decode()
            assert f"key={identity.public_hex()}" in area and "url=https://pes.example.org" in area
            assert "addrs=192.168.1.50,203.0.113.7" in area and body.endswith(b"..tail")
            monkeypatch.setattr(httpgate, "MOD_DLL", str(tmp_path / "missing.dll"))
            head, body = await get(port, "/mod/dinput8.dll")
            assert "404" in head
        finally:
            srv.close()
    asyncio.run(go())


def test_admin_only_from_own_networks(monkeypatch):
    monkeypatch.delenv("ADMIN_REMOTE", raising=False)
    for ok in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "172.17.0.1", "100.101.1.2", "::1", "::ffff:192.168.1.9", "fd00::1"):
        assert httpgate.admin_allowed(ok), ok
    for bad in ("8.8.8.8", "156.204.152.194", "2001:4860::1", "", "nonsense"):
        assert not httpgate.admin_allowed(bad), bad
    monkeypatch.setenv("ADMIN_REMOTE", "1")
    assert httpgate.admin_allowed("8.8.8.8")
    monkeypatch.delenv("ADMIN_REMOTE")
    monkeypatch.setitem(netsetup.STATE, "lan", "8.8.4.4")       # a cloud server with a public address
    assert httpgate.admin_allowed("8.8.8.8")
