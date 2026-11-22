"""The game data editor on the admin page: players (name, profile, positions, abilities, skills), team names and
squads (transfers), sent to every friend as a Live Update.

How: the data shown is what friends get now (the newest Live Update in <data>/liveupdate/ other than ours, or
the game's own tables when there is none), with the host's changes on top. Changes are kept in
<data>/liveupdate/edits.json until the host sends them; then edits.cpk is built: the offered update's files (as
they are) with Player.bin, Team.bin and PlayerAssignment.bin changed, and a version above every earlier one (a game
downloads only a higher version). When the update under it changes (a new option file or patch), edits.cpk is
built again on top of it with the changes that were sent, so it stays the newest.

Fields: the Player.bin parameters (pesoption.PMAP; their meaning from notes/re/functions/tools/pesdb.py, proven
on 60 players with the game's own converter, update.md), names at +0xfb (name) and +0x81 / +0xbe (shirt),
nationality at +0x1c bits 9..17. Team names: +0x170 and +0x5b6 (English), short +0x372 / +0x566.
PlayerAssignment.bin rows: {row id, player, team, flags: shirt-1 (10 bits) | squad slot << 10 (6 bits: 0..10 =
the starting eleven) | set-piece and captain bits 16..21}.
"""
import hashlib
import json
import logging
import os
import struct
import threading
import time

from . import cpkread, pesoption
from .pesoption import PMAP, PDB, unwrap, wrap, _put, _cstr

log = logging.getLogger("pesserver.gamedb")

EDITS = "edits.json"         # in the Live Update folder: the changes (sent or not)
OUT = "edits.cpk"            # ... and the Live Update made from them
PREC, TREC = 312, 0x5fc
TABLES = ("Player.bin", "Team.bin", "PlayerAssignment.bin", "Country.bin")
MIN_SQUAD, MAX_SQUAD, MAX_NATIONAL = 16, 40, 23

POSITIONS = ["GK", "CB", "LB", "RB", "DMF", "CMF", "LMF", "RMF", "AMF", "LWF", "RWF", "SS", "CF"]
# position -> its parameter (0 = can't play there, 1 = can, 2 = best)
POS_PARAM = {"CF": 1, "SS": 2, "LWF": 3, "RWF": 4, "AMF": 5, "DMF": 6, "CMF": 7, "LMF": 8, "RMF": 9, "CB": 10,
             "LB": 11, "RB": 12, "GK": 13}
# (parameter, label, lowest, highest, shown = stored + this): the player sheet's order (table 0x142978fd0)
ABILITIES = [(14, "Offensive Awareness"), (18, "Ball Control"), (17, "Dribbling"), (19, "Tight Possession"),
             (21, "Low Pass"), (22, "Lofted Pass"), (20, "Finishing"), (23, "Heading"), (26, "Place Kicking"),
             (27, "Curl"), (33, "Speed"), (37, "Acceleration"), (36, "Kicking Power"), (38, "Jump"),
             (34, "Physical Contact"), (35, "Balance"), (39, "Stamina"), (15, "Defensive Awareness"),
             (24, "Ball Winning"), (25, "Aggression"), (16, "GK Awareness"), (28, "GK Catching"),
             (29, "GK Parrying"), (30, "GK Reflexes"), (31, "GK Reach")]
FIELDS = ([(p, n, 40, 99, 0) for p, n in ABILITIES] +
          [(44, "Weak Foot Usage", 1, 4, 1), (32, "Weak Foot Accuracy", 1, 4, 1), (40, "Form", 1, 8, 1),
           (41, "Injury Resistance", 1, 3, 1), (45, "Age", 15, 50, 0), (46, "Height", 150, 210, 0),
           (47, "Weight", 40, 120, 0), (42, "Left foot", 0, 1, 0), (0, "Position", 0, 12, 0),
           (59, "Playing Style", 0, 21, 0)] +
          [(p, pos, 0, 2, 0) for pos, p in POS_PARAM.items()])
