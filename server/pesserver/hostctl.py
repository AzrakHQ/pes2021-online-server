"""The host's server program on Linux: install it once, then update, restart and stop it from the admin page.

- `pes2021-server install` (run.sh runs it after downloading): a `systemd --user` service, so the server runs
  in the background with no window, a "PES 2021 Server" entry in the apps menu (`pes2021-server open`: start
  it if needed and open the admin page), and `~/.local/bin/pes2021-server`.
- Update from the admin page: the newest release is downloaded next to the program, checked against the
  release's SHA256SUMS and test-run (`pes2021-server selfcheck`), swapped in, and the server restarts into it
  (same process id: os.execv after a clean shutdown). "When nobody's playing" waits for running matches to end.
  The previous program is kept until the new one has come up; if it doesn't (twice), the old one is put back.
- Restart, stop, "start when I log in" (the service enabled or not) and "move to the background" (a server
  started in a terminal hands over to the service).

Only for the downloadable program (PyInstaller) on Linux; run from source, all of this reports why it can't.
"""
import hashlib, json, logging, os, platform, shutil, subprocess, sys, threading, time, urllib.request

log = logging.getLogger("pesserver.hostctl")

REPO = "AzrakHQ/pes2021-online-server"
REL = f"https://github.com/{REPO}/releases/latest/download"


def _rel():
    return os.environ.get("PESSERVER_RELEASE_URL") or REL      # (tests: a local copy of a release)
UNIT_NAME = "pes2021-server.service"
APP_ID = "pes2021-server"

RESTART_EXIT = 75                 # the service's "restart me" exit code
CANT_START_EXIT = 78              # a port is taken: trying again every 2 seconds would not help (the unit stops)
TRIES = 2                         # starts a newly installed version gets to come up before the old one is put back
IDLE_POLL = 10                    # seconds between "is a match still on?" checks while an update waits
STATE = {"update": "idle", "error": "", "progress": 0, "target": "", "when": "", "since": 0.0}
_CTL = {"loop": None, "stop": None, "after": None, "cancel": threading.Event(), "wake": threading.Event(), "thread": None}
_UPDATE_LOCK = threading.Lock()   # two "Update now" clicks at once start one update


# ---- where things are -------------------------------------------------------------------------------------
def program():
    """The running program file, or None when this isn't the downloadable program."""
    return os.path.realpath(sys.executable) if getattr(sys, "frozen", False) else None


def asset():
    m = platform.machine().lower()
    return {"x86_64": "pes2021-server-linux-x64", "amd64": "pes2021-server-linux-x64",
            "aarch64": "pes2021-server-linux-arm64", "arm64": "pes2021-server-linux-arm64"}.get(m)


def manageable():
    """(True, "") when this program can update and control itself, else (False, the reason in plain words)."""
    p = program()
    if not p:
        return False, "This server runs from source code, so update it with git."
    if not asset():
        return False, f"There's no ready-made server program for this computer ({platform.machine()})."
    if not os.access(os.path.dirname(p), os.W_OK):
        return False, f"The program's folder ({os.path.dirname(p)}) can't be written to."
    return True, ""


def _config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def unit_path():
    return os.path.join(_config_home(), "systemd", "user", UNIT_NAME)


def in_service():
    """Started by the systemd user service (the unit sets PES_SERVICE=1)."""
    return os.environ.get("PES_SERVICE") == "1"


