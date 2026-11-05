"""Install the mod into PES 2021 copies on the host's own machine, from the admin page.

install.sh looks for PES2021.exe on the host (Steam libraries, ~/Games) and mounts only those game
folders into the container at /games/<n>, plus each copy's Wine prefix at /prefixes/<n> when it finds
one. PES_GAME_<n> / PES_PREFIX_<n> carry the host paths, for display.

Installing writes dinput8.dll (packed with this server's identity) next to PES2021.exe and, on
Linux, tells Wine to load it for PES2021.exe only:
    [Software\\Wine\\AppDefaults\\PES2021.exe\\DllOverrides]  "dinput8"="native,builtin"
in the prefix's user.reg, the same per-program setting Proton uses for its own overrides. That
replaces the Steam launch option. Wine rewrites user.reg when the game exits, so the game must be
closed while installing. A dinput8.dll that isn't ours (another mod) is never overwritten.
"""
import os, time

from . import identity

GAMES_ROOT = os.environ.get("PESSERVER_GAMES_ROOT", "/games")
PREFIXES_ROOT = os.environ.get("PESSERVER_PREFIXES_ROOT", "/prefixes")
SECTION = "[Software\\\\Wine\\\\AppDefaults\\\\PES2021.exe\\\\DllOverrides]"
LINE = '"dinput8"="native,builtin"'


def _games():
    out = []
    try:
        names = sorted(os.listdir(GAMES_ROOT), key=lambda n: (len(n), n))
    except OSError:
        return out
    for n in names:
        d = os.path.join(GAMES_ROOT, n)
        if not os.path.lexists(os.path.join(d, "PES2021.exe")):   # may be a link into another copy
            continue
        pfx = os.path.join(PREFIXES_ROOT, n)
        out.append({"id": n, "dir": d, "host_dir": os.environ.get(f"PES_GAME_{n}", d),
                    "prefix": pfx if os.path.isfile(os.path.join(pfx, "user.reg")) else None,
                    "host_prefix": os.environ.get(f"PES_PREFIX_{n}")})
    return out


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
    except OSError:
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
    except OSError:
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
