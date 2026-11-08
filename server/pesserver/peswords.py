"""The host's word filter: text players type (player name, room names, profile texts, ...) is refused when it
contains a word from <data>/blocked_words.txt (one word or phrase a line, # comments; case does not matter).

The game asks the server itself (notes/re/functions/menus.md "CMD_CHECK_STRING"): CHECK_STRING {str} for room
names and profile texts, CREATE_PLAYER {name} for the player name. Any error reply makes the game show
"Includes unusable letters or phrases." and keep the player on the input. No file = nothing is refused.
"""
import logging, os, re, unicodedata

from .pescmds import NOERR, cmd

log = logging.getLogger("pesserver.words")
_cache = {}


def _norm(t):
    t = unicodedata.normalize("NFKC", str(t or "")).casefold()
    return re.sub(r"\s+", " ", t)


def file_path(svc):
    return os.path.join(getattr(svc, "data_dir", "") or ".", "blocked_words.txt")


def words(svc):
    path = file_path(svc)
    try:
        m = os.path.getmtime(path)
    except OSError:
        return []
    if _cache.get(path, (None,))[0] != m:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                ws = [_norm(l.split("#", 1)[0]).strip() for l in f]
        except OSError:
            ws = []
        _cache[path] = (m, [w for w in ws if w])
    return _cache[path][1]


def _joined(t):
    """Letters spelled out one at a time ("b.a.d", "b a d") joined back into a word."""
    return re.sub(r"(?<![^\W_])(?:[^\W_][\W_]+){2,}[^\W_](?![^\W_])", lambda m: re.sub(r"[\W_]+", "", m.group()), t)


def blocked(svc, text):
    """The blocked word found in text, or None. Whole words only (so "hell" never blocks "hello"); a phrase
    anywhere in the text."""
    t = _norm(text)
    for cand in (t, _joined(t)):
        for w in words(svc):
            if " " in w:
                if w in cand:
                    return w
            elif re.search(rf"(?<![^\W_]){re.escape(w)}(?![^\W_])", cand):
                return w
    return None


@cmd("CMD_CHECK_STRING")
def check_string(svc, sess, req):
    w = blocked(svc, req.get("str"))
    if w:
        log.info("refused text from pid %s (blocked word %r)", getattr(sess, "pes_pid", None), w)
        return {"result": "ERR_NGWORD"}
    return {"result": NOERR}
