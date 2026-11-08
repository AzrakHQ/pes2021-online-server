"""Install the mod into PES 2021 copies on the host's own machine, from the admin page.

The server looks for PES2021.exe on this computer (Steam libraries, ~/Games, on Windows the registry)
and, on Linux, for each copy's Wine prefix.

Installing writes dinput8.dll (packed with this server's identity) next to PES2021.exe and, on
Linux, tells Wine to load it for PES2021.exe only:
    [Software\\Wine\\AppDefaults\\PES2021.exe\\DllOverrides]  "dinput8"="native,builtin"
in the prefix's user.reg, the same per-program setting Proton uses for its own overrides. That
replaces the Steam launch option. Wine rewrites user.reg when the game exits, so the game must be
closed while installing. A dinput8.dll that isn't ours (another mod) is never overwritten.

Automatic (auto_start): every minute, while PES 2021 isn't running, every copy found on this computer gets the
mod if it has none, and this server's mod is replaced when the server hands out a different file (a new mod
version, new addresses). A copy with another server's mod or someone else's dinput8.dll is left alone, and so
is a copy the host removed the mod from on the admin page. The admin page can turn it off (auto.json).
"""
import json, logging, os, re, sys, threading, time

from . import identity

SECTION = "[Software\\\\Wine\\\\AppDefaults\\\\PES2021.exe\\\\DllOverrides]"
LINE = '"dinput8"="native,builtin"'


LOCAL = None           # [(dir, prefix or None)] found on this computer (searched on first use)
log = logging.getLogger("pesserver.gamesetup")
AUTO = {"path": None, "on": True, "skip": [], "last": {}, "found_at": 0.0}
AUTO_EVERY = 60        # seconds between passes
RESCAN_EVERY = 600     # look for newly installed copies


