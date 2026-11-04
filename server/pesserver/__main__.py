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
    st = await stun.serve(a.stun_host or a.host, a.stun_port)
    await stun.serve_relay(a.stun_host or a.host, int(_os.environ.get("PESSERVER_RELAY_PORT", "7600")))
    logging.info("gate tcp/%d  http tcp/%d  stun udp/%d", a.gate_port, a.http_port, a.stun_port)
    try:
        await asyncio.Event().wait()
    finally:
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
