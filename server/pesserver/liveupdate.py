"""Live Update from the host: a CPK in <data>/liveupdate/ is offered to every game through CMD_GET_DLC_FILELIST.

Why: two friends can only play when their player data is identical (both CHECK_CHEAT sums; datacheck.md). A
roster update the host publishes gives everyone the same data through the game's own downloader: one plain
HTTP GET, checked by SHA-256 (notes/re/functions/live.md, livetests.md).

Rules found live (2026-11-05/06):
- the entry is an inline map {name, size, url, hash (sha256, lowercase), dlc_version}; dlc_version is the
  update's version as an integer string ("1.02" in version_wu.bin -> "102") and must be higher than the one the
  game has saved, or it doesn't download;
- **keep listing it**: the game asks again whenever it enters an online mode (always with pid=0); if the list
  is empty then, it computes its checksums from the base data although the update stays saved;
- the CPK must contain common/etc/version/version_wu.bin (three 10-byte fields: live data version, game
  version, label); notes/re/functions/tools/mklivedata.py builds one.
The newest *.cpk in the folder wins. Remove the file to stop offering it (games keep what they saved).
An option file (EDIT00000000) put in the folder is converted into option-EDIT00000000.cpk first (pesoption.py):
online modes never use edit data, so that is how its squads reach online play. A patch CPK (database tables, no
version_wu.bin) becomes patch-<name> with its tables.

The file's SHA-256 is worked out by a background thread (start), at startup and when the folder changes, so a
game's request doesn't wait for hundreds of MB to be read. A name with spaces or other characters that don't
travel well in a web address is offered under a plain one (safe_name).
"""
import hashlib, logging, os, re, threading, time

from . import cpkread

log = logging.getLogger("pesserver.liveupdate")
VERSION_FILE = "common/etc/version/version_wu.bin"
EXTRA_VERSION_FILE = "common/etc/version/version_xu.bin"
# The two kinds of file the folder can offer: the Live Update (version_wu.bin: player data, bound on entering an
# online mode) and the Extra Update (version_xu.bin: bound at every start, before the menus load their pictures,
# so it can change graphics such as flags and emblems; update.md).
KINDS = {"wu": VERSION_FILE, "xu": EXTRA_VERSION_FILE}
_CACHE = {}                                   # (path, kind) -> (mtime, size, entry fields) | (mtime, size, None)
_LOCK = threading.Lock()                      # one reader at a time (the thread, or a request that came first)
_LAST = {}                                    # (folder, kind) -> the last answer (while the thread reads a new file)
_WATCHED = set()                              # folders the thread looks after: only it reads their new files
_WAKE = threading.Event()                     # a request met a new or changed file: the thread looks now
CHECK_EVERY = 30                              # s between the thread's looks at the folder


