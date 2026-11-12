"""The downloadable program: certificates in Python, HTTP + HTTPS on one port, the local game list."""
import asyncio, json, os, ssl, subprocess
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
    assert [(x["id"], x["dir"], x["prefix"]) for x in games] == [(gamesetup.gid(str(g)), str(g), str(pfx))]
    monkeypatch.setattr(gamesetup, "LOCAL", None)


def test_skipped_ports_are_not_mapped(monkeypatch):
    monkeypatch.setattr(netsetup, "PORTS", list(netsetup.PORTS))
    netsetup.skip_port(80)
    assert (80, "TCP") not in netsetup.PORTS and (8080, "TCP") in netsetup.PORTS
    monkeypatch.setenv("TURN", "0")
    assert netsetup.port_list() == "TCP 443,8080,10000 + UDP 3478,7600"
    monkeypatch.delenv("TURN")                       # the relay is on by default: its ports are listed as ranges
    assert netsetup.port_list() == "TCP 443,5349,8080,10000 + UDP 3478,5350,7600,49160-49179"


def test_admin_local_sign_in_and_internet_rules(tmp_path, monkeypatch):
    """Same PC: no password. Home network: password. Internet: only when switched on, only over HTTPS."""
    from pesserver.accounts import Accounts
    from pesserver.service import Service
    svc = Service(Accounts(str(tmp_path / "a.db"))); svc.admin_password = "pw-123456789"; svc.data_dir = str(tmp_path)
    httpgate._FAILED.clear()
    httpgate.load_remote(str(tmp_path))
    monkeypatch.setitem(netsetup.STATE, "lan", "192.168.1.50")
    h = {"host": "localhost:8080", "x-pes-local": "1"}

    def call(route, body=b"", peer="127.0.0.1", secure=False, method="POST", headers=h):
        code, out, _ = asyncio.run(httpgate._admin(svc, method, route, headers, body, peer, secure))
        return int(code.split()[0]), out

    code, out = call("/admin/api/login", b'{"local": true}')
    assert code == 200 and json.loads(out)["token"]                                      # this PC
    assert call("/admin/api/login", b'{"local": true}', peer="192.168.1.50")[0] == 200   # its own LAN address
    assert call("/admin/api/login", b'{"local": true}', peer="192.168.1.20")[0] == 401   # another device
    assert call("/admin/api/login", b'{"password": "pw-123456789"}', peer="192.168.1.20")[0] == 200
    assert call("/admin/api/login", b'{"local": true}', headers={"host": "evil.example", "x-pes-local": "1"})[0] == 403  # rebinding
    assert call("/admin/api/login", b'{"local": true}', headers={"host": "localhost:8080"})[0] == 401   # no custom header
    # internet: blocked until switched on, then HTTPS only
    assert call("/admin/api/login", b'{"password": "pw-123456789"}', peer="8.8.8.8", headers={"host": "203.0.113.7:8080"})[0] == 403
    httpgate.set_remote(True)
    assert os.path.exists(tmp_path / "admin_remote")                                     # kept across restarts
    plain = call("/admin", peer="8.8.8.8", method="GET", headers={"host": "203.0.113.7:8080"})
    assert plain[0] == 200 and b"https://203.0.113.7:8080/admin" in plain[1]             # sent to the secure page
    assert call("/admin/api/login", b'{"password": "pw-123456789"}', peer="8.8.8.8", headers={"host": "203.0.113.7:8080"})[0] == 403
    code, out = call("/admin/api/login", b'{"password": "pw-123456789", "remember": true}', peer="8.8.8.8",
                     secure=True, headers={"host": "203.0.113.7:8080"})
    assert code == 200
    assert call("/admin/api/login", b'{"local": true}', peer="8.8.8.8", secure=True, headers={"host": "203.0.113.7:8080", "x-pes-local": "1"})[0] == 401
    httpgate.set_remote(False)
    assert not os.path.exists(tmp_path / "admin_remote") and not httpgate.admin_allowed("8.8.8.8")


def test_browsers_get_the_admin_certificate(tmp_path):
    """A browser on https://<ip>:8080/admin (no SNI) sees the admin certificate, the game the NTL one."""
    tls = certs.ensure(str(tmp_path / "certs"))

    async def go():
        srv = await httpgate.serve(Service(Accounts(str(tmp_path / "a.db"))), "127.0.0.1", 0, tls=tls)
        port = srv.sock.getsockname()[1]
        names = {}
        for sni in (None, "ntl.service.konami.net"):
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            r, w = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname=sni)
            der = w.get_extra_info("ssl_object").getpeercert(binary_form=True)
            w.close()
            from cryptography import x509
            names[sni] = x509.load_der_x509_certificate(der).subject.rfc4514_string()
        srv.close()
        return names

    names = asyncio.run(go())
    assert "admin page" in names[None] and "ntl.service.konami.net" in names["ntl.service.konami.net"]
    assert len(certs.admin_fingerprint(str(tmp_path / "certs")).split(":")) == 32
