"""Install the mod into PES 2021 copies on the host's own machine, from the admin page.

The server looks for PES2021.exe on this computer (Steam libraries, ~/Games, on Windows the registry)
and, on Linux, for each copy's Wine prefix.

Installing writes dinput8.dll (packed with this server's identity) next to PES2021.exe and, on
Linux, tells Wine to load it for PES2021.exe only:
    [Software\\Wine\\AppDefaults\\PES2021.exe\\DllOverrides]  "dinput8"="native,builtin"
in the prefix's user.reg, the same per-program setting Proton uses for its own overrides. That
replaces the Steam launch option. Wine rewrites user.reg when the game exits, so the game must be
closed while installing. A dinput8.dll that isn't ours (another mod) is never overwritten.
"""
import os, re, sys, time

from . import identity

SECTION = "[Software\\\\Wine\\\\AppDefaults\\\\PES2021.exe\\\\DllOverrides]"
LINE = '"dinput8"="native,builtin"'


LOCAL = None           # [(dir, prefix or None)] found on this computer (searched on first use)


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
    return status()


def remove(gid):
    g = next((g for g in _games() if g["id"] == gid), None)
    if not g:
        raise LookupError("no such game")
    path = os.path.join(g["dir"], "dinput8.dll")
    if _dll_state(path) in ("ours", "other-server"):
        os.remove(path)
    if g["prefix"] and _override_set(os.path.join(g["prefix"], "user.reg")):
        _set_override(os.path.join(g["prefix"], "user.reg"), False)
    return status()