PLAYING_STYLES = ["None", "Goal Poacher", "Dummy Runner", "Fox in the Box", "Prolific Winger", "Classic No. 10",
                  "Hole Player", "Box-to-Box", "Anchor Man", "The Destroyer", "Extra Frontman", "Offensive Full-back",
                  "Defensive Full-back", "Target Man", "Creative Playmaker", "Build Up", "Offensive Goalkeeper",
                  "Defensive Goalkeeper", "Roaming Flank", "Cross Specialist", "Orchestrator", "Full-back Finisher"]
COM_STYLES = [(60, "Trickster"), (61, "Mazing Run"), (62, "Speeding Bullet"), (63, "Incisive Run"),
              (64, "Long Ball Expert"), (65, "Early Cross"), (66, "Long Ranger")]
SKILLS = list(zip([67, 104, 68, 69, 70, 105, 71, 72, 99, 73, 74, 98, 106, 75, 100, 101, 76, 77, 78, 79, 107, 80,
                   81, 82, 83, 95, 84, 85, 102, 86, 87, 96, 97, 88, 89, 90, 103, 91, 92, 93, 94],
                  ["Scissors Feint", "Double Touch", "Flip Flap", "Marseille Turn", "Sombrero", "Cross Over Turn",
                   "Cut Behind & Turn", "Scotch Move", "Step On Ball Control", "Heading", "Long Range Drive",
                   "Chip Shot Control", "Long Range Shooting", "Knuckle Shot", "Dipping Shot", "Rising Shots",
                   "Acrobatic Finishing", "Heel Trick", "First-time Shot", "One-touch Pass", "Through Passing",
                   "Weighted Pass", "Pinpoint Crossing", "Outside Curler", "Rabona", "No Look Pass",
                   "Low Lofted Pass", "GK Low Punt", "GK High Punt", "Long Throw", "GK Long Throw",
                   "Penalty Specialist", "GK Penalty Saver", "Gamesmanship", "Man Marking", "Track Back",
                   "Interception", "Acrobatic Clear", "Captaincy", "Super-sub", "Fighting Spirit"]))
_RANGE = {p: (lo, hi, d) for p, _, lo, hi, d in FIELDS}
_RANGE.update({p: (0, 1, 0) for p, _ in COM_STYLES + SKILLS})
NAME_BYTES, TEAM_NAME_BYTES, SHORT_BYTES = 60, 69, 9      # text + its 0 in 61 / 70 / 10 bytes


def meta():
    """What the page needs to draw the player sheet."""
    return {"abilities": [{"p": p, "name": n, "min": lo, "max": hi} for p, n, lo, hi, _ in FIELDS[:29]],
            "positions": POSITIONS, "pos_param": POS_PARAM, "styles": PLAYING_STYLES,
            "com": [{"p": p, "name": n} for p, n in COM_STYLES], "skills": [{"p": p, "name": n} for p, n in SKILLS],
            "min_squad": MIN_SQUAD, "max_squad": MAX_SQUAD, "max_national": MAX_NATIONAL}


def _param(rec, o, p):
    off, sh, w, add = PMAP[p]
    return ((struct.unpack_from("<I", rec, o + off)[0] >> sh) & ((1 << w) - 1)) + add


def _text(s, size):
    b = s.encode("utf-8")
    return b[:size].ljust(size + 1, b"\0")


# ---- what friends have now --------------------------------------------------------------------------------
def folder_of(svc):
    from . import liveupdate
    return liveupdate.folder(svc)


def base_update(folder):
    """The Live Update under ours: the newest CPK in the folder with version_wu.bin, other than edits.cpk."""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    found = []
    for n in names:
        p = os.path.join(folder, n)
        if n.lower().endswith(".cpk") and n != OUT and os.path.isfile(p):
            found.append((os.path.getmtime(p), p))
    for _, p in sorted(found, reverse=True):
        try:
            st = os.stat(p)
        except OSError:
            continue
        c = _HAS.get(p)
        if c is None or c[0] != (st.st_mtime, st.st_size):
            try:
                yes = bool(cpkread.read_file(p, pesoption.VERSION_FILE))
            except Exception:  # noqa: BLE001 - damaged: liveupdate.py says so
                yes = False
            c = _HAS[p] = ((st.st_mtime, st.st_size), yes)
        if c[1]:
            return p
    return None


_HAS = {}                    # path -> ((mtime, size), has version_wu.bin)


def _key(path):
    st = os.stat(path)
    return [os.path.basename(path), st.st_mtime, st.st_size]


