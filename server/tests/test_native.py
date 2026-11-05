"""The downloadable program: certificates in Python, HTTP + HTTPS on one port, the local game list."""
import asyncio, os, ssl, subprocess
from pesserver import certs, gamesetup, httpgate, netsetup
from pesserver.accounts import Accounts
from pesserver.service import Service


def test_certs_made_once_and_old_ca_replaced(tmp_path, monkeypatch):
    d = str(tmp_path / "certs")
    crt, key = certs.ensure(d)
    first = open(crt, "rb").read()
    assert certs.ensure(d) == (crt, key) and open(crt, "rb").read() == first      # kept on the next start
    assert certs.ca_ok(os.path.join(d, "ca.crt"))
    ctx = ssl.create_default_context(cafile=os.path.join(d, "ca.crt"))
    ctx.load_cert_chain(crt, key)                                                   # key matches the cert
    # a CA without the name constraint (an old version) is replaced, and the banner is told
    monkeypatch.setattr(certs, "CA_REPLACED", False)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=old",
                    "-keyout", os.path.join(d, "ca.key"), "-out", os.path.join(d, "ca.crt")],
                   check=True, capture_output=True)
    certs.ensure(d)
    assert certs.CA_REPLACED and certs.ca_ok(os.path.join(d, "ca.crt")) and os.path.exists(os.path.join(d, "ca.crt.old"))


def test_http_and_https_on_one_port(tmp_path):
    tls = certs.ensure(str(tmp_path / "certs"))

    async def go():
        srv = await httpgate.serve(Service(Accounts(str(tmp_path / "a.db"))), "127.0.0.1", 0, tls=tls)
        port = srv.sock.getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET /status HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"); await w.drain()
        plain = await r.read(); w.close()
        ctx = ssl.create_default_context(cafile=str(tmp_path / "certs" / "ca.crt"))
        r, w = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname="ntl.service.konami.net")
        w.write(b"GET /status HTTP/1.1\r\nHost: ntl.service.konami.net\r\nConnection: close\r\n\r\n"); await w.drain()
        secure = await r.read(); w.close()
        srv.close()
        return plain, secure

    plain, secure = asyncio.run(go())
    assert plain.startswith(b"HTTP/1.1 200") and secure.startswith(b"HTTP/1.1 200")


def test_local_game_list(tmp_path, monkeypatch):
    g = tmp_path / "Games" / "pes" / "files"; g.mkdir(parents=True); (g / "PES2021.exe").write_bytes(b"MZ")
    pfx = tmp_path / "Games" / "pes" / "proton" / "pfx"; pfx.mkdir(parents=True); (pfx / "user.reg").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(gamesetup, "LOCAL", None)
    gamesetup.use_local_games()
    games = gamesetup._games()
    assert [(x["id"], x["dir"], x["prefix"]) for x in games] == [("1", str(g), str(pfx))]
    monkeypatch.setattr(gamesetup, "LOCAL", None)


def test_skipped_ports_are_not_mapped(monkeypatch):
    monkeypatch.setattr(netsetup, "PORTS", list(netsetup.PORTS))
    netsetup.skip_port(80)
    assert (80, "TCP") not in netsetup.PORTS and (8080, "TCP") in netsetup.PORTS
    assert netsetup.port_list() == "TCP 443,8080,10000 + UDP 3478,7600"
