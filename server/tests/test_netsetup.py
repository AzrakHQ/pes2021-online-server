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


def test_banner_lists_lan_and_internet_commands(monkeypatch):
    monkeypatch.setitem(netsetup.STATE, "mapped", ["80/tcp"])
    b = netsetup.banner()
    assert "irm http://192.168.1.50/join | iex" in b and "curl -fsSL http://203.0.113.7/join | bash" in b


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
            assert "$PesUninstall = 0 -eq 1" in ps
            sh = await get(port, "/leave", "curl/8.5.0")
            assert sh.startswith("#!/bin/bash") and 'SERVER="pes.example"' in sh and "UNINSTALL=1" in sh
            page = await get(port, "/", "Mozilla/5.0 Firefox/130")
            assert "<html" in page and "irm http://pes.example:%d/join | iex" % port in page
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