def clean_env():
    """The environment for programs this one starts. The packaged program points LD_LIBRARY_PATH at its own
    bundled libraries (which can break systemctl or xdg-open) and leaves PyInstaller's own variables, which
    would make a restarted or test-run copy reuse this copy's unpacked files."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k not in ("_MEIPASS2", "LD_LIBRARY_PATH")}
    if os.environ.get("LD_LIBRARY_PATH_ORIG"):
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
    env.pop("LD_LIBRARY_PATH_ORIG", None)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _systemctl(*args, timeout=15):
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout, env=clean_env())
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def installed():
    """Set up by `install` (Linux, the service exists): then `pes2021-server` alone shows its screen."""
    return bool(program()) and os.path.exists(unit_path()) and not in_service()


_SEEN = {"systemd": None, "autostart": (0.0, None)}     # answers kept: each one costs a systemctl process


def systemd_ok():
    if not _SEEN["systemd"]:                             # (a "yes" stays true for as long as this process lives)
        _SEEN["systemd"] = shutil.which("systemctl") is not None and _systemctl("show-environment")[0] == 0
    return _SEEN["systemd"]


def autostart(fresh=False):
    """True / False, or None when there is no systemd user session (then it can't be set)."""
    if not systemd_ok():
        return None
    t, on = _SEEN["autostart"]
    if fresh or time.monotonic() - t > 30:
        on = os.path.exists(unit_path()) and _systemctl("is-enabled", UNIT_NAME)[1].splitlines()[:1] == ["enabled"]
        _SEEN["autostart"] = (time.monotonic(), on)
    return on


# ---- the service and the apps-menu entry --------------------------------------------------------------------
KEPT = ("PUBLIC_IP", "UPNP", "TURN", "UPDATE_CHECK", "PESSERVER_NO_IPCHECK", "AUTO_MOD", "REPLAYS")
# ^ settings given on the command line (UPNP=0 ... | sh) that the background service must keep (server/README.md)


def env_path(data):
    return os.path.join(data, "server.env")


def keep_settings(data):
    """Save the settings this process was started with for the service (<data>/server.env, read by the unit).
    A setting given now replaces the saved one; the others stay. ADMIN_PASSWORD goes where the server reads its
    password from, not into a file other programs list."""
    ok = lambda v: bool(v) and all(c.isalnum() or c in ".:-_" for c in v)
    kept = {}
    try:
        for line in open(env_path(data), encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k in KEPT and ok(v):
                kept[k] = v
    except OSError:
        pass
    kept.update({k: os.environ[k].strip() for k in KEPT if ok((os.environ.get(k) or "").strip())})
    try:
        os.makedirs(data, exist_ok=True)
        tmp = env_path(data) + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write("".join(f"{k}={v}\n" for k, v in sorted(kept.items())))
        os.replace(tmp, env_path(data))
        from . import netsetup
        pw = os.environ.get("ADMIN_PASSWORD") or ""
        if len(pw) >= 10 and pw not in netsetup.EXAMPLE_PASSWORDS and "\n" not in pw:
            tmp = os.path.join(data, "admin_password.tmp")
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
                f.write(pw + "\n")
            os.replace(tmp, os.path.join(data, "admin_password"))
    except OSError as e:
        log.warning("couldn't save the server's settings for the service: %s", e)
    return kept


def unit_text(prog, data):
    pub = (os.environ.get("PUBLIC_IP") or "").strip()     # e.g. the host's Tailscale address (README, Problems)
    extra = f"\nEnvironment=PUBLIC_IP={pub}" if pub and all(c.isalnum() or c in ".:-" for c in pub) else ""
    extra += f"\nEnvironmentFile=-{env_path(data)}"       # (after Environment=: a saved setting wins)
    return f"""[Unit]
Description=PES 2021 private server
Documentation=https://github.com/{REPO}
After=network-online.target
Wants=network-online.target

[Service]
ExecStart="{prog}" host --data "{data}"
WorkingDirectory={data}
Environment=PES_SERVICE=1 PES_CONSOLE=1{extra}
Restart=on-failure
RestartSec=2
# exit code 75 = "restart me" (an update or Restart on the admin page): systemd starts a fresh copy
SuccessExitStatus=75
RestartForceExitStatus=75
# exit code 78 = a port it needs is taken by another program: starting again would fail the same way
RestartPreventExitStatus=78

[Install]
WantedBy=default.target
"""


def desktop_text(prog):
    return f"""[Desktop Entry]
Type=Application
Name=PES 2021 Server
GenericName=Game server
Comment=Your PES 2021 server for friends: opens its admin page (and starts it if it isn't running)
Exec="{prog}" open
Terminal=false
Categories=Game;Network;
Keywords=pes;efootball;football;server;
"""


def write_unit(prog, data):
    os.makedirs(os.path.dirname(unit_path()), exist_ok=True)
    keep_settings(data)
    with open(unit_path(), "w") as f:
        f.write(unit_text(prog, data))
    _systemctl("daemon-reload")


def refresh_unit(data):
    """A server that updates itself from the admin page keeps the unit file its first install wrote. Bring it up to
    date (it takes effect at the next start). Only for the running service; returns whether it changed."""
    prog = program()
    if not (prog and in_service() and os.path.exists(unit_path())):
        return False
    want = unit_text(prog, _unit_data() or data)
    try:
        with open(unit_path()) as f:
            if f.read() == want:
                return False
        with open(unit_path() + ".tmp", "w") as f:
            f.write(want)
        os.replace(unit_path() + ".tmp", unit_path())
    except OSError:
        return False
    _systemctl("daemon-reload")
    log.info("the service's unit file was brought up to date")
    return True


def write_menu_entry(prog):
    apps = os.path.join(_data_home(), "applications")
    os.makedirs(apps, exist_ok=True)
    with open(os.path.join(apps, APP_ID + ".desktop"), "w") as f:
        f.write(desktop_text(prog))
    bindir = os.path.expanduser("~/.local/bin")
    try:
        os.makedirs(bindir, exist_ok=True)
        link = os.path.join(bindir, APP_ID)
        if os.path.islink(link) or not os.path.exists(link):
            if os.path.islink(link):
                os.remove(link)
            os.symlink(prog, link)
    except OSError as e:
        log.info("no ~/.local/bin link: %s", e)


def set_autostart(on, prog=None, data=None):
    if not systemd_ok():
        raise RuntimeError("This computer has no systemd user session, so the server can't start by itself.")
    prog = prog or program()
    if on:
        if not prog:
            raise RuntimeError("This server runs from source code; install the program to use this.")
        write_unit(prog, data or _data_dir())
        code, out = _systemctl("enable", UNIT_NAME)
    else:
        code, out = (0, "") if not os.path.exists(unit_path()) else _systemctl("disable", UNIT_NAME)
    if code != 0:
        raise RuntimeError(out or "systemctl failed")
    return autostart(fresh=True)


def _data_dir():
    from .__main__ import default_data_dir
    return default_data_dir()


# ---- stopping and restarting the running server ---------------------------------------------------------------
def attach(loop, stop_event):
    """__main__.run(): how to end the server from here."""
    _CTL["loop"], _CTL["stop"] = loop, stop_event


def _end(after):
    if _CTL["stop"] is None:
        raise RuntimeError("the server isn't running")
    _CTL["after"] = after
    log.warning("the server is %s", {"restart": "restarting", "stop": "stopping", "background": "moving to the background"}[after])
    _CTL["loop"].call_soon_threadsafe(_CTL["stop"].set)


def after_shutdown():
    """__main__.main() after the server closed its ports: restart, hand over to the service, or just exit."""
    after = _CTL["after"]
    prog = program()
    if after == "restart" and prog:
        sys.stdout.flush(); sys.stderr.flush()
        if in_service():
            sys.exit(RESTART_EXIT)              # systemd starts a fresh copy (RestartForceExitStatus=75)
        # In a terminal: become the new program in place (same window, same process id). PyInstaller's one-file
        # programs then keep the old copy's unpacked files in /tmp until the server stops; the service avoids it.
        os.execve(prog, [prog] + sys.argv[1:], clean_env())
    if after == "background":
        code, out = _systemctl("start", UNIT_NAME)
        print("\n   The server now runs in the background. You can close this window.\n" if code == 0
              else f"\n   Couldn't start the background service: {out}\n", flush=True)


def restart(svc):
    if not program():
        raise RuntimeError("This server runs from source code; restart it the way you started it.")
    _end("restart")


def stop(svc):
    _end("stop")


def to_background(svc):
    if in_service():
        raise RuntimeError("It already runs in the background.")
    if not program():
        raise RuntimeError("This server runs from source code; install the program to use this.")
    if not systemd_ok():
        raise RuntimeError("This computer has no systemd user session, so the server can't run in the background.")
    write_unit(program(), getattr(svc, "data_dir", None) or _data_dir())
    _end("background")


# ---- updates ---------------------------------------------------------------------------------------------------
def current():
    from . import VERSION
    return VERSION


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "pes2021-online-server"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest_version():
    with _get(f"{_rel()}/version.txt") as r:
        return r.read(200).decode("utf-8", "replace").splitlines()[0].strip()


def _expected_sha(name):
    with _get(f"{_rel()}/SHA256SUMS") as r:
        for line in r.read(1 << 16).decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                return parts[0].lower()
    raise RuntimeError("the release has no checksum for this program")


def download(cancel=None, want=None):
    """The newest program next to this one (<program>.new), checked and test-run. Returns (path, version)."""
    prog, name = program(), asset()
    want = want or latest_version()
    sha = _expected_sha(name)
    new = prog + ".new"
    h = hashlib.sha256()
    with _get(f"{_rel()}/{name}", timeout=60) as r, open(new, "wb") as f:
        total, got = int(r.headers.get("Content-Length") or 0), 0
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk); h.update(chunk); got += len(chunk)
            STATE["progress"] = int(100 * got / total) if total else 0
    if h.hexdigest() != sha:
        raise RuntimeError("the download didn't match the release's checksum (a newer release may be on its way; try again in a few minutes)")
    os.chmod(new, 0o755)
    try:
        out = subprocess.run([new, "selfcheck"], capture_output=True, text=True, timeout=120, env=clean_env()).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"the new program didn't start ({e})")
    if "pes2021-server ok" not in out:
        raise RuntimeError("the new program didn't pass its self-check")
    return new, want                            # (update() removes the file after any failure)


