"""Starts a built program on spare ports and checks it answers like a server: plain HTTP, HTTPS on the
same port (the mod's network test), the admin page and the mod download. Then stops it.
    python packaging/smoke.py dist/pes2021-server-linux-x64
Used by .github/workflows/program.yml on every platform (no UPnP, nothing leaves the machine)."""
import json, os, signal, ssl, subprocess, sys, tempfile, time, urllib.request

exe = os.path.abspath(sys.argv[1])
data = tempfile.mkdtemp(prefix="pes-smoke-")
env = dict(os.environ, UPNP="0", PUBLIC_IP="127.0.0.1", UPDATE_CHECK="0", PESSERVER_NO_IPCHECK="1",
           PES_GATE_PORT="11000", PES_HTTP_PORT="18080", PES_HTTPS_PORT="18443", PES_JOIN_PORT="18081",
           PES_STUN_PORT="13478", PESSERVER_RELAY_PORT="17600", PESSERVER_BOOK="")
flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
p = subprocess.Popen([exe, "--data", data], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     creationflags=flags)


def get(url, ctx=None):
    with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
        return r.status, r.read()


try:
    for _ in range(60):
        try:
            status, body = get("http://127.0.0.1:18080/status")
            break
        except OSError:
            if p.poll() is not None:
                raise SystemExit("the program exited:\n" + p.stdout.read().decode(errors="replace"))
            time.sleep(1)
    else:
        raise SystemExit("no answer on :18080 after 60 s")
    assert json.loads(body)["id"], body
    # the game's network test: HTTPS on the same port, NTL name, verified against the server's CA
    import socket
    ctx = ssl.create_default_context(cafile=os.path.join(data, "certs", "ca.crt"))
    with ctx.wrap_socket(socket.create_connection(("127.0.0.1", 18080), timeout=5),
                         server_hostname="ntl.service.konami.net") as t:
        t.sendall(b"GET /status HTTP/1.1\r\nHost: ntl.service.konami.net\r\nConnection: close\r\n\r\n")
        assert t.recv(64).startswith(b"HTTP/1.1 200"), "network test over TLS"
    # a browser opening https://<address>:8080/admin (no name) gets the admin page's own certificate
    loose = ssl.create_default_context(); loose.check_hostname = False; loose.verify_mode = ssl.CERT_NONE
    with loose.wrap_socket(socket.create_connection(("127.0.0.1", 18080), timeout=5)) as t:
        der = t.getpeercert(binary_form=True)
    assert b"PES 2021 server admin page" in der, "admin certificate"
    assert b"PES 2021 Server" in get("http://127.0.0.1:18080/admin")[1]
    status, dll = get("http://127.0.0.1:18080/mod/dinput8.dll")
    assert status == 200 and dll[:2] == b"MZ" and len(dll) > 50000, len(dll)
    print("smoke test passed: http, https (game and admin certificates) on the same port, admin page, mod download")
finally:
    if sys.platform == "win32":
        p.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        p.send_signal(signal.SIGINT)
    try:
        out = p.communicate(timeout=20)[0].decode(errors="replace")
    except subprocess.TimeoutExpired:
        p.kill()
        out = p.communicate()[0].decode(errors="replace")
        print(out)
        raise SystemExit("the program did not stop on Ctrl+C / Ctrl+Break")
    print(out)
