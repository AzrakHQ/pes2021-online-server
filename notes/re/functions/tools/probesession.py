#!/usr/bin/env python3
"""probesession.py — one command for a probe test session, and one to undo it.

  probesession.py up [1|2|both] [--no-login]   build the probe DLL (if stale), start the test server with
                                                it, install a stamped copy into the game copy/copies, launch,
                                                wait for the probe port, log in (keys, for now)
  probesession.py down                          close the games, stop the test server, restore the release
                                                DLL and logs/server.log, delete test Live Update saves
  probesession.py restart                       restart the test server (new code) and log the games in again
  probesession.py status                        what is running / installed

Runs from a repo checkout inside the game folder (<game folder>/pes2021-private-server or a wt-* worktree): the
probe DLL and the test server (testserver/run_server.py) come from this checkout; the games, logs/ and
tools/pes.py (keys, relaunch) from the game folder (PES_ROOT, default: the checkout's parent folder).
Probe ports: copy 1 = 27801, copy 2 = 27802. Test server log: logs/server-probe.out (logs/server.log is a
symlink to it during the session, so `pes.py flow login` works). State: logs/probesession.json.
Reply patches (testserver/replypatch.py): logs/patch.json; every reply: logs/replies.jsonl.
SERVER_ENV="COOP=1 LOBBYTEST=1 ..." extra env for the test server (run_server.py add-ons).
Never ship the probe DLL. `down` always puts the release DLL back.
"""
import glob, hashlib, json, os, shutil, socket, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
ROOT = os.environ.get("PES_ROOT") or os.path.dirname(REPO)
TOOLS = os.path.join(ROOT, "tools")
LOGS = os.path.join(ROOT, "logs")
STATE = os.path.join(LOGS, "probesession.json")
PATCH = os.path.join(LOGS, "patch.json")
REPLIES = os.path.join(LOGS, "replies.jsonl")
SERVER_OUT = os.path.join(LOGS, "server-probe.out")
SERVER_LOG = os.path.join(LOGS, "server.log")
BACKUP = os.path.join(TOOLS, "dinput8-release-backup.dll")
PROBE_DLL = os.path.join(TOOLS, "dinput8-probe.dll")          # unstamped build
STAMPED = os.path.join(TOOLS, "dinput8-probe-stamped.dll")
WINESERVER = os.path.expanduser("~/.steam/steam/steamapps/common/Proton 10.0/files/bin/wineserver")
SAVE_REL = "drive_c/ProgramData/KONAMI/eFootball PES 2021 SEASON UPDATE/save/LIVE00000000"
ENV = {**os.environ, "DISPLAY": ":0"}

# Side by side with the host's own server (always on unless PROBE_ALT=0): the test server gets its own ports
# and its own data folder (logs/../testdata: its own players, identity and admin password), and netshim (the dev
# launcher's preload) moves the game's fixed ports there. Only UDP sendto() is not moved: the game's STUN probe
# still reaches port 3478 (whoever answers it only tells the game its own address).
ALT = os.environ.get("PROBE_ALT", "1") != "0"
# (18080/18090 are used by the admin-page work's own test servers)
GATE, HTTP = (30000, 28080) if ALT else (10000, 8080)
ALT_PORTS = {"PES_GATE_PORT": 30000, "PES_HTTP_PORT": 28080, "PES_HTTPS_PORT": 28443, "PES_JOIN_PORT": 28081,
             "PES_STUN_PORT": 23478, "TURN_TLS_PORT": 25349, "TURN_PORT": 23479, "TURN_DTLS_PORT": 25350,
             "TURN_MIN_PORT": 59160, "TURN_MAX_PORT": 59179, "PESSERVER_RELAY_PORT": 27600}
PORTMAP = "80:28080,443:28443,8080:28080,8443:28443,10000:30000,5349:25349,3479:23479,7600:27600"
TESTDATA = os.path.join(ROOT, "testdata")


def game(inst):
    return os.path.join(ROOT, "game" if inst == 1 else f"game{inst}")


def port(inst):
    return 27800 + inst


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def repo():
    if not os.path.exists(os.path.join(REPO, "server", "mod", "probe.c")):
        sys.exit(f"{REPO} has no server/mod/probe.c")
    return REPO


def load():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def save(st):
    json.dump(st, open(STATE, "w"), indent=1)


def listening(p, host="127.0.0.1"):
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect((host, p)); return True
    except OSError:
        return False
    finally:
        s.close()