def busy(svc):
    """Is a match being played (or about to start)?"""
    from . import events
    # a copy: the update thread asks while the server's own loop adds and ends matches
    return any(m.get("phase") != "Full time" for m in list(events._st(svc)["live"].values()))


def _note_version(prog, version):
    try:
        with open(os.path.join(os.path.dirname(prog), "program-version.txt"), "w") as f:
            f.write(version + "\n")
    except OSError:
        pass


def _swap(new, version):
    """Put the new program in place. The running one is kept as <program>.old until the new one has come up
    (trial_begin / trial_passed)."""
    prog = program()
    old = prog + ".old"
    try:
        if os.path.lexists(old):
            os.remove(old)
        try:
            os.link(prog, old)
        except OSError:
            shutil.copy2(prog, old)
        with open(prog + ".trying", "w") as f:
            json.dump({"version": version, "from": current(), "starts": 0}, f)
    except OSError as e:                        # (no way back then, as before; the update itself goes on)
        log.warning("the current program couldn't be kept as a way back: %s", e)
    os.replace(new, prog)
    _note_version(prog, version)


# ---- a new version that doesn't come up is undone ----------------------------------------------------------------
def _trial(prog):
    try:
        with open(prog + ".trying") as f:
            t = json.load(f)
        return t if isinstance(t, dict) else None
    except (OSError, ValueError):
        return None


