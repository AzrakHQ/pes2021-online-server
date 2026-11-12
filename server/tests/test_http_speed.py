"""The admin page's transfer savings: compressed responses, a cached page (ETag), {"same": tag} when the state
didn't change between polls, and one kept-open connection for a browser's polls. Raw HTTP clients that read to
the end of the stream (scripts, the game) still get Connection: close."""
import asyncio, gzip, json
from pesserver import httpgate, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service


def _svc(tmp_path):
    svc = Service(Accounts(str(tmp_path / "a.db")))
    svc.admin_password = "s3cret"
    svc.data_dir = str(tmp_path)
    httpgate._FAILED.clear()
    pescmds._db(svc)
    return svc


async def _req(r, w, method, path, headers=(), body=b""):
    """One request on an open connection; reads exactly one response (by Content-Length)."""
    h = "".join(f"{k}: {v}\r\n" for k, v in headers)
    w.write(f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{h}Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await w.drain()
    head = (await r.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
    hdr = {k.lower(): v for k, v in (l.split(": ", 1) for l in head[1:] if ": " in l)}
    out = await r.readexactly(int(hdr.get("content-length", 0)))
    return int(head[0].split()[1]), hdr, out


def _serve(svc, test):
    async def go():
        srv = await httpgate.serve(svc, "127.0.0.1", 0)
        try:
            await test(srv.sockets[0].getsockname()[1])
        finally:
            srv.close()
    asyncio.run(go())


def test_page_compressed_cached_and_polls_share_a_connection(tmp_path):
    svc = _svc(tmp_path)

    async def test(port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        keep = [("Connection", "keep-alive"), ("Accept-Encoding", "gzip, deflate, br")]
        code, hdr, page = await _req(r, w, "GET", "/admin", keep)
        assert code == 200 and hdr["content-encoding"] == "gzip" and hdr["connection"] == "keep-alive"
        assert gzip.decompress(page).startswith(b"<!doctype html>") and len(page) < 100_000
        assert hdr["cache-control"] == "no-cache" and hdr["etag"].startswith('"')
        code, hdr2, again = await _req(r, w, "GET", "/admin", keep + [("If-None-Match", hdr["etag"])])
        assert (code, again, hdr2["etag"]) == (304, b"", hdr["etag"])        # same connection, nothing re-sent
        code, _, out = await _req(r, w, "POST", "/admin/api/login", keep, b'{"password": "s3cret"}')
        auth = keep + [("Authorization", "Bearer " + json.loads(out)["token"])]
        code, hdr, out = await _req(r, w, "GET", "/admin/api/state", auth)
        st = json.loads(gzip.decompress(out) if hdr.get("content-encoding") == "gzip" else out)
        assert code == 200 and hdr["cache-control"] == "no-store" and st["tag"] and "players" in st
        code, _, out = await _req(r, w, "GET", "/admin/api/state", auth + [("X-State-Tag", st["tag"])])
        same = json.loads(out)
        assert same["same"] == st["tag"] and set(same) == {"same", "now", "uptime"}
        svc.accounts.db.execute("INSERT INTO pes_players(pid, username, name, created) VALUES(7, 'u7', 'Gamma', 0)")
        code, hdr, out = await _req(r, w, "GET", "/admin/api/state", auth + [("X-State-Tag", st["tag"])])
        st2 = json.loads(gzip.decompress(out) if hdr.get("content-encoding") == "gzip" else out)
        assert "same" not in st2 and st2["tag"] != st["tag"] and any(p["name"] == "Gamma" for p in st2["players"])
        w.close()
    _serve(svc, test)


def test_plain_clients_still_get_the_stream_closed(tmp_path):
    svc = _svc(tmp_path)

    async def test(port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET /admin HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")       # no keep-alive, no gzip asked
        await w.drain()
        data = await asyncio.wait_for(r.read(), 5)                         # ends: the server closed it
        head, _, body = data.partition(b"\r\n\r\n")
        assert b"Connection: close" in head and b"Content-Encoding" not in head and body.startswith(b"<!doctype html>")
        w.close()
    _serve(svc, test)


def test_gzip_only_when_asked_and_worth_it():
    big, small = b"x" * 5000, b"x" * 100
    assert httpgate.gzipped(big, "application/json", {"accept-encoding": "gzip"})[1].startswith(b"Content-Encoding: gzip")
    assert httpgate.gzipped(big, "application/json", {"accept-encoding": "gzip;q=0"})[0] == big
    assert httpgate.gzipped(big, "application/json", {})[0] == big
    assert httpgate.gzipped(small, "application/json", {"accept-encoding": "gzip"})[0] == small
    assert httpgate.gzipped(big, "application/octet-stream", {"accept-encoding": "gzip"}) == (big, b"")