def load_base(folder, cpks=None):
    """{key, path (None: the game's own data), tables {name: bytes}}: the data friends have now."""
    path = base_update(folder)
    tables = {}
    if path:
        _, rows = cpkread.toc(path)
        for r in rows:
            n = cpkread.name(r)
            if n.startswith(PDB) and n[len(PDB):] in TABLES:
                tables[n[len(PDB):]] = unwrap(cpkread.read_entry(path, r))
    missing = [n for n in TABLES if n not in tables]
    game = cpks if cpks is not None else pesoption.game_cpks()
    if missing:
        if not game:
            raise ValueError("PES 2021 isn't on this computer (its data is needed to edit it)")
        for c in game:
            if not os.path.isfile(c) or not missing:
                continue
            _, rows = cpkread.toc(c)
            by = {cpkread.name(r): r for r in rows}
            for n in list(missing):
                r = by.get(PDB + n)
                if r is not None:
                    tables[n] = unwrap(cpkread.read_entry(c, r))
                    missing.remove(n)
    if {"Player.bin", "Team.bin", "PlayerAssignment.bin"} - set(tables):
        raise ValueError("the game's data is missing its player tables")
    key = _key(path) if path else ["game"] + [_key(c)[1:] for c in game if os.path.isfile(c)]
    return {"key": key, "path": path, "tables": tables}


# ---- the changes ------------------------------------------------------------------------------------------
def empty():
    return {"players": {}, "teams": {}, "squads": {}}


def read_edits(folder):
    try:
        with open(os.path.join(folder, EDITS), encoding="utf-8") as f:
            e = json.load(f)
        return {k: dict(e.get(k) or {}) for k in ("players", "teams", "squads")}
    except (OSError, ValueError, AttributeError):
        return empty()


def write_edits(folder, e):
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, EDITS + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, sort_keys=True, indent=1)
    os.replace(tmp, os.path.join(folder, EDITS))


def digest(e):
    return hashlib.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def count(e):
    """Players and teams changed (a transfer changes two squads: two teams)."""
    return len(e["players"]) + len(set(e["teams"]) | set(e["squads"]))


def apply_edits(tables, e):
    """{Player.bin, Team.bin, PlayerAssignment.bin} with the changes made."""
    praw = bytearray(tables["Player.bin"])
    index = {struct.unpack_from("<I", praw, o + 8)[0]: o for o in range(0, len(praw) - PREC + 1, PREC)}
    for pid, c in e["players"].items():
        o = index.get(int(pid))
        if o is None:                                  # (gone from the update under it)
            continue
        if "name" in c:
            praw[o + 0xfb:o + 0x138] = _text(c["name"], NAME_BYTES)
        if "shirt" in c:
            praw[o + 0x81:o + 0xbe] = praw[o + 0xbe:o + 0xfb] = _text(c["shirt"], NAME_BYTES)
        if "nat" in c:
            _put(praw, o + 0x1c, 9, 9, int(c["nat"]))
        for p, v in (c.get("p") or {}).items():
            off, sh, w, add = PMAP[int(p)]
            lo, hi, d = _RANGE[int(p)]
            _put(praw, o + off, sh, w, (int(v) - d - add) & ((1 << w) - 1))
    traw = bytearray(tables["Team.bin"])
    tindex = {struct.unpack_from("<I", traw, o + 8)[0]: o for o in range(0, len(traw) - TREC + 1, TREC)}
    for tid, c in e["teams"].items():
        o = tindex.get(int(tid))
        if o is None:
            continue
        if c.get("name"):
            for at in (0x170, 0x5b6):                  # (English: the second one is the longer form, "UNITED STATES")
                traw[o + at:o + at + 70] = _text(c["name"], TEAM_NAME_BYTES)
        if c.get("short"):
            traw[o + 0x372:o + 0x37c] = _text(c["short"], SHORT_BYTES)
            if traw[o + 0x566]:                        # (the unlicensed short name, where there is one)
                traw[o + 0x566:o + 0x570] = _text(c["short"], SHORT_BYTES)
    return {"Player.bin": bytes(praw), "Team.bin": bytes(traw),
            "PlayerAssignment.bin": _assign(tables["PlayerAssignment.bin"], e["squads"])}