def _save_trial(prog, t):
    try:
        with open(prog + ".trying.tmp", "w") as f:
            json.dump(t, f)
        os.replace(prog + ".trying.tmp", prog + ".trying")
    except OSError:
        pass


def trial_begin():
    """The first thing a start of the server does. After an update the new version gets TRIES starts to come up;
    when it has failed them, the previous program is put back and started in its place."""
    prog = program()
    t = _trial(prog) if prog else None
    if t is None:
        return
    t["starts"] = int(t.get("starts") or 0) + 1
    old = prog + ".old"
    if t["starts"] <= TRIES or not os.path.exists(old):
        return _save_trial(prog, t)
    try:
        os.replace(old, prog)
        os.remove(prog + ".trying")
        with open(prog + ".undone", "w") as f:
            json.dump({"version": t.get("version") or "", "back_to": t.get("from") or ""}, f)
    except OSError:
        return
    _note_version(prog, str(t.get("from") or ""))
    print(f"\n   The update to {t.get('version')} didn't start on this computer. Going back to {t.get('from')}.\n", flush=True)
    sys.stdout.flush(); sys.stderr.flush()
    if in_service():
        sys.exit(RESTART_EXIT)                  # systemd starts the program that is in place now
    os.execve(prog, [prog] + sys.argv[1:], clean_env())


def trial_uncount():
    """This start failed for a reason that isn't the new version's (a port taken by another program)."""
    prog = program()
    t = _trial(prog) if prog else None
    if t is not None and int(t.get("starts") or 0) > 0:
        t["starts"] -= 1
        _save_trial(prog, t)


def trial_passed(svc=None):
    """The server is up: the previous program isn't needed any more. Says so if an update was undone."""
    prog = program()
    if not prog:
        return
    for ext in (".trying", ".old"):
        try:
            os.remove(prog + ext)
        except OSError:
            pass
    try:
        with open(prog + ".undone") as f:
            u = json.load(f)
        os.remove(prog + ".undone")
    except (OSError, ValueError):
        return
    if svc is not None and isinstance(u, dict):
        from . import events
        events.feed(svc, f"The update to {u.get('version')} didn't start on this computer, so the server went back to "
                         f"{u.get('back_to') or 'the version before'}", "warn")


