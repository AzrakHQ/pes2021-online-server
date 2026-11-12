"""Backups of the server's database (players, results, tables, tournaments): <data>/backups/*.db.

One is made by itself every day (the newest 7 are kept) and before every restore; the host can make one at any
time, download it, or restore one from the admin page (Server, Backups). A backup file put into the folder by
hand (moving to a new computer) shows up in the list too. Restoring replaces the database in place, without a
restart: everyone online is disconnected first and signs in again.
"""
import asyncio, logging, os, re, sqlite3, time

log = logging.getLogger("pesserver.backup")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.db$")
KEEP_AUTO, EVERY = 7, 86400


def folder(svc):
    return os.path.join(getattr(svc, "data_dir", None) or ".", "backups")


def listing(svc):
    """Newest first: [{name, t, size, auto}]."""
    d, out = folder(svc), []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if NAME_RE.fullmatch(n):
            try:
                st = os.stat(os.path.join(d, n))
            except OSError:
                continue
            out.append({"name": n, "t": st.st_mtime, "size": st.st_size, "auto": n.startswith("auto-")})
    return sorted(out, key=lambda b: -b["t"])


def file_for(svc, name):
    """The path of a listed backup, or None (names are matched whole: no way out of the folder)."""
    name = str(name or "")
    if not NAME_RE.fullmatch(name):
        return None
    p = os.path.join(folder(svc), name)
    return p if os.path.isfile(p) else None


def make(svc, kind="manual"):
    """Copy the live database into a new backup file (SQLite's own backup: safe while games are being played).
    Returns its listing entry."""
    d = folder(svc)
    os.makedirs(d, exist_ok=True)
    name = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    path, tmp = os.path.join(d, name), os.path.join(d, name + ".tmp")
    try:
        dst = sqlite3.connect(tmp)
        try:
            with svc.accounts.lock:
                svc.accounts.db.commit()
                svc.accounts.db.backup(dst)
        finally:
            dst.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, sqlite3.Error) as e:         # a full disk: leave no half file behind
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise OSError(f"the backup couldn't be written ({e})") from None
    autos = [b for b in listing(svc) if b["auto"]]
    for b in autos[KEEP_AUTO:]:
        try:
            os.remove(os.path.join(d, b["name"]))
        except OSError:
            pass
    log.info("backup %s (%d KB)", name, os.path.getsize(path) // 1024)
    return next(b for b in listing(svc) if b["name"] == name)


def check(path):
    """Is this file a server database? Returns how many players and matches it holds; ValueError if not."""
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("That backup file is damaged.")
            players = src.execute("SELECT COUNT(*) FROM pes_players").fetchone()[0]
            has = src.execute("SELECT 1 FROM sqlite_master WHERE name='pes_matches'").fetchone()
            matches = src.execute("SELECT COUNT(*) FROM pes_matches").fetchone()[0] if has else 0
        finally:
            src.close()
    except sqlite3.Error:
        raise ValueError("That file isn't a backup of this server.") from None
    return {"players": players, "matches": matches}


def restore(svc, name):
    """Replace the live database with a backup. The current one is saved first (before-restore-…); once the
    backup is in place everyone online is disconnected and has to sign in again (a game that only reconnected
    would carry on as whoever has its player number in the backup), and what the server remembers in memory is
    dropped. RuntimeError: the copy failed and nothing was changed."""
    path = file_for(svc, name)
    if path is None:
        raise LookupError("That backup is gone.")
    found = check(path)
    make(svc, "before-restore")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        with svc.accounts.lock:
            svc.accounts.db.commit()
            src.backup(svc.accounts.db)
    except sqlite3.Error as e:
        raise RuntimeError(f"the backup couldn't be copied in ({e})") from None
    finally:
        src.close()
    from . import pescmds
    for pid, sess in list(svc.online.items()):
        sess.kicked = True                    # not an early exit: no forfeit, no courtesy mark
        pescmds.show_out(svc, pid)            # (the game's own reconnect is refused: a full sign-in)
        if hasattr(sess, "close"):
            sess.close()
    started = getattr(svc, "pes_events", {}).get("started")
    for attr in [a for a in vars(svc) if a.startswith(("pes_", "_pes_")) and a not in ("pes_shown_out", "_pes_discord")]:
        delattr(svc, attr)                    # queues, live matches, the feed, "table is ready" flags: read again
    getattr(svc.accounts.db, "ready", set()).clear()         # an older backup may lack newer tables and columns
    from . import events
    if started:
        events._st(svc)["started"] = started  # (the server itself didn't restart)
    log.warning("database restored from %s (%s)", name, found)
    return found


async def daily(svc):
    """A backup a day, as long as the server runs (checked every hour)."""
    while True:
        try:
            autos = [b for b in listing(svc) if b["auto"]]
            if not autos or time.time() - autos[0]["t"] > EVERY:
                await asyncio.to_thread(make, svc, "auto")
        except Exception:  # noqa: BLE001 - never stop the server over a backup
            log.exception("daily backup")
        await asyncio.sleep(3600)
