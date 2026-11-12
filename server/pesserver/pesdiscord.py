"""Optional notices in the friends' Discord channel: "Omar is looking for a match" and final scores.

The host pastes a channel's webhook address on the admin page (Server, Discord). It is kept in
<data>/discord.json (only this user can read it: anyone who has the address can post to the channel). Notices are
sent from a background thread, so a slow or unreachable Discord never holds up a game.
"""
import json, logging, os, queue, re, threading, time, urllib.error, urllib.request

log = logging.getLogger("pesserver.discord")
URL_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,25}/[A-Za-z0-9_-]{20,200}$")
SEARCH_EVERY = 600         # s: at most one "is looking for a match" notice per player in this time
DEFAULT = {"url": "", "search": True, "results": True}


def _path(svc):
    d = getattr(svc, "data_dir", None)
    return os.path.join(d, "discord.json") if d else None


def config(svc):
    if not hasattr(svc, "_pes_discord"):
        cfg = dict(DEFAULT)
        try:
            with open(_path(svc), encoding="utf-8") as f:
                saved = json.load(f)
            if URL_RE.match(str(saved.get("url") or "")):
                cfg["url"] = saved["url"]
            cfg.update({k: bool(saved[k]) for k in ("search", "results") if k in saved})
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        svc._pes_discord = {"cfg": cfg, "error": "", "told": {}, "q": None}
    return svc._pes_discord["cfg"]


def save(svc, url=None, search=None, results=None):
    """Change the settings; url "" turns the notices off, None keeps the saved address."""
    cfg = config(svc)
    if url is not None:
        url = str(url).strip()
        if url and not URL_RE.match(url):
            raise ValueError("that isn't a Discord webhook address")
        cfg["url"] = url
        svc._pes_discord["error"] = ""
    if search is not None:
        cfg["search"] = bool(search)
    if results is not None:
        cfg["results"] = bool(results)
    path = _path(svc)
    if path:
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        os.replace(tmp, path)
    return status(svc)


def status(svc):
    """For the admin page: never the address itself, only that one is set (and the last problem sending)."""
    cfg = config(svc)
    return {"on": bool(cfg["url"]), "search": cfg["search"], "results": cfg["results"], "error": svc._pes_discord["error"]}


def _send(url, text):
    """Post one message; returns "" or what went wrong, in plain words."""
    body = json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "pes2021-server (webhook)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
        return ""
    except urllib.error.HTTPError as e:
        return {401: "Discord doesn't know this webhook any more", 404: "Discord doesn't know this webhook any more",
                429: "Discord is asking the server to slow down"}.get(e.code, f"Discord answered {e.code}")
    except (OSError, ValueError) as e:
        return f"couldn't reach Discord ({getattr(e, 'reason', e)})"


def test(svc):
    """Send a test message now (called off the event loop). Returns "" or the problem."""
    url = config(svc)["url"]
    if not url:
        return "no webhook address saved"
    err = _send(url, "✅ Your PES 2021 server can post here.")
    svc._pes_discord["error"] = err
    return err


def _worker(svc, q):
    while True:
        text = q.get()
        url = config(svc)["url"]
        if url:
            err = _send(url, text)
            if err != svc._pes_discord["error"]:
                svc._pes_discord["error"] = err
                if err:
                    log.warning("Discord notice not sent: %s", err)


def post(svc, text):
    st = svc._pes_discord
    if st["q"] is None:
        st["q"] = queue.Queue(maxsize=50)
        threading.Thread(target=_worker, args=(svc, st["q"]), name="discord", daemon=True).start()
    try:
        st["q"].put_nowait(text)
    except queue.Full:                    # Discord is unreachable and notices pile up: drop, don't grow
        pass


def _still_searching(svc, pid):
    mm = getattr(svc, "pes_mm", None) or {}
    return any(p == pid for p, _ in mm.get("queue", [])) or any(x[0] == pid for x in mm.get("coop_queue", []))


def on_feed(svc, item):
    """Called for every activity line (events.feed): turn the two interesting kinds into notices."""
    cfg = config(svc)
    if not cfg["url"]:
        return
    kind, text, pids = item["kind"], item["text"], item.get("pids") or []
    if kind == "search" and cfg["search"] and pids:
        pid, told, now = pids[0], svc._pes_discord["told"], time.time()
        if not _still_searching(svc, pid) or now - told.get(pid, 0) < SEARCH_EVERY:
            return                            # paired at once, or already announced a moment ago
        told[pid] = now
        post(svc, "🔎 " + text)
    elif kind == "result" and cfg["results"] and (text.startswith("Full time:") or "left the match early" in text):
        post(svc, "⚽ " + text)