def _steam_roots():
    if sys.platform == "win32":
        roots = []
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, name)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots.append(os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam"))
        return roots
    h = os.path.expanduser("~")
    return [os.path.join(h, ".steam/steam"), os.path.join(h, ".local/share/Steam"),
            os.path.join(h, ".var/app/com.valvesoftware.Steam/.local/share/Steam")]


def _prefix_for(d):
    """The Wine prefix PES 2021 runs in (Linux): Steam's compatdata, or a pfx next to the game."""
    if sys.platform == "win32":
        return None
    m = d.replace("\\", "/").split("/steamapps/common/")
    if len(m) > 1:
        p = os.path.join(m[0], "steamapps/compatdata/1259970/pfx")
        if os.path.isfile(os.path.join(p, "user.reg")):
            return p
    for c in (os.path.join(d, "../proton/pfx"), os.path.join(d, "../pfx")):
        if os.path.isfile(os.path.join(c, "user.reg")):
            return os.path.realpath(c)
    return None


def find_local_games():
    found = []
    libs = []
    for root in _steam_roots():
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        libs.append(root)
        try:
            text = open(vdf, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        libs += [m.replace("\\\\", "\\") for m in re.findall(r'^\s*"path"\s*"(.*)"\s*$', text, re.M)]
    for lib in libs:
        common = os.path.join(lib, "steamapps", "common")
        try:
            names = os.listdir(common)
        except OSError:
            continue
        for n in names:
            if os.path.isfile(os.path.join(common, n, "PES2021.exe")):
                found.append(os.path.join(common, n))
    games = os.path.expanduser("~/Games")
    if sys.platform != "win32" and os.path.isdir(games):
        for top, dirs, files in os.walk(games):
            depth = top[len(games):].count(os.sep)
            dirs[:] = [x for x in dirs if x != "pfx" and depth < 5]
            if "PES2021.exe" in files:
                found.append(top)
    out, seen = [], set()
    for d in sorted(found):
        real = os.path.realpath(d)
        key = os.path.normcase(real)
        if key not in seen:
            seen.add(key)
            out.append((real, _prefix_for(real)))
    return out


def use_local_games():
    global LOCAL
    try:
        LOCAL = find_local_games()
    except Exception:  # noqa: BLE001  (never stop the server over this)
        LOCAL = []


def _games():
    if LOCAL is None:
        use_local_games()
    return [{"id": str(i + 1), "dir": d, "host_dir": d, "prefix": p, "host_prefix": p}
            for i, (d, p) in enumerate(LOCAL)]


def _dll_state(path):
    """'none', 'ours' (this server), 'other-server' (our mod, another server) or 'foreign' (not ours)."""
    try:
        data = open(path, "rb").read()
    except FileNotFoundError:
        return "none"
    i = data.find(identity.EMBED_MARKER)
    if i < 0:
        return "foreign"
    area = data[i + len(identity.EMBED_MARKER):i + identity.EMBED_SIZE]
    return "ours" if f"key={identity.public_hex()}".encode() in area else "other-server"


def _override_set(user_reg):
    try:
        text = open(user_reg, encoding="utf-8", errors="surrogateescape").read()
    except OSError:
        return False
    i = text.find(SECTION)
    if i < 0:
        return False
    block = text[i:text.find("\n\n", i) if text.find("\n\n", i) > 0 else len(text)]
    return LINE in block


def _set_override(user_reg, on):
    """Add (on) or remove our dinput8 line in the PES2021.exe override section of user.reg."""
    enc = dict(encoding="utf-8", errors="surrogateescape")
    original = open(user_reg, **enc).read()
    text, kept = original, []
    i = text.find(SECTION)
    if i >= 0:                                    # take the section out, keeping any other overrides
        end = text.find("\n\n", i)
        end = len(text) if end < 0 else end
        kept = [l for l in text[i:end].split("\n")[1:]
                if l and not l.startswith("#") and not l.startswith('"dinput8"=')]
        text = text[:i] + text[end:].lstrip("\n")
    if on:
        kept.append(LINE)
    if kept:
        text = text.rstrip("\n") + f"\n\n{SECTION} {int(time.time())}\n" + "\n".join(kept) + "\n"
    st = os.stat(user_reg)
    backup = user_reg + ".pes2021-backup"
    if not os.path.exists(backup):
        _write_as(backup, original, st)
    _write_as(user_reg + ".pes2021-tmp", text, st)
    os.replace(user_reg + ".pes2021-tmp", user_reg)


def _write_as(path, text, st):
    """Write a file owned like `st` (the player's own user, not the container's root)."""
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)
    try:
        os.chown(path, st.st_uid, st.st_gid)
    except (OSError, AttributeError):             # AttributeError: Windows has no chown
        pass


def status():
    out = []
    for g in _games():
        dll = _dll_state(os.path.join(g["dir"], "dinput8.dll"))
        override = _override_set(os.path.join(g["prefix"], "user.reg")) if g["prefix"] else None
        out.append({"id": g["id"], "path": g["host_dir"], "mod": dll, "override": override,
                    "prefix": (g["host_prefix"] or g["prefix"]) if g["prefix"] else None})
    return out


def install(gid, dll_bytes):
    g = next((g for g in _games() if g["id"] == gid), None)
    if not g:
        raise LookupError("no such game")
    path = os.path.join(g["dir"], "dinput8.dll")
    if _dll_state(path) == "foreign":
        raise PermissionError("This folder already has a different dinput8.dll (another mod). Remove it first.")
    tmp = path + ".pes2021-tmp"
    with open(tmp, "wb") as f:
        f.write(dll_bytes)
    st = os.stat(g["dir"])
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (OSError, AttributeError):
        pass
    os.replace(tmp, path)
    if g["prefix"]:
        _set_override(os.path.join(g["prefix"], "user.reg"), True)
    _skip(g["dir"], False)
    return status()


def remove(gid):
    g = next((g for g in _games() if g["id"] == gid), None)
    if not g:
        raise LookupError("no such game")
    _skip(g["dir"], True)                         # the host took it out: the automatic pass leaves it alone
    path = os.path.join(g["dir"], "dinput8.dll")
    if _dll_state(path) in ("ours", "other-server"):
        os.remove(path)
    if g["prefix"] and _override_set(os.path.join(g["prefix"], "user.reg")):
        _set_override(os.path.join(g["prefix"], "user.reg"), False)
    return status()


# ---- automatic: keep the mod in this computer's copies up to date ---------------------------------------------
def _auto_load(data_dir):
    AUTO["path"] = os.path.join(data_dir, "auto.json") if data_dir else None
    try:
        with open(AUTO["path"]) as f:
            saved = json.load(f)
        AUTO["on"], AUTO["skip"] = bool(saved.get("on", True)), list(saved.get("skip") or [])
    except (OSError, ValueError, TypeError):
        pass


def _auto_save():
    if not AUTO["path"]:
        return
    try:
        with open(AUTO["path"] + ".tmp", "w") as f:
            json.dump({"on": AUTO["on"], "skip": AUTO["skip"]}, f)
        os.replace(AUTO["path"] + ".tmp", AUTO["path"])
    except OSError as e:
        log.info("auto.json not saved: %s", e)


def _skip(d, on):
    key = os.path.normcase(os.path.realpath(d))
    if on and key not in AUTO["skip"]:
        AUTO["skip"].append(key)
    elif not on and key in AUTO["skip"]:
        AUTO["skip"].remove(key)
    else:
        return
    _auto_save()


def set_auto(on):
    AUTO["on"] = bool(on)
    _auto_save()
    return AUTO["on"]


def game_running():
    """Is PES 2021 running on this computer (under Wine/Proton, or natively on Windows)?"""
    if sys.platform == "win32":
        try:
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PES2021.exe"], capture_output=True, text=True, timeout=10).stdout
            return "PES2021.exe" in out
        except Exception:  # noqa: BLE001
            return True                            # can't tell: don't touch anything
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return True
    for p in pids:
        try:
            with open(f"/proc/{p}/cmdline", "rb") as f:
                if _is_game(f.read()):
                    return True
        except OSError:
            continue
    return False


def _is_game(cmdline):
    """The game's own process: its program (first argument) is PES2021.exe, e.g. Wine's
    "Z:\\...\\PES2021.exe". Not a terminal command or a log viewer that merely mentions the file."""
    args = [a for a in cmdline.split(b"\0") if a]
    names = [a.replace(b"\\", b"/").lower().rsplit(b"/", 1)[-1] for a in args]
    if not names:
        return False
    if names[0] == b"pes2021.exe":
        return True
    # starting up: Proton's "proton waitforexitandrun …/PES2021.exe", Wine's "steam.exe …/PES2021.exe"
    launcher = b"waitforexitandrun" in args or names[0] in (b"steam.exe", b"wine", b"wine64", b"wine64-preloader")
    return launcher and b"pes2021.exe" in names[1:]


def auto_pass(dll_bytes, feed=None):
    """One pass. Returns what it did: [(game dir, "installed" | "updated")]."""
    if not AUTO["on"] or dll_bytes is None:
        return []
    if time.time() - AUTO["found_at"] > RESCAN_EVERY:
        use_local_games()
        AUTO["found_at"] = time.time()
    done = []
    games = [g for g in _games() if os.path.normcase(os.path.realpath(g["dir"])) not in AUTO["skip"]]
    if not games or game_running():
        return done
    for g in games:
        path = os.path.join(g["dir"], "dinput8.dll")
        state = _dll_state(path)
        if state == "ours":
            try:
                with open(path, "rb") as f:
                    if f.read() == dll_bytes and (not g["prefix"] or _override_set(os.path.join(g["prefix"], "user.reg"))):
                        continue
            except OSError:
                continue
        elif state != "none":
            continue                               # another server's mod, or another mod: leave it alone
        try:
            install(g["id"], dll_bytes)
        except Exception as e:  # noqa: BLE001  (a read-only folder, a busy file: try again next pass)
            if AUTO["last"].get(g["dir"]) != str(e):
                log.warning("couldn't put the mod in %s: %s", g["dir"], e)
                AUTO["last"][g["dir"]] = str(e)
            continue
        what = "installed" if state == "none" else "updated"
        done.append((g["dir"], what))
        log.info("mod %s in %s", what, g["dir"])
        if feed:
            feed(g["dir"], what)
    return done


def auto_start(svc, dll_bytes_fn):
    """Background thread: auto_pass now and every AUTO_EVERY seconds. dll_bytes_fn() gives the file to put in."""
    _auto_load(getattr(svc, "data_dir", None))
    from . import events, httpgate

    def feed(d, what):
        events.feed(svc, f"The mod was {what} in PES 2021 on this computer ({httpgate.copy_name(d)})", "info")

    def loop():
        time.sleep(5)
        while True:
            try:
                auto_pass(dll_bytes_fn(), feed)
            except Exception:  # noqa: BLE001  (never stop the server over this)
                log.exception("automatic mod pass")
            time.sleep(AUTO_EVERY)

    t = threading.Thread(target=loop, name="auto-mod", daemon=True)
    t.start()
    return t