def _assign(pa, squads):
    """PlayerAssignment.bin with the given squads ({team: [[player, shirt], ...]} in squad order) in place of
    those teams' rows. A player who stays keeps his row id and roles (captain, set pieces); a new row takes the
    next id; rows keep their place in the file."""
    old, ids, base = {}, {}, {}
    for o in range(0, len(pa) - 15, 16):
        rid, pid, tid, f = struct.unpack_from("<IIII", pa, o)
        old[(tid, pid)], ids[(tid, pid)] = f, rid
        base.setdefault(tid, []).append(pid)
    sq = {int(t): v for t, v in squads.items()}
    rows = []
    for tid in list(base) + [t for t in sq if t not in base]:
        if tid not in sq:
            rows += [(pid, tid, old[(tid, pid)]) for pid in base[tid]]
            continue
        team = []
        for slot, (pid, shirt) in enumerate(sq[tid]):
            roles = old.get((tid, pid), 0) & ~0xffff
            team.append((pid, tid, roles | ((slot & 0x3f) << 10) | ((int(shirt) - 1) & 0x3ff)))
        at = {p: i for i, p in enumerate(base.get(tid, ()))}
        rows += sorted(team, key=lambda r: at.get(r[0], len(at)))
    fresh = max(ids.values(), default=0)
    out = bytearray()
    for p, t, f in rows:
        rid = ids.get((t, p))
        if rid is None:
            fresh += 1
            rid = fresh
        out += struct.pack("<IIII", rid, p, t, f)
    return bytes(out)


# ---- reading it for the page --------------------------------------------------------------------------------
class View:
    """The data friends will have with the current changes: lookups for the page."""

    def __init__(self, base, e):
        t = {**base["tables"], **apply_edits(base["tables"], e)}
        self.P, self.T, self.pa = t["Player.bin"], t["Team.bin"], t["PlayerAssignment.bin"]
        self.p_at = {struct.unpack_from("<I", self.P, o + 8)[0]: o
                     for o in range(0, len(self.P) - PREC + 1, PREC)}
        self.t_at = {struct.unpack_from("<I", self.T, o + 8)[0]: o for o in range(0, len(self.T) - TREC + 1, TREC)}
        self.squads, self.teams_of = {}, {}
        for o in range(0, len(self.pa) - 15, 16):
            _, pid, tid, f = struct.unpack_from("<IIII", self.pa, o)
            self.squads.setdefault(tid, []).append((f >> 10 & 0x3f, pid, (f & 0x3ff) + 1, f >> 21 & 1))
            self.teams_of.setdefault(pid, []).append(tid)
        for s in self.squads.values():
            s.sort()
        self.countries = {}
        c = t.get("Country.bin") or b""
        for o in range(0, len(c) - 0x58c + 1, 0x58c):
            self.countries[(struct.unpack_from("<I", c, o)[0] >> 10) & 0x1ff] = _cstr(c[o + 0x120:o + 0x166])
        self.names = [(pid, self.pname(pid)) for pid in self.p_at]
        self.folded = [(pid, _fold(n)) for pid, n in self.names]

    def pname(self, pid):
        o = self.p_at[pid]
        return _cstr(self.P[o + 0xfb:o + 0x138]) or _cstr(self.P[o + 0x44:o + 0x81])

    def tname(self, tid):
        o = self.t_at.get(tid)
        if o is None:
            return f"Team {tid}"
        return _cstr(self.T[o + 0x170:o + 0x1b6]) or _cstr(self.T[o + 0x58:o + 0x9e]) or f"Team {tid}"

    def param(self, pid, p):
        return _param(self.P, self.p_at[pid], p) + _RANGE[p][2]

    def brief(self, pid):
        tids = self.teams_of.get(pid, [])
        return {"id": pid, "name": self.pname(pid), "pos": POSITIONS[self.param(pid, 0)] if self.param(pid, 0) < 13
                else "", "age": self.param(pid, 45),
                "teams": [{"id": t, "name": self.tname(t)} for t in tids]}

    def player(self, pid):
        o = self.p_at[pid]
        d = self.brief(pid)
        d.update(shirt=_cstr(self.P[o + 0x81:o + 0xbe]),
                 nat=(struct.unpack_from("<I", self.P, o + 0x1c)[0] >> 9) & 0x1ff,
                 p={str(p): self.param(pid, p) for p in _RANGE})
        d["teams"] = [{"id": t, "name": self.tname(t), "shirt": next((s for _, q, s, _ in self.squads[t]
                                                                        if q == pid), None)}
                      for t in self.teams_of.get(pid, [])]
        return d

    def team(self, tid):
        o = self.t_at.get(tid)
        squad = []
        for slot, pid, shirt, cap in self.squads.get(tid, []):
            b = self.brief(pid) if pid in self.p_at else {"id": pid, "name": f"Player {pid}"}
            squad.append({"id": pid, "name": b["name"], "pos": b.get("pos", ""), "age": b.get("age"),
                          "shirt": shirt, "captain": bool(cap)})
        return {"id": tid, "name": self.tname(tid),
                "short": _cstr(self.T[o + 0x372:o + 0x37c]) if o is not None else "", "squad": squad}

    def find(self, q, limit=30):
        q = _fold(q.strip())
        if not q:
            return []
        hits = [(not n.startswith(q), n.find(q), pid) for pid, n in self.folded if q in n]
        hits += [(2, 0, pid) for pid in ([int(q)] if q.isdigit() and int(q) in self.p_at else [])]
        return [self.brief(pid) for _, _, pid in sorted(hits)[:limit]]


