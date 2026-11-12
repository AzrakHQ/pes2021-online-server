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

The file's SHA-256 is worked out by a background thread (start), at startup and when the folder changes, so a
game's request doesn't wait for hundreds of MB to be read. A name with spaces or other characters that don't
travel well in a web address is offered under a plain one (safe_name).
"""
import hashlib, logging, os, re, threading, time

from . import cpkread

log = logging.getLogger("pesserver.liveupdate")
VERSION_FILE = "common/etc/version/version_wu.bin"
_CACHE = {}                                   # path -> (mtime, size, entry fields) | (mtime, size, None)
_LOCK = threading.Lock()                      # one reader at a time (the thread, or a request that came first)
CHECK_EVERY = 30                              # s between the thread's looks at the folder


def safe_name(name):
    """The name the game is given and asks for: letters, digits, dot, dash and underscore only."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "update.cpk"


def folder(svc):
    return os.path.join(getattr(svc, "data_dir", "") or ".", "liveupdate")


def _version(path):
    raw = cpkread.read_file(path, VERSION_FILE)
    if not raw:
        return None
    v = raw[:10].split(b"\0", 1)[0].decode("ascii", "replace")
    digits = v.replace(".", "")
    return digits if re.fullmatch(r"\d{1,6}", digits) and int(digits) > 0 else None


def current(svc):
    """(path, {name, size, hash, dlc_version}) of the update to offer, or None."""
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
    with _LOCK:
        return _newest(files)


def _newest(files):
    for st, path in sorted(files, key=lambda x: x[0].st_mtime, reverse=True):
        c = _CACHE.get(path)
        if c is None or c[:2] != (st.st_mtime, st.st_size):
            info = None
            try:
                ver = _version(path)
                if ver is None:
                    log.warning("Live Update %s has no %s: not offered", os.path.basename(path), VERSION_FILE)
                else:
                    h = hashlib.sha256()
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    info = {"name": safe_name(os.path.basename(path)), "size": st.st_size, "hash": h.hexdigest(),
                            "dlc_version": ver}
                    log.info("Live Update offered: %s (version %s, %d bytes)", info["name"], ver, st.st_size)
            except Exception as e:  # noqa: BLE001 - a damaged file fails in many ways; none may stop the others
                log.warning("Live Update %s can't be read: %s", os.path.basename(path), e)
            c = _CACHE[path] = (st.st_mtime, st.st_size, info)
        if c[2] is not None:
            return path, c[2]
    return None


def entry(svc, base_url):
    cur = current(svc)
    if cur is None:
        return None
    _, info = cur
    return {**info, "url": f"{base_url}/liveupdate/{info['name']}"}


def start(svc):
    """Background thread: keep the offered file's checksum ready, so no game request has to work it out."""
    def loop():
        while True:
            try:
                current(svc)
            except Exception:  # noqa: BLE001
                log.exception("Live Update check")
            time.sleep(CHECK_EVERY)
    t = threading.Thread(target=loop, name="liveupdate", daemon=True)
    t.start()
    return t


def file_for(svc, name):
    """The file to serve for GET /liveupdate/<name>: only the one currently offered (no other paths)."""
    cur = current(svc)
    if cur is None or cur[1]["name"].lower() != name.lower():     # httpgate lowercases the route
        return None
    return cur[0]