def update(svc, when="now"):
    """Start an update in the background ("now" or "idle"); "cancel" stops a waiting or running one."""
    if when == "cancel":
        _CTL["cancel"].set(); _CTL["wake"].set()
        return STATE
    ok, why = manageable()
    if not ok:
        raise RuntimeError(why)
    with _UPDATE_LOCK:
        return _start_update(svc, when)


def _start_update(svc, when):
    from . import events
    old = _CTL["thread"]
    if _CTL["cancel"].is_set() and old is not None and old.is_alive():
        old.join(10)                            # "Cancel" then "Update now": let the cancelled one finish first
    if STATE["update"] in ("downloading", "waiting", "restarting"):
        STATE["when"] = when
        if when == "now":
            _CTL["wake"].set()                  # "Update now anyway" while it waits for a match to end
        return STATE
    _CTL["cancel"] = cancel = threading.Event()
    _CTL["wake"] = wake = threading.Event()
    STATE.update(update="downloading", error="", progress=0, when=when, since=time.time(), target="")

    def drop_download():
        try:
            os.remove(program() + ".new")
        except OSError:
            pass

    def work():
        from . import updates
        try:
            want = latest_version()
            if not updates.newer(want, current()):      # nothing newer published (yet): say so, drop the notice
                STATE.update(update="idle", error="", target=current())
                updates.settle()
                events.feed(svc, "The server is already the newest version", "info")
                return
            new, version = download(cancel, want)
            STATE["target"] = version
            if STATE["when"] == "idle" and busy(svc):
                STATE["update"] = "waiting"
                events.feed(svc, f"The update to {version} waits until nobody is playing", "info")
                while STATE["when"] == "idle" and busy(svc):
                    wake.wait(IDLE_POLL); wake.clear()
                    if cancel.is_set():
                        raise InterruptedError
            if cancel.is_set():
                raise InterruptedError
            _swap(new, version)
            STATE["update"] = "restarting"
            events.feed(svc, f"Updating the server to {version}: it restarts now", "warn")
            time.sleep(1)                       # let the admin page see "restarting"
            _end("restart")
        except InterruptedError:
            drop_download()
            STATE.update(update="idle", error="")
            events.feed(svc, "The server update was cancelled", "info")
        except Exception as e:  # noqa: BLE001  (offline, GitHub busy, disk full: say so on the admin page)
            log.warning("update failed: %s", e)
            drop_download()                     # (half a download, or one that failed its checks)
            STATE.update(update="error", error=str(e))

    _CTL["thread"] = threading.Thread(target=work, name="self-update", daemon=True)
    _CTL["thread"].start()
    return STATE


# ---- the admin page ---------------------------------------------------------------------------------------------
def log_tail(svc, n=40):
    path = os.path.join(getattr(svc, "data_dir", "") or "", "server.log")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []
    return [ln[:400] for ln in lines]


def status(svc):
    from . import updates
    ok, why = manageable()
    return {"version": current(), "latest": updates.STATE.get("latest") or "", "update_available": bool(updates.STATE.get("available")),
            "checked": updates.STATE.get("checked") or 0,
            "can_update": ok, "why_not": why, "update": dict(STATE), "busy": busy(svc),
            "systemd": systemd_ok(), "autostart": autostart(),
            "in_service": in_service(), "program": program() or "", "log": log_tail(svc)}


def admin_api(svc, route, method, body):
    """GET /admin/api/program; POST /admin/api/program/update {when: now|idle|cancel}, /restart, /stop,
    /autostart {on}, /background. Returns (status, json-able body)."""
    if route == "/admin/api/program" and method == "GET":
        return "200 OK", status(svc)
    if method != "POST":
        return "405 Method Not Allowed", {"error": "method"}
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        return "400 Bad Request", {"error": "bad request"}
    try:
        if route == "/admin/api/program/update":
            when = req.get("when", "now")
            if when not in ("now", "idle", "cancel"):
                raise ValueError("when: now, idle or cancel")
            update(svc, when)
        elif route == "/admin/api/program/restart":
            restart(svc)
        elif route == "/admin/api/program/stop":
            stop(svc)
        elif route == "/admin/api/program/autostart":
            set_autostart(bool(req.get("on")), data=getattr(svc, "data_dir", None))
        elif route == "/admin/api/program/background":
            to_background(svc)
        else:
            return "404 Not Found", {"error": "no such route"}
    except ValueError as e:
        return "400 Bad Request", {"error": str(e)}
    except RuntimeError as e:
        return "409 Conflict", {"error": str(e)}
    return "200 OK", status(svc)