def _fold(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


# ---- the state the server keeps ------------------------------------------------------------------------------
_STATE = {}                  # folder -> {"base", "edits", "view", "view_key"}
_LOCK = threading.RLock()


def state(folder, cpks=None):
    """(base, edits, view), loading what changed. Raises ValueError when there is no data to edit."""
    with _LOCK:
        s = _STATE.setdefault(folder, {})
        path = base_update(folder)
        want = _key(path) if path else None
        b = s.get("base")
        if b is None or (b["path"] and b["key"] != want) or (b["path"] is None) != (path is None):
            s["base"] = b = load_base(folder, cpks)
            s.pop("view", None)
        e = read_edits(folder)
        k = digest(e)
        if s.get("view_key") != k or "view" not in s:
            s["view"], s["view_key"] = View(b, e), k
        return b, e, s["view"]


_PUB = {}                    # path -> ((mtime, size), summary)


def published(folder):
    """The summary inside edits.cpk (with the changes it has), or None."""
    p = os.path.join(folder, OUT)
    try:
        st = os.stat(p)
    except OSError:
        return None
    c = _PUB.get(p)
    if c is None or c[0] != (st.st_mtime, st.st_size):
        c = _PUB[p] = ((st.st_mtime, st.st_size), pesoption._summary_of(p))
    return c[1]


def status(folder):
    pub = published(folder)
    e = read_edits(folder)
    out = {"changes": count(e), "unsent": digest(e) != (pub or {}).get("edits_digest", digest(empty())),
           "sent": {k: pub[k] for k in ("version", "t", "changes") if k in pub} if pub else None}
    path = base_update(folder)
    out["base"] = os.path.basename(path) if path else None
    return out


# ---- changes from the page ---------------------------------------------------------------------------------
def set_player(folder, pid, ch, cpks=None):
    """ch: {name?, shirt?, nat?, p?: {param: shown value}} -> the changes; an unchanged value is dropped."""
    with _LOCK:
        b, e, v = state(folder, cpks)
        pid = int(pid)
        if pid not in v.p_at:
            raise KeyError("player")
        cur = dict(e["players"].get(str(pid)) or {})
        orig = _orig_player(b, pid)
        for k, size in (("name", NAME_BYTES), ("shirt", NAME_BYTES)):
            if k in ch:
                s = str(ch[k]).strip()
                if not s:
                    raise ValueError(f"the {'name' if k == 'name' else 'shirt name'} can't be empty")
                if len(s.encode()) > size:
                    raise ValueError(f"the {'name' if k == 'name' else 'shirt name'} is too long")
                cur[k] = s
        if "nat" in ch:
            nat = int(ch["nat"])
            if nat not in v.countries and nat != orig["nat"]:
                raise ValueError("unknown nationality")
            cur["nat"] = nat
        ps = dict(cur.get("p") or {})
        for p, val in (ch.get("p") or {}).items():
            p, val = int(p), int(val)
            if p not in _RANGE:
                raise ValueError(f"unknown field {p}")
            lo, hi, _ = _RANGE[p]
            if not lo <= val <= hi:
                raise ValueError(f"{_label(p)} must be {lo} to {hi}")
            ps[str(p)] = val
        reg = int(ps.get("0", orig["p"]["0"]))
        if int(ps.get(str(POS_PARAM[POSITIONS[reg]]), orig["p"][str(POS_PARAM[POSITIONS[reg]])])) != 2:
            ps[str(POS_PARAM[POSITIONS[reg]])] = 2           # (the registered position is always a best one)
        cur["p"] = {p: x for p, x in ps.items() if orig["p"].get(p) != x}
        for k in ("name", "shirt", "nat"):
            if k in cur and cur[k] == orig[k]:
                del cur[k]
        if not cur["p"]:
            del cur["p"]
        if cur:
            e["players"][str(pid)] = cur
        else:
            e["players"].pop(str(pid), None)
        write_edits(folder, e)
        return state(folder, cpks)[2].player(pid)


def _orig_player(b, pid):
    o = None
    P = b["tables"]["Player.bin"]
    for q in range(0, len(P) - PREC + 1, PREC):
        if struct.unpack_from("<I", P, q + 8)[0] == pid:
            o = q
            break
    if o is None:                                      # (a player the update under it doesn't have)
        return {"name": None, "shirt": None, "nat": None, "p": {}}
    return {"name": _cstr(P[o + 0xfb:o + 0x138]), "shirt": _cstr(P[o + 0x81:o + 0xbe]),
            "nat": (struct.unpack_from("<I", P, o + 0x1c)[0] >> 9) & 0x1ff,
            "p": {str(p): _param(P, o, p) + _RANGE[p][2] for p in _RANGE}}


def _label(p):
    for q, n, *_ in FIELDS:
        if q == p:
            return n
    return next((n for q, n in COM_STYLES + SKILLS if q == p), str(p))


def set_team(folder, tid, ch, cpks=None):
    """ch: {name?, short?, squad?: [[player, shirt], ...] in order (the first 11 start)}. A player added to a club
    leaves his other club; one added to a national team leaves his other national team."""
    with _LOCK:
        b, e, v = state(folder, cpks)
        tid = int(tid)
        if tid not in v.t_at:
            raise KeyError("team")
        cur = dict(e["teams"].get(str(tid)) or {})
        for k, size, what in (("name", TEAM_NAME_BYTES, "name"), ("short", SHORT_BYTES, "short name")):
            if k in ch:
                s = str(ch[k]).strip()
                if not s:
                    raise ValueError(f"the {what} can't be empty")
                if len(s.encode()) > size:
                    raise ValueError(f"the {what} is too long")
                cur[k] = s
        base_t = _orig_team(b, tid)
        for k in ("name", "short"):
            if cur.get(k) == base_t[k]:
                cur.pop(k, None)
        if cur:
            e["teams"][str(tid)] = cur
        else:
            e["teams"].pop(str(tid), None)
        if "squad" in ch:
            _set_squad(b, e, v, tid, ch["squad"])
        write_edits(folder, e)
        return state(folder, cpks)[2].team(tid)


def _orig_team(b, tid):
    T = b["tables"]["Team.bin"]
    for o in range(0, len(T) - TREC + 1, TREC):
        if struct.unpack_from("<I", T, o + 8)[0] == tid:
            return {"name": _cstr(T[o + 0x170:o + 0x1b6]), "short": _cstr(T[o + 0x372:o + 0x37c])}
    return {"name": None, "short": None}


def national():
    from . import adminextra
    return {t for g in adminextra.teams().get("national", []) for t in g["teams"]}


def _set_squad(b, e, v, tid, squad):
    nat = national()
    lst, seen, shirts = [], set(), set()
    for item in squad:
        pid, shirt = int(item[0]), int(item[1])
        if pid not in v.p_at:
            raise ValueError(f"there's no player {pid}")
        if pid in seen:
            raise ValueError(f"{v.pname(pid)} is in the squad twice")
        if not 1 <= shirt <= 99:
            raise ValueError("shirt numbers are 1 to 99")
        if shirt in shirts:
            raise ValueError(f"two players have number {shirt}")
        seen.add(pid)
        shirts.add(shirt)
        lst.append([pid, shirt])
    top = MAX_NATIONAL if tid in nat else MAX_SQUAD
    if not MIN_SQUAD <= len(lst) <= top:
        raise ValueError(f"a squad has {MIN_SQUAD} to {top} players")
    squads = {t: [[pid, shirt] for _, pid, shirt, _ in s] for t, s in v.squads.items()}
    before = {pid for pid, _ in squads.get(tid, [])}
    squads[tid] = lst
    changed = {tid}
    for pid, _ in lst:                                 # a new player leaves his other club (or national team)
        if pid in before:
            continue
        for other in v.teams_of.get(pid, []):
            if other != tid and (other in nat) == (tid in nat):
                squads[other] = [x for x in squads[other] if x[0] != pid]
                if len(squads[other]) < MIN_SQUAD:
                    raise ValueError(f"{v.tname(other)} would be left with {len(squads[other])} players "
                                     f"(at least {MIN_SQUAD})")
                changed.add(other)
    base_pa = _squads_of(b["tables"]["PlayerAssignment.bin"])
    for t in changed:                                  # (a squad back as it was is no change)
        if base_pa.get(t) == squads[t]:
            e["squads"].pop(str(t), None)
        else:
            e["squads"][str(t)] = squads[t]


def _squads_of(pa):
    out = {}
    for o in range(0, len(pa) - 15, 16):
        _, pid, tid, f = struct.unpack_from("<IIII", pa, o)
        out.setdefault(tid, []).append((f >> 10 & 0x3f, pid, (f & 0x3ff) + 1))
    return {t: [[pid, shirt] for _, pid, shirt in sorted(s)] for t, s in out.items()}


def undo(folder, kind, ident):
    """Drop the changes of one player ('player') or one team (name and squad: 'team'), or all ('all')."""
    with _LOCK:
        e = read_edits(folder)
        if kind == "all":
            e = empty()
        elif kind == "player":
            e["players"].pop(str(int(ident)), None)
        elif kind == "team":
            e["teams"].pop(str(int(ident)), None)
            e["squads"].pop(str(int(ident)), None)
        else:
            raise ValueError("unknown")
        write_edits(folder, e)
        return e


def take_back(folder):
    """The changes back to what was sent (or none)."""
    with _LOCK:
        pub = published(folder)
        e = (pub or {}).get("edits") or empty()
        write_edits(folder, {k: dict(e.get(k) or {}) for k in ("players", "teams", "squads")})


# ---- sending ------------------------------------------------------------------------------------------------
def build(folder, cpks=None, e=None):
    """edits.cpk from the update under it (or the game's data) and the changes e (default: edits.json)."""
    with _LOCK:
        b, cur, _ = state(folder, cpks)
        e = cur if e is None else e
        changed = apply_edits(b["tables"], e)
        files, slices = {}, []
        if b["path"]:                                  # the update under it: all its files, the 3 tables replaced
            _, rows = cpkread.toc(b["path"])
            skip = {pesoption.VERSION_FILE, pesoption.SUMMARY}
            for r in rows:
                n = cpkread.name(r)
                if not n or n.lower() in skip or (n.startswith(PDB) and n[len(PDB):] in changed):
                    continue
                if r.get("FileSize"):
                    slices.append((n, (b["path"], r["_abs"], r["FileSize"]), r["ExtractSize"]))
        files = {PDB + n: wrap(blob) for n, blob in changed.items()}
        prev = max(pesoption._high(folder), _base_version(b["path"]))
        ver = pesoption.version_for(time.time(), prev)
        summary = {"kind": "edits", "version": ver, "t": time.time(), "changes": count(e), "edits": e,
                   "edits_digest": digest(e), "base": b["key"]}
        out = os.path.join(folder, OUT)
        pesoption._write(out, files, summary, "edits", slices=slices)
        with open(os.path.join(folder, pesoption.HIGH), "w") as f:
            f.write(str(int(ver.replace(".", ""))))
        log.info("game data changes sent: %s (version %s)", summary["changes"], ver)
        return summary


def _base_version(path):
    if not path:
        return 0
    try:
        raw = cpkread.read_file(path, pesoption.VERSION_FILE) or b""
        return int(raw[:10].split(b"\0", 1)[0].decode("ascii").replace(".", "") or 0)
    except (ValueError, OSError, UnicodeDecodeError):
        return 0


def refresh(folder, cpks=None):
    """Called by liveupdate's thread: when the update under edits.cpk changed (a new option file, patch...),
    build edits.cpk again on top of it with the changes that were sent."""
    pub = published(folder)
    if not pub:
        return None
    path = base_update(folder)
    try:
        key = _key(path) if path else None
    except OSError:
        return None
    if path is None and pub.get("base", [None])[0] == "game":
        return None
    if key == pub.get("base"):
        return None
    try:
        return build(folder, cpks, e=pub.get("edits") or empty())
    except Exception as ex:  # noqa: BLE001 - say so in the log; the old one stays offered
        log.warning("game data changes not built again: %s", ex)
        return None


# ---- the admin page's calls (POST /admin/api/data/...) ------------------------------------------------------
def _groups(v):
    """Teams to browse: the game's leagues and national teams (web/teams.json), then the other teams."""
    from . import adminextra
    t = adminextra.teams()
    out, seen = [], set()
    for kind, groups in (("club", t.get("clubs", [])), ("national", t.get("national", []))):
        for g in groups:
            ids = [i for i in g["teams"] if i in v.squads and i not in seen]
            seen.update(ids)
            if ids:
                out.append({"name": g["region"], "kind": kind,
                            "teams": [[i, v.tname(i), len(v.squads[i])] for i in ids]})
    rest = sorted((i for i in v.squads if i not in seen and i in v.t_at and len(v.squads[i]) >= MIN_SQUAD),
                  key=v.tname)
    if rest:
        out.append({"name": "Other teams", "kind": "other", "teams": [[i, v.tname(i), len(v.squads[i])] for i in rest]})
    return out


def admin_api(folder, route, body, cpks=None):
    """(status, reply): the page's calls. Replies carry the status line's numbers (st)."""
    req = json.loads(body or b"{}")
    if not isinstance(req, dict):
        raise ValueError("bad request")
    what = route[len("/admin/api/data"):]
    try:
        if what == "/take-back":
            take_back(folder)
        elif what == "/undo":
            undo(folder, req.get("kind"), req.get("id", 0))
        b, e, v = state(folder, cpks)
        out = {}
        if what in ("", "/info"):
            out = {"meta": meta(), "groups": _groups(v),
                   "countries": sorted(([i, n] for i, n in v.countries.items() if n), key=lambda x: _fold(x[1]))}
        elif what == "/find":
            q = str(req.get("q") or "")
            fq = _fold(q.strip())
            teams = sorted((i for i in v.squads if i in v.t_at and fq and fq in _fold(v.tname(i))),
                           key=lambda i: (not _fold(v.tname(i)).startswith(fq), v.tname(i)))[:12]
            out = {"players": v.find(q), "teams": [[i, v.tname(i), len(v.squads[i])] for i in teams]}
        elif what == "/player":
            out = {"player": v.player(int(req["id"]))}
        elif what == "/player/save":
            out = {"player": set_player(folder, req["id"], req, cpks)}
        elif what == "/team":
            out = {"team": v.team(int(req["id"]))}
        elif what == "/team/save":
            out = {"team": set_team(folder, req["id"], req, cpks)}
        elif what == "/send":
            s = build(folder, cpks)
            out = {"sent": {"version": s["version"]}}
        elif what not in ("/undo", "/take-back"):
            return "404 Not Found", {"error": "unknown"}
        _, e, v = state(folder, cpks)
        out["st"] = {**status(folder), "edited": {
            "players": [[p, v.pname(p)] for p in sorted(int(p) for p in e["players"]) if p in v.p_at],
            "teams": [[t, v.tname(t)] for t in sorted({int(t) for t in e["teams"]} | {int(t) for t in e["squads"]})]}}
        return "200 OK", out
    except KeyError as ex:
        return "404 Not Found", {"error": f"no such {ex.args[0] if ex.args and ex.args[0] in ('player', 'team') else 'thing'}"}