def safe_name(name):
    """The name the game is given and asks for: letters, digits, dot, dash and underscore only."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "update.cpk"


def folder(svc):
    return os.path.join(getattr(svc, "data_dir", "") or ".", "liveupdate")


def _version(path, kind="wu"):
    raw = cpkread.read_file(path, KINDS[kind])
    if not raw:
        return None
    v = raw[:10].split(b"\0", 1)[0].decode("ascii", "replace")
    digits = v.replace(".", "")
    return digits if re.fullmatch(r"\d{1,6}", digits) and int(digits) > 0 else None


def _has(path, inner):
    try:
        return cpkread.read_file(path, inner) is not None
    except Exception:  # noqa: BLE001 - damaged: said elsewhere
        return False


def current(svc, wait=False, kind="wu"):
    """(path, {name, size, hash, dlc_version}) of the update of this kind to offer, or None."""
    d = folder(svc)
    files = []
    try:
        for f in os.listdir(d):
            if f.lower().endswith(".cpk"):
                try:
                    files.append((os.stat(os.path.join(d, f)), os.path.join(d, f)))
                except OSError:                   # a link to nowhere, a file that just went away
                    continue
    except OSError:
        return None
    # The server's own loop never reads a big file, nor waits for the thread reading one: it gets the last answer.
    if not wait and d in _WATCHED and not _known(files, kind):
        _WAKE.set()                           # (a replaced file fails the game's check until its new sum is ready)
        return _LAST.get((d, kind))
    if not _LOCK.acquire(blocking=wait):
        return _LAST.get((d, kind))
    try:
        _LAST[(d, kind)] = _newest(files, kind)
        return _LAST[(d, kind)]
    finally:
        _LOCK.release()


def _by_age(files):
    return sorted(files, key=lambda x: x[0].st_mtime, reverse=True)


def _known(files, kind):
    """The answer can be given without reading a file: no new or changed one comes before the file to offer."""
    for st, path in _by_age(files):
        c = _CACHE.get((path, kind))
        if c is None or c[:2] != (st.st_mtime, st.st_size):
            return False
        if c[2] is not None:
            return True
    return True


def _newest(files, kind="wu"):
    for st, path in _by_age(files):
        c = _CACHE.get((path, kind))
        if c is None or c[:2] != (st.st_mtime, st.st_size):
            info = None
            try:
                ver = _version(path) if kind == "wu" else _version(path, kind)
                if ver is None:
                    if kind == "wu" and not _has(path, EXTRA_VERSION_FILE):
                        log.warning("Live Update %s has neither %s nor %s: not offered", os.path.basename(path),
                                    VERSION_FILE, EXTRA_VERSION_FILE)
                else:
                    h = hashlib.sha256()
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    info = {"name": safe_name(os.path.basename(path)), "size": st.st_size, "hash": h.hexdigest(),
                            "dlc_version": ver}
                    log.info("%s offered: %s (version %s, %d bytes)", "Live Update" if kind == "wu" else
                             "Extra Update", info["name"], ver, st.st_size)
            except Exception as e:  # noqa: BLE001 - a damaged file fails in many ways; none may stop the others
                if kind == "wu":
                    log.warning("Live Update %s can't be read: %s", os.path.basename(path), e)
            c = _CACHE[(path, kind)] = (st.st_mtime, st.st_size, info)
        if c[2] is not None:
            return path, c[2]
    return None


def entry(svc, base_url, kind="wu"):
    cur = current(svc, kind=kind)
    if cur is None:
        return None
    _, info = cur
    return {**info, "url": f"{base_url}/liveupdate/{info['name']}"}


# The Extra Update downloader insists on HTTPS (live 2026-11-22: a TLS handshake on an http:// address), and the
# server's certificate is for the game's own NTL names, which the mod sends to this server: the address names one.
def extra_base():
    return f"https://ntl.service.konami.net:{os.environ.get('PESSERVER_HTTP_PORT', '8080')}"


def start(svc):
    """Background thread: keep the offered file's checksum ready, so no game request has to work it out."""
    _WATCHED.add(folder(svc))

    def loop():
        while True:
            try:
                from . import pesoption
                pesoption.refresh(folder(svc))       # an option file in the folder -> its Live Update CPK
            except Exception:  # noqa: BLE001
                log.exception("option file")
            try:
                from . import gamedb
                gamedb.refresh(folder(svc))          # the admin's changes on top of a new update under them
            except Exception:  # noqa: BLE001
                log.exception("game data changes")
            for kind in KINDS:
                try:
                    current(svc, wait=True, kind=kind)
                except Exception:  # noqa: BLE001
                    log.exception("Live Update check")
            _WAKE.wait(CHECK_EVERY)
            _WAKE.clear()
    t = threading.Thread(target=loop, name="liveupdate", daemon=True)
    t.start()
    return t


def wake():
    """Look at the folder now (a new file was just made)."""
    _WAKE.set()


def file_for(svc, name):
    """The file to serve for GET /liveupdate/<name>: only the ones currently offered (no other paths)."""
    for kind in KINDS:
        cur = current(svc, kind=kind)
        if cur is not None and cur[1]["name"].lower() == name.lower():     # httpgate lowercases the route
            return cur[0]
    return None
