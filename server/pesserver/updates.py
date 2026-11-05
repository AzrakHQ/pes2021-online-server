"""Tells the host when a newer server is out. Every 6 hours it asks GitHub whether the server code
(server/) changed since the commit this program was built from; docs-only commits don't
count. The admin page and the console show the result. UPDATE_CHECK=0 turns it off."""
import json, logging, os, threading, time, urllib.request

log = logging.getLogger("pesserver.updates")
REPO = "MohamedAliRashad/pes2021-private-server"
STATE = {"available": False, "latest": "", "checked": 0.0}


def _matters(name):
    return name.startswith("server/")


def parse_compare(d):
    """GitHub's compare (this build...main) reply -> the newer version's label, or "" if none matters."""
    if d.get("status") not in ("ahead", "diverged") or not d.get("ahead_by"):
        return ""
    files = d.get("files") or []
    if len(files) < 300 and not any(_matters(f.get("filename", "")) for f in files):
        return ""                             # (300 = GitHub's cap: a list that long is assumed to matter)
    last = (d.get("commits") or [{}])[-1]
    when = (((last.get("commit") or {}).get("committer") or {}).get("date") or "")[:10]
    sha = (last.get("sha") or "")[:7]
    return f"{when} ({sha})" if when and sha else "newer"


def check_once(commit):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/compare/{commit}...main",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "pes2021-private-server"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_compare(json.load(r))


def start(on_new=None, every=6 * 3600, first=60):
    """Background thread; on_new(label) is called once per newly found version."""
    from . import COMMIT as commit
    if os.environ.get("UPDATE_CHECK", "1") == "0" or len(commit) < 7:
        return None                           # turned off, or not an official build (nothing to compare)

    def loop():
        time.sleep(first)
        while True:
            try:
                latest = check_once(commit)
                STATE["checked"] = time.time()
                if latest and latest != STATE["latest"]:
                    STATE.update(available=True, latest=latest)
                    log.warning("a newer server version is out: %s", latest)
                    if on_new:
                        on_new(latest)
            except Exception as e:  # noqa: BLE001  (offline, rate limit, unknown commit: try later)
                log.info("update check failed: %s", e)
            time.sleep(every)

    t = threading.Thread(target=loop, name="update-check", daemon=True)
    t.start()
    return t