# ---- the terminal screen of a server in the background ------------------------------------------------------
# A server started by the service has no window, so it also writes what the window used to show (the banner,
# the activity lines, warnings) to <data>/console.log, in colour. `pes2021-server` shows that file live.
CONSOLE = {"path": None, "banner": ""}
CONSOLE_MAX = 2 << 20


def console_open(data_dir):
    if not in_service():
        return
    CONSOLE["path"] = os.path.join(data_dir, "console.log")
    try:                                                 # a new run starts a new screen (a new file, so a
        open(CONSOLE["path"] + ".new", "w").close()      # live view notices even if it grows back quickly)
        os.replace(CONSOLE["path"] + ".new", CONSOLE["path"])
    except OSError:
        CONSOLE["path"] = None


def console(text, banner=False):
    """Add text (with colours) to the background server's screen."""
    path = CONSOLE["path"]
    if not path:
        return
    if banner:
        CONSOLE["banner"] = text
    try:
        if os.path.getsize(path) > CONSOLE_MAX:          # keep the banner and the newest lines
            with open(path, encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-300:]
            with open(path, "w", encoding="utf-8") as f:
                f.write(CONSOLE["banner"] + "\n" + "\n".join(tail) + "\n")
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


class ConsoleLog(logging.Handler):
    """Warnings on the background server's screen, as the window showed them."""
    def emit(self, record):
        try:
            console("   " + record.getMessage())
        except Exception:  # noqa: BLE001
            pass


def _strip(t):
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", t)