def build(rp):
    mod = os.path.join(rp, "server", "mod")
    srcs = [f for f in glob.glob(os.path.join(mod, "**", "*.[ch]"), recursive=True)] + [os.path.join(mod, "build.sh")]
    if os.path.exists(PROBE_DLL) and os.path.getmtime(PROBE_DLL) > max(map(os.path.getmtime, srcs)):
        return
    env = {**ENV, "PATH": os.path.join(TOOLS, "zig-mingw", "bin") + ":" + ENV["PATH"], "PROBE": "1"}
    r = subprocess.run(["nice", "sh", "build.sh", PROBE_DLL], cwd=mod, env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit("probe build failed:\n" + r.stdout + r.stderr)
    print(r.stdout.strip())


def start_server(rp, st):
    if listening(GATE):
        if st.get("server_pid"):
            return
        sys.exit(f"something already listens on :{GATE} — stop it first, or ask the user")
    env = {**ENV, "UPNP": "0", "PES_CONSOLE": "0", "PYTHONUNBUFFERED": "1", "TURN": "1",
           "PYTHONPATH": os.path.join(rp, "server"), "PESSERVER_MOD_DLL": PROBE_DLL,
           "PATCH_CTL": PATCH, "PATCH_TXLOG": REPLIES}
    if ALT:
        os.makedirs(TESTDATA, exist_ok=True)
        env.update({k: str(v) for k, v in ALT_PORTS.items()}, TEST_DATA=TESTDATA)
    for kv in os.environ.get("SERVER_ENV", "").split():
        k, _, v = kv.partition("="); env[k] = v
    out = open(SERVER_OUT, "a")
    out.write(f"\n===== probesession {time.ctime()} repo={rp}\n"); out.flush()
    if not os.path.exists(PATCH):
        open(PATCH, "w").write("{}\n")
    p = subprocess.Popen(["python3", os.path.join(HERE, "testserver", "run_server.py")], cwd=os.path.join(rp, "server"),
                         env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    st["server_pid"] = p.pid
    if not os.path.islink(SERVER_LOG):
        if os.path.exists(SERVER_LOG):
            os.replace(SERVER_LOG, SERVER_LOG + ".probesession")
            st["server_log_moved"] = True
        os.symlink(SERVER_OUT, SERVER_LOG)
    save(st)
    for _ in range(60):
        if listening(GATE) and listening(HTTP):
            print(f"test server up (pid {p.pid}) -> {SERVER_OUT}"); return
        if p.poll() is not None:
            sys.exit(f"test server exited; see {SERVER_OUT}")
        time.sleep(0.5)
    sys.exit(f"test server did not come up; see {SERVER_OUT}")


def stamp():
    with urllib.request.urlopen(f"http://127.0.0.1:{HTTP}/mod/dinput8.dll?from=admin", timeout=10) as r:
        data = r.read()
    if len(data) < 100000 or data[:2] != b"MZ":
        sys.exit("stamped DLL download looks wrong")
    open(STAMPED, "wb").write(data)


def install(inst, st):
    dll = os.path.join(game(inst), "files", "dinput8.dll")
    cur, probe = sha(dll), sha(STAMPED)
    ours = st.setdefault("installed", {}).get(str(inst))       # a probe build this session put there earlier
    if cur != probe:
        if cur == ours:
            pass
        elif not os.path.exists(BACKUP):
            shutil.copy2(dll, BACKUP)
        elif cur != sha(BACKUP):
            keep = os.path.join(TOOLS, f"dinput8-copy{inst}-{cur[:12]}.dll")
            shutil.copy2(dll, keep)
            st.setdefault("restore", {})[str(inst)] = keep
            print(f"copy {inst}: its DLL is not the release backup; saved as {keep}, restored from it on `down`")
        shutil.copy2(STAMPED, dll)
    st["installed"][str(inst)] = probe
    save_dir = os.path.join(game(inst), "proton", "pfx", SAVE_REL)
    st.setdefault("had_liveupdate", {}).setdefault(str(inst), os.path.exists(save_dir))
    save(st)


def launch(inst):
    env = {**ENV, "PES_INSTANCE": str(inst), "PES2021ONLINE_PROBE": str(port(inst))}
    if ALT:
        env["NETSHIM_PORTMAP"] = PORTMAP
    subprocess.run(["python3", os.path.join(TOOLS, "pes.py"), "relaunch"], env=env)
    for _ in range(120):
        if listening(port(inst)):
            print(f"copy {inst}: probe on 127.0.0.1:{port(inst)}"); return True
        time.sleep(1)
    print(f"copy {inst}: probe port never opened (is the probe DLL installed? see logs/game-dev*.out)")
    return False


def flowtool():
    return os.path.join(HERE, "pesflow.py")


def login(inst):
    """Title + login with keys (gated on the server log), the login dialogs with the probe, until the main menu.
    An account without a player gets a test player "Probe<inst>" (delete it after the session)."""
    pes = lambda *a: subprocess.run(["python3", os.path.join(TOOLS, "pes.py"), *a], capture_output=True, text=True,
                                    env={**ENV, "PES_INSTANCE": str(inst)}).stdout
    fl = lambda *a: subprocess.run(["python3", flowtool(), str(port(inst)), *a], capture_output=True, text=True)
    r = fl("wait", "Intro/MenuTitle", "180")
    print(f"copy {inst}: title {'reached' if r.returncode == 0 else 'NOT reached: ' + r.stderr.strip()[-200:]}")
    time.sleep(1)
    fl("title")                                       # no key press (pesflow title_press)
    end = time.time() + 120
    while time.time() < end:
        r = fl("wait", "TopMenu/", "15")
        if r.returncode == 0:
            print(f"copy {inst}: main menu (no keys)"); return
        w = fl("windows").stdout
        if "MenuCreatingUserData" in w:               # the account has no player: the name needs keys
            pes("key", "Return", "sleep:2", f"type:Probe{inst}", "sleep:1", "Return", "sleep:1.5", "Down", "Return")
            print(f"copy {inst}: created test player Probe{inst} (delete it after the session)")
        elif "Intro/MenuTitle" in fl("screen").stdout:  # first start of a copy: region/terms still need keys
            out = pes("flow", "login")
            print("\n".join(l for l in out.splitlines() if l.startswith(("OK", "--"))))
    r = fl("wait", "TopMenu/", "90")
    print(f"copy {inst}: {'main menu' if r.returncode == 0 else 'main menu NOT reached: ' + r.stderr.strip()[-200:]}")


def up(args):
    which = [1, 2] if "both" in args else [2] if "2" in args else [1]
    st = load()
    rp = repo()
    build(rp)
    start_server(rp, st)
    stamp()
    for i in which:
        install(i, st)
    st["games"] = sorted(set(st.get("games", [])) | set(which)); save(st)
    for i in which:
        if launch(i) and "--no-login" not in args:
            login(i)
    print("ready:", ", ".join(f"pesflow.py {port(i)} screen" for i in which))


def down(_args):
    st = load()
    for i in (1, 2):
        pfx = os.path.join(game(i), "proton", "pfx")
        if i in st.get("games", []) or listening(port(i)):
            subprocess.run([WINESERVER, "-k"], env={**ENV, "WINEPREFIX": pfx})
    time.sleep(1)
    pid = st.get("server_pid")
    if pid:
        try:
            os.killpg(pid, 15)
        except ProcessLookupError:
            pass
    for i in (1, 2):
        dll = os.path.join(game(i), "files", "dinput8.dll")
        src = st.get("restore", {}).get(str(i), BACKUP)
        cur = sha(dll)
        if cur and cur != sha(src) and (cur in (sha(STAMPED), st.get("installed", {}).get(str(i))) or i in st.get("games", [])):
            shutil.copy2(src, dll); print(f"copy {i}: release DLL restored")
        saves = os.path.join(game(i), "proton", "pfx", SAVE_REL)
        if os.path.exists(saves) and st.get("had_liveupdate", {}).get(str(i)) is False:
            shutil.rmtree(saves); print(f"copy {i}: test Live Update save deleted")
    if os.path.islink(SERVER_LOG):
        os.unlink(SERVER_LOG)
        if st.get("server_log_moved"):
            os.replace(SERVER_LOG + ".probesession", SERVER_LOG)
    if os.path.exists(STATE):
        os.unlink(STATE)
    print("down. (Tell the user to restart their own server if they use one.)")


def restart(_args):
    """Restart the test server (new code) and the session's games, back to the main menu."""
    st = load()
    pid = st.pop("server_pid", None)
    if pid:
        try:
            os.killpg(pid, 15)
        except ProcessLookupError:
            pass
        for _ in range(40):
            if not listening(GATE):
                break
            time.sleep(0.25)
    save(st)
    start_server(repo(), st)
    stamp()
    for i in st.get("games", []):
        install(i, st)
        if launch(i):
            login(i)


def status(_args):
    st = load()
    print("state:", json.dumps(st) if st else "(no session)")
    print(f"test server :{GATE}", "up" if listening(GATE) else "down", "(side by side)" if ALT else "")
    for i in (1, 2):
        dll = sha(os.path.join(game(i), "files", "dinput8.dll"))
        kind = "release" if dll == sha(BACKUP) else "probe" if dll == sha(STAMPED) else "other"
        print(f"copy {i}: dll={kind} probe port {'open' if listening(port(i)) else 'closed'}")


if __name__ == "__main__":
    a = sys.argv[1:] or ["status"]
    {"up": up, "down": down, "status": status, "restart": restart}[a[0]](a[1:])
