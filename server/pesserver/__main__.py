import argparse, asyncio, logging, sys
from . import gate, httpgate, stun
from .accounts import Accounts, AccountError
from .protocol import JsonCodec
from .codec_capture import CaptureCodec
from .ncl import NclCodec

CODECS = {'ncl': NclCodec, 'json': JsonCodec, 'capture': CaptureCodec}
from .service import Service


async def run(a):
    svc = Service(Accounts(a.db), public_ip=a.public_ip, allow_register=not a.no_register)
    servers = [await gate.serve(svc, CODECS[a.codec], a.host, a.gate_port),
               await httpgate.serve(svc, a.host, a.http_port, a.public_ip or "127.0.0.1", a.stun_port, a.gate_port)]
    import os as _os
    cert = _os.path.join(_os.path.dirname(_os.path.abspath(a.db)), "certs")
    if _os.path.exists(_os.path.join(cert, "server.crt")):
        servers.append(await httpgate.serve_tls(svc, a.host, a.https_port, _os.path.join(cert, "server.crt"),
                                                _os.path.join(cert, "server.key"), a.public_ip or "127.0.0.1",
                                                a.stun_port, a.gate_port))
        logging.info("https tcp/%d", a.https_port)
    if a.join_port and a.join_port != a.http_port:      # short URLs for players: http://server/join
        try:
            servers.append(await httpgate.serve(svc, a.host, a.join_port))
        except OSError as e:
            logging.warning("join page not on port %d (%s); use :%d instead", a.join_port, e, a.http_port)
    st = await stun.serve(a.stun_host or a.host, a.stun_port)
    await stun.serve_relay(a.stun_host or a.host, int(_os.environ.get("PESSERVER_RELAY_PORT", "7600")))
    logging.info("gate tcp/%d  http tcp/%d  stun udp/%d", a.gate_port, a.http_port, a.stun_port)
    from . import netsetup
    data_dir = _os.path.dirname(_os.path.abspath(a.db))
    svc.admin_password = netsetup.admin_password(data_dir)
    # inside the container, not the data volume: a stale copy must never be shown by install.sh
    banner_file = _os.environ.get("PESSERVER_BANNER_FILE", "/tmp/pesserver-banner.txt")
    try:
        _os.unlink(banner_file)
    except OSError:
        pass

    def show_banner(_url=None):
        if not netsetup.STATE["ready"]:
            return
        print(netsetup.banner(svc.admin_password), flush=True)
        with open(banner_file, "w") as f:      # read by install.sh (has the admin password: local only)
            f.write(netsetup.banner(svc.admin_password))
        _os.chmod(banner_file, 0o600)

    tunnel = None
    if _os.environ.get("TUNNEL", "1") != "0":
        tunnel = asyncio.create_task(netsetup.run_tunnel(a.http_port, on_url=show_banner))
    if a.auto_net:
        await asyncio.to_thread(netsetup.setup, use_upnp=_os.environ.get("UPNP", "1") != "0")
    else:
        netsetup.STATE["lan"] = netsetup.lan_ip()
    for _ in range(40):                        # give the tunnel a moment to get its address
        if tunnel is None or netsetup.STATE.get("url") or netsetup.STATE.get("url_error") or tunnel.done():
            break
        await asyncio.sleep(0.5)
    netsetup.STATE["ready"] = True
    show_banner()
    stop = asyncio.Event()
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        logging.info("shutting down")
        if a.auto_net:
            await asyncio.to_thread(netsetup.teardown)
        st.close()
        for s in servers:
            s.close()


def main():
    p = argparse.ArgumentParser(prog="pesserver")
    p.add_argument("--db", default="pesserver.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--public-ip"); s.add_argument("--no-register", action="store_true")
    s.add_argument("--codec", choices=sorted(CODECS), default="ncl")
    s.add_argument("--gate-port", type=int, default=10000)
    s.add_argument("--http-port", type=int, default=8080)
    s.add_argument("--stun-port", type=int, default=3478)
    s.add_argument("--https-port", type=int, default=8443)
    s.add_argument("--stun-host", help="bind address for STUN (default: --host)")
    s.add_argument("--join-port", type=int, default=0, help="also serve the player pages on this port (80)")
    s.add_argument("--auto-net", action="store_true",
                   help="detect LAN/public addresses and open router ports with UPnP")
    u = sub.add_parser("adduser"); u.add_argument("name"); u.add_argument("password"); u.add_argument("--steam-id")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if a.cmd == "adduser":
        try:
            print("created user id", Accounts(a.db).register(a.name, a.password, a.steam_id))
        except AccountError as e:
            sys.exit(f"error: {e}")
    else:
        asyncio.run(run(a))


main()