def _unit_data():
    """The data folder the installed service runs with (install --data DIR), or None."""
    import re
    try:
        with open(unit_path()) as f:
            m = re.search(r'^ExecStart=.* --data "([^"]*)"', f.read(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def cmd_console(args):
    """`pes2021-server` in a terminal: start the background server if needed and show its screen live.
    Ctrl+C closes the view; the server keeps running."""
    if not (os.path.exists(unit_path()) and systemd_ok()):
        os.execve(program() or sys.executable, ([program()] if program() else [sys.executable, "-m", "pesserver"]) +
                  ["host", "--data", args.data], clean_env())      # not installed: run in this window, as before
    data = _unit_data() or args.data                # the service's screen is in the folder it runs with
    if _systemctl("is-active", UNIT_NAME)[1].splitlines()[:1] != ["active"]:
        if _answering():
            sys.exit("   A PES 2021 server is already running in another window (not in the background).")
        print("   Starting the PES 2021 server in the background...", flush=True)
        _systemctl("start", UNIT_NAME)
    path = os.path.join(data, "console.log")
    end = time.time() + 60
    while time.time() < end and not (os.path.exists(path) and os.path.getsize(path) > 0 and _answering()):
        time.sleep(0.5)
    tty = sys.stdout.isatty()
    show = (lambda t: sys.stdout.write(t)) if tty else (lambda t: sys.stdout.write(_strip(t)))
    pos, ino = 0, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            ino = os.fstat(f.fileno()).st_ino
            show(f.read()); pos = f.tell()
    except OSError:
        print("   The server is running, but its screen isn't there yet. Admin page: http://localhost:%d/admin" % _port())
    hint = "   Ctrl+C closes this view; the server keeps running. Stop it: pes2021-server stop"
    show(("\x1b[2m" + hint + "\x1b[0m\n") if tty else hint + "\n"); sys.stdout.flush()
    if not tty or getattr(args, "no_follow", False):
        return
    try:
        while True:
            time.sleep(0.5)
            try:
                st = os.stat(path)
            except OSError:
                continue
            size = st.st_size
            if st.st_ino != ino or size < pos:                 # the server restarted: a new screen
                ino, pos = st.st_ino, 0
                show("\n")
            if size > pos:
                with open(path, encoding="utf-8", errors="replace") as f:
                    f.seek(pos); show(f.read()); pos = f.tell()
                sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n   Closed the view. The server keeps running in the background (stop it: pes2021-server stop).\n", flush=True)


def cmd_stop(args):
    if os.path.exists(unit_path()) and systemd_ok() and _systemctl("is-active", UNIT_NAME)[1].splitlines()[:1] == ["active"]:
        code, out = _systemctl("stop", UNIT_NAME, timeout=60)
        print("   The PES 2021 server is stopped. Start it again: pes2021-server (or PES 2021 Server in your apps menu)."
              if code == 0 else f"   Couldn't stop it: {out}", flush=True)
        return
    if _answering():
        sys.exit("   The server runs in a terminal window: press Ctrl+C there (or stop it on the admin page, Server).")
    print("   The PES 2021 server isn't running.", flush=True)


# ---- command line: install, open, selfcheck ------------------------------------------------------------------------
def _port():
    """The web port of the server on this computer (PES_HTTP_PORT, as `host` reads it)."""
    try:
        return int(os.environ.get("PES_HTTP_PORT") or 8080)
    except ValueError:
        return 8080


def _answering(port=None, timeout=1.5):
    port = port or _port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def _open_page(port=None):
    port = port or _port()
    url = f"http://localhost:{port}/admin"
    opener = shutil.which("xdg-open")
    if opener and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         env=clean_env())
        return url, True
    return url, False


def _wait_up(port=None, secs=60):
    end = time.time() + secs
    while time.time() < end:
        if _answering(port):
            return True
        time.sleep(1)
    return False


def cmd_install(args):
    """run.sh: set up the service and the apps-menu entry, (re)start the server in the background, open the page."""
    prog, data = program(), args.data
    if not prog:
        sys.exit("   install works for the downloadable program.")
    os.makedirs(data, exist_ok=True)
    write_menu_entry(prog)
    if not systemd_ok():
        print("   This computer has no systemd user session, so the server runs in this window instead.", flush=True)
        os.execve(prog, [prog, "host", "--data", data], clean_env())
    running = _systemctl("is-active", UNIT_NAME)[1].splitlines()[:1] == ["active"]
    if not running and _answering():
        sys.exit("\n   A PES 2021 server is already running in another window. Close it (Ctrl+C in that window),\n"
                 "   then run this line again. Players, results and friends' mods are kept.\n")
    write_unit(prog, data)
    if not args.no_autostart:
        _systemctl("enable", UNIT_NAME)
    code, out = _systemctl("restart" if running else "start", UNIT_NAME)
    if code != 0:
        sys.exit(f"   Couldn't start the server's service: {out}\n   Run it in this window instead: {prog} host")
    print("   Starting the PES 2021 server in the background...", flush=True)
    if not _wait_up():
        sys.exit("   The server didn't answer within a minute. Its log: journalctl --user -u pes2021-server -e")
    url, opened = _open_page()
    print(f"""
   Installed. The server runs in the background{" and starts by itself when you log in" if not args.no_autostart else ""}.
   The admin page {"is open in your browser" if opened else f"is at {url}"}: inviting friends, updating and stopping are there.
   Later: run pes2021-server to see this screen again, or open "PES 2021 Server" from your apps menu.""", flush=True)
    if stops_at_logout():
        print("\n   On this computer the server stops when you log out. To keep it running: loginctl enable-linger", flush=True)
    cmd_console(args)


def stops_at_logout():
    """Installed over SSH or on a machine without a desktop: the user's services end with the last login session,
    unless "lingering" is on for the user. (On a desktop the server is meant to run while you're logged in.)"""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        import getpass
        r = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
                           capture_output=True, text=True, timeout=10, env=clean_env())
    except (OSError, subprocess.SubprocessError, KeyError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "no"


def cmd_open(args):
    """The apps-menu entry: start the server if it isn't running, then open its admin page."""
    if not _answering():
        if os.path.exists(unit_path()) and systemd_ok():
            _systemctl("start", UNIT_NAME)
        else:
            prog = program() or sys.executable
            argv = [prog, "host", "--data", args.data] if program() else [sys.executable, "-m", "pesserver", "host", "--data", args.data]
            os.makedirs(args.data, exist_ok=True)
            with open(os.path.join(args.data, "server.out"), "ab") as out:
                subprocess.Popen(argv, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True,
                                 env={**clean_env(), "PES_CONSOLE": "1"})
        if not _wait_up():
            sys.exit("The server didn't start. Its log is server.log in " + args.data)
    url, opened = _open_page()
    if not opened:
        print(f"Admin page: {url}")


def cmd_selfcheck():
    """`pes2021-server selfcheck`: the updater test-runs a new download with this before swapping it in."""
    from . import VERSION, httpgate, service, gate, pescmds, pesnames  # noqa: F401  (the bundle is complete)
    print(f"pes2021-server ok {VERSION}", flush=True)
