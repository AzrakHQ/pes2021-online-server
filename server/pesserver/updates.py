"""Tells the host when a newer server program is out. Every 6 hours it reads the published release's
version.txt, the same release the admin page's "Update now" downloads, and compares it with this build.
The release is only rebuilt when server/ changes, so docs-only commits never count, and the notice never
promises a version that can't be installed yet (main can be ahead while a release is still building).
The admin page and the console show the result. UPDATE_CHECK=0 turns it off."""
import logging, os, re, threading, time

log = logging.getLogger("pesserver.updates")
STATE = {"available": False, "latest": "", "checked": 0.0}


def newer(release, mine):
    """The release's version label ("2026-11-11 (a95abd0)") when it is a different build that isn't older than
    this one, else ""."""
    release, mine = (release or "").strip(), (mine or "").strip()
    dated = lambda v: re.match(r"\d{4}-\d\d-\d\d", v)
    if not release or release == mine or (dated(release) and dated(mine) and release[:10] < mine[:10]):
        return ""
    return release


def check_once(mine):
    from . import hostctl
    return newer(hostctl.latest_version(), mine)


def settle():
    """Update now found nothing newer published: drop the notice (it may be from before a release was built)."""
    STATE.update(available=False, latest="")


def start(on_new=None, every=6 * 3600, first=60):
    """Background thread; on_new(label) is called once per newly found version."""
    from . import COMMIT as commit, VERSION as mine
    if os.environ.get("UPDATE_CHECK", "1") == "0" or len(commit) < 7:
        return None                           # turned off, or not an official build (nothing to compare)

    def loop():
        time.sleep(first)
        while True:
            try:
                latest = check_once(mine)
                STATE["checked"] = time.time()
                if latest and latest != STATE["latest"]:
                    STATE.update(available=True, latest=latest)
                    log.warning("a newer server version is out: %s", latest)
                    if on_new:
                        on_new(latest)
                elif not latest:
                    STATE.update(available=False, latest="")
            except Exception as e:  # noqa: BLE001  (offline, GitHub busy, no release yet: try later)
                log.info("update check failed: %s", e)
            time.sleep(every)

    t = threading.Thread(target=loop, name="update-check", daemon=True)
    t.start()
    return t
