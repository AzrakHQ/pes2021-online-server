"""pesserver command line.

  pesserver host              the downloadable program (also what it does with no arguments): runs in this
                              window, data in the user's folder, finds PES 2021 on this computer
  pesserver install           Linux: run it in the background (systemd user service), add "PES 2021 Server"
                              to the apps menu, open the admin page (run.sh does this; hostctl.py)
  pesserver open              start it if it isn't running and open the admin page (the apps-menu entry)
  pesserver selfcheck         prints "pes2021-server ok <version>" (the updater test-runs new downloads)
  pesserver                   installed: the background server's screen, live (console); else host
  pesserver stop              stop the background server
  pesserver serve ...         development (tools/pes.py restart)
  pesserver adduser NAME PW   add an account by hand
"""
import argparse, asyncio, logging, os, sys
from . import gate, httpgate, stun
from .accounts import Accounts, AccountError
from .protocol import JsonCodec
from .codec_capture import CaptureCodec
from .ncl import NclCodec

CODECS = {'ncl': NclCodec, 'json': JsonCodec, 'capture': CaptureCodec}
from .service import Service

log = logging.getLogger("pesserver")


def default_data_dir():
    """Where the downloadable program keeps players, results, certificates and the server identity."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PES2021 Server")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/PES2021 Server")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "pes2021-server")


async def _optional(what, port, coro):
    """Ports below 1024 need administrator rights on Linux: the server works without them, with less."""
    try:
        return await coro
    except OSError as e:
        log.info("%s not on port %d (%s)", what, port, e)
        return None


async def run(a):
    svc = Service(Accounts(a.db), public_ip=a.public_ip, allow_register=not a.no_register)
    from . import certs, netsetup
    data_dir = os.path.dirname(os.path.abspath(a.db))
    tls = None
    if a.cmd == "host" or os.path.exists(os.path.join(data_dir, "certs", "server.crt")):
        tls = certs.ensure(os.path.join(data_dir, "certs"))
    # 8080 speaks both HTTP and HTTPS: the mod sends the game's network test here (no admin rights needed)
    try:
        servers = [await gate.serve(svc, CODECS[a.codec], a.host, a.gate_port),
                   await httpgate.serve(svc, a.host, a.http_port, a.public_ip or "127.0.0.1", a.stun_port, a.gate_port, tls=tls)]
    except OSError as e:
        if a.cmd != "host":
            raise
        sys.exit(f"\n   The server can't start: port {a.gate_port} or {a.http_port} is already in use.\n"
                 "   Is another PES 2021 server running on this computer? Close its window first.\n")
    if not tls:
        netsetup.skip_port(443)
    if tls and a.https_port:        # 443: players set up with the /join line, and mods from before 2026-11
        s = await _optional("HTTPS for join-line players", a.https_port,
                            httpgate.serve_tls(svc, a.host, a.https_port, *tls, a.public_ip or "127.0.0.1",
                                               a.stun_port, a.gate_port))
        if s:
            servers.append(s)
            log.info("https tcp/%d", a.https_port)
        else:
            netsetup.skip_port(a.https_port)
            netsetup.NOTES.append("Port 443 is not available, so only players with the mod can join (the old join line needs it).")
    if a.join_port and a.join_port != a.http_port:      # short URLs for players: http://server/join
        s = await _optional("join page", a.join_port, httpgate.serve(svc, a.host, a.join_port))
        if s:
            servers.append(s)
            netsetup.WEB_PORT = a.join_port
        else:
            netsetup.skip_port(a.join_port)
            netsetup.WEB_PORT = a.http_port
    else:
        netsetup.WEB_PORT = a.http_port
    st = await stun.serve(a.stun_host or a.host, a.stun_port)
    await stun.serve_relay(a.stun_host or a.host, int(os.environ.get("PESSERVER_RELAY_PORT", "7600")))
    logging.info("gate tcp/%d  http tcp/%d  stun udp/%d", a.gate_port, a.http_port, a.stun_port)
    svc.admin_password = netsetup.admin_password(data_dir)
    svc.data_dir = data_dir
    httpgate.load_remote(data_dir)
    from . import identity
    identity.init(data_dir)
    logging.info("server identity %s", identity.public_hex())
    from . import hostctl
    hostctl.console_open(data_dir)

    def show_banner():
        if netsetup.STATE["ready"]:
            print(netsetup.banner(svc.admin_password, color=sys.stdout.isatty()), flush=True)
            hostctl.console(netsetup.banner(svc.admin_password, color=True), banner=True)

    if a.auto_net:
        await asyncio.to_thread(netsetup.setup, use_upnp=os.environ.get("UPNP", "1") != "0")
    else:
        netsetup.STATE["lan"] = netsetup.lan_ip()
    netsetup.STATE["ready"] = True
    from . import turn
    await turn.start(svc, data_dir, netsetup.STATE["lan"], netsetup.STATE["wan"],
                     [os.environ.get("PESSERVER_PUBLIC_ADDR", "")])
    show_banner()
    identity.start_publisher()
    if a.cmd == "host" and os.environ.get("AUTO_MOD", "1") != "0":
        from . import gamesetup                 # keep the mod up to date in PES 2021 on this computer
        gamesetup.auto_start(svc, lambda: httpgate.mod_bytes(httpgate.mod_url(svc, {}, None))
                             if os.path.isfile(httpgate.MOD_DLL) else None)
    from . import events, updates
    from . import hostctl
    how = "update it on the admin page (Server)" if hostctl.manageable()[0] else \
        "close this window and start the server again to update"
    updates.start(lambda v: events.feed(svc, f"A newer server version is out ({v}): {how}", "warn"))
    if os.environ.get("PES_CONSOLE") == "1" and not hostctl.in_service():
        print("   Keep this window open while you play. Press Ctrl+C to stop the server.\n", flush=True)
    stop = asyncio.Event()
    import signal
    loop = asyncio.get_running_loop()
    hostctl.attach(loop, stop)              # the admin page can restart, update or stop the server
    for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK"):   # SIGHUP: window closed; SIGBREAK: Windows Ctrl+Break
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):            # Windows event loop: plain handler
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await stop.wait()
    finally:
        logging.info("shutting down")
        if os.environ.get("PES_CONSOLE") == "1" and not hostctl.in_service():
            print("\n   Stopping the server...", flush=True)
        turn.stop()
        if a.auto_net:
            await asyncio.to_thread(netsetup.teardown)
        st.close()
        for s in servers:
            s.close()


def _host_setup(a):
    """The downloadable program: settings for this computer."""
    os.makedirs(a.data, exist_ok=True)
    a.db = os.path.join(a.data, "pesserver.db")
    a.host, a.codec, a.no_register, a.stun_host = "0.0.0.0", "ncl", False, None
    a.public_ip = os.environ.get("PUBLIC_IP") or None
    a.auto_net = not a.public_ip
    env = os.environ
    env.setdefault("PES_CONSOLE", "1")
    env["PESSERVER_HTTP_PORT"] = str(a.http_port)
    env["PESSERVER_GATE_PORT"] = str(a.gate_port)
    env["PESSERVER_CERTS"] = os.path.join(a.data, "certs")
    if a.public_ip:
        env["PESSERVER_PUBLIC_ADDR"] = a.public_ip
    from . import gamesetup
    gamesetup.use_local_games()
    if sys.platform == "win32":
        os.system("")                     # turn on colours in the Windows console
        try:
            os.system("title PES 2021 Server")
        except Exception:  # noqa: BLE001
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    p = argparse.ArgumentParser(prog="pesserver")
    p.add_argument("--db", default="pesserver.db")
    sub = p.add_subparsers(dest="cmd")
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
    h = sub.add_parser("host", help="run the server for players on this computer (the default)")
    h.add_argument("--data", default=default_data_dir(), help="where players and results are kept")
    for flag, port in (("--gate-port", 10000), ("--http-port", 8080), ("--https-port", 443), ("--join-port", 80),
                       ("--stun-port", 3478)):
        h.add_argument(flag, type=int, default=int(os.environ.get("PES_" + flag[2:].upper().replace("-", "_"), port)))
    u = sub.add_parser("adduser"); u.add_argument("name"); u.add_argument("password"); u.add_argument("--steam-id")
    i = sub.add_parser("install", help="Linux: run the server in the background, add it to the apps menu")
    i.add_argument("--data", default=default_data_dir()); i.add_argument("--no-autostart", action="store_true")
    o = sub.add_parser("open", help="start the server if needed and open its admin page")
    o.add_argument("--data", default=default_data_dir())
    sub.add_parser("selfcheck")
    st = sub.add_parser("stop", help="stop the server running in the background")
    cn = sub.add_parser("console", help="show the background server's screen live (Ctrl+C closes the view)")
    cn.add_argument("--data", default=default_data_dir()); cn.add_argument("--no-follow", action="store_true")
    argv = sys.argv[1:]
    from . import hostctl
    if not argv and hostctl.installed():
        argv = ["console"]                    # installed (Linux service): show the background server's screen
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--db")):
        argv = ["host"] + argv                # the program's own options, e.g. --data DIR
    a = p.parse_args(argv)
    if a.cmd is None:
        p.error("choose a command: host, serve or adduser")
    from . import hostctl
    if a.cmd in ("install", "open", "selfcheck", "stop", "console"):
        return {"install": hostctl.cmd_install, "open": hostctl.cmd_open, "stop": hostctl.cmd_stop,
                "console": hostctl.cmd_console}.get(a.cmd, lambda _: hostctl.cmd_selfcheck())(a)
    if a.cmd == "host":
        _host_setup(a)
    fmt = "%(asctime)s %(name)s %(message)s"
    if os.environ.get("PES_CONSOLE") == "1" and a.cmd in ("serve", "host"):
        # In a window: it shows the banner and the activity feed; the detailed log goes to a file
        # next to the database (warnings show in the window too).
        from logging.handlers import RotatingFileHandler
        f = RotatingFileHandler(os.path.join(os.path.dirname(os.path.abspath(a.db)), "server.log"),
                                maxBytes=10 << 20, backupCount=2, encoding="utf-8")
        f.setFormatter(logging.Formatter(fmt))
        c = logging.StreamHandler()
        c.setLevel(logging.WARNING)
        c.setFormatter(logging.Formatter("   %(message)s"))
        from .hostctl import ConsoleLog
        w = ConsoleLog(); w.setLevel(logging.WARNING)              # a server in the background: its screen file
        logging.basicConfig(level=logging.INFO, handlers=[f, c, w])
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)
    if a.cmd == "adduser":
        try:
            print("created user id", Accounts(a.db).register(a.name, a.password, a.steam_id))
        except AccountError as e:
            sys.exit(f"error: {e}")
    else:
        try:
            asyncio.run(run(a))
        except KeyboardInterrupt:
            pass
        hostctl.after_shutdown()            # restart into a new version, or hand over to the background service


if __name__ == "__main__":
    main()
