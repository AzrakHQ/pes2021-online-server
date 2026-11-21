"""Cups: a knockout, with an optional group stage first, inside an Online Tournament competition (pestourney).

A competition whose rules say format "cup" is a cup. The game has no bracket screen online (its bracket menus
belong to the offline modes; notes/re/functions/tourney.md), so the server keeps the bracket and
- pairs a searching player only with the opponent of one of their open ties (pesmatch asks can_pair),
- shows the draw, the tables and the results in the game's Competition Outline text, in the Rankings order and
  on the admin page,
- ends the competition when the final is decided, which brings up the game's own Competition Results notice.

Stages: entry (friends enter with a team) -> the host makes the draw on the admin page -> groups (optional:
everyone plays everyone in their group once or twice, the top one or two go through, and with rules fill the
best of the next place too, up to a full knockout of 4, 8, 16 ...) -> knockout -> done.
Group rules (pestourney.save): groups (the group size, 0 = a knockout only), qualify (1 or 2), fill, legs (1 or 2).
With rules third_place the two beaten semi-finalists play a third-place match (stage "T"); the cup is done when
that and the final are both decided.
Every cup match goes to extra time and penalties when it is level (pestourney.regulation; room["pk"] is the
shootout's score), so every match has a winner: three points in a group, the next round in the knockout. A match
that still ends level (a game without the cup's rules, pestourney.host_first) is a draw in a group and is played
again in the knockout.
A player the host deletes stays in the draw as a negative number (-pid, "a deleted player"): every tie they are
in, now or later, goes to the opponent, and they do not go through from a group.
"""
import logging, random, time

from .accounts import once
from .pescmds import _db

log = logging.getLogger("pesserver.cup")
GROUPS_FROM = 6                     # a cup without group rules offers groups (of 4) from this many players
QUALIFY = 2                         # per group, unless the rules say 1
GROUP_SIZES = (3, 4, 5, 6)
WALKOVER = (3, 0)
COLS = ("fid", "stage", "grp", "round", "slot", "a", "b", "a_goals", "b_goals", "a_pk", "b_pk", "winner", "how",
        "match_id", "t", "replays")


@once
def _setup(db):
    # stage: G a group match, K a knockout tie, T the third-place match.
    # winner: NULL = not decided, 0 = drawn (groups only), else the pid. how: played / walkover / bye.
    db.execute("""CREATE TABLE IF NOT EXISTS pes_cup_fixture (compe_id INTEGER, fid INTEGER, stage TEXT NOT NULL,
                  grp INTEGER NOT NULL DEFAULT 0, round INTEGER NOT NULL DEFAULT 0, slot INTEGER NOT NULL DEFAULT 0,
                  a INTEGER, b INTEGER, a_goals INTEGER, b_goals INTEGER, a_pk INTEGER, b_pk INTEGER,
                  winner INTEGER, how TEXT, match_id TEXT, t REAL, replays INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (compe_id, fid))""")


def is_cup(c):
    return bool(c) and (c.get("rules") or {}).get("format") == "cup"


def third_place(c):
    return is_cup(c) and bool((c.get("rules") or {}).get("third_place"))


def _rows(db, cid, where="", args=()):
    cur = db.execute(f"SELECT {', '.join(COLS)} FROM pes_cup_fixture WHERE compe_id=? {where} "
                     "ORDER BY stage, round, grp, slot, fid", (cid, *args))
    return [dict(zip(COLS, r)) for r in cur.fetchall()]


def fixtures(svc, cid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        return _rows(db, cid)


def _stage(fx):
    if not fx:
        return "entry"
    ko = [f for f in fx if f["stage"] == "K"]
    if not ko:
        return "groups"
    last = [max(ko, key=lambda f: f["round"])] + [f for f in fx if f["stage"] == "T"]
    return "done" if all(f["winner"] is not None for f in last) else "knockout"


def stage(svc, cid):
    return _stage(fixtures(svc, cid))


def group_rules(c):
    """(size, qualify, fill, legs) from the competition's rules; size 0: no group stage planned."""
    r = (c or {}).get("rules") or {}
    return int(r.get("groups") or 0), int(r.get("qualify") or QUALIFY), bool(r.get("fill")), int(r.get("legs") or 1)


def _pow2(n):
    size = 2
    while size < n:
        size *= 2
    return size


def plan(n, size, qualify=QUALIFY, fill=False, legs=1):
    """How n players split into groups of about `size`: {groups, sizes, qualify, extra (the best of the next
    place who go through too), through, matches}. Fewer groups when a group would be too small to leave anyone
    out; ValueError when there are too few players for groups at all."""
    g = max(1, -(-n // size))
    while g > 1 and n // g <= qualify:
        g -= 1
    if n // g <= qualify:
        raise ValueError(f"groups need at least {qualify + 1} players")
    base = g * qualify
    extra = min(_pow2(base) - base, g) if fill else 0
    if base + extra < 2:
        raise ValueError("only one player would go through: let the best two go through, or no groups")
    sizes = [len(range(i, n, g)) for i in range(g)]
    return {"groups": g, "sizes": sizes, "qualify": qualify, "extra": extra, "through": base + extra,
            "matches": sum(k * (k - 1) // 2 for k in sizes) * legs, "legs": legs}


def round_name(ties):
    return {1: "Final", 2: "Semi-finals", 4: "Quarter-finals"}.get(ties, f"Round of {ties * 2}")


def group_name(i):
    return "Group " + (chr(ord("A") + i) if i < 26 else str(i + 1))


# ---- the draw -----------------------------------------------------------------------------------------------
def _seed_order(size):
    """Bracket places for seeds 1..size: 1 meets the last, and 1 and 2 can only meet in the final."""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def _advance(db, cid, f, winner):
    """The winner of a knockout tie takes their place in the next round."""
    nxt = db.execute("SELECT fid FROM pes_cup_fixture WHERE compe_id=? AND stage='K' AND round=? AND slot=?",
                     (cid, f["round"] + 1, f["slot"] // 2)).fetchone()
    if nxt:
        db.execute(f"UPDATE pes_cup_fixture SET {'a' if f['slot'] % 2 == 0 else 'b'}=? WHERE compe_id=? AND fid=?",
                   (winner, cid, nxt[0]))


def _make_knockout(db, cid, seeds, groups=None, third=False):
    """seeds: pids, best first. groups {pid: group}: first-round ties between two of the same group are avoided.
    third: with four players or more (two semi-finals that are played), a third-place match for their losers."""
    n = len(seeds)
    size = 2
    while size < n:
        size *= 2
    order = _seed_order(size)
    pairs = [[seeds[order[2 * s] - 1] if order[2 * s] <= n else None,
              seeds[order[2 * s + 1] - 1] if order[2 * s + 1] <= n else None] for s in range(size // 2)]
    if groups:
        clash = lambda p: p[0] is not None and p[1] is not None and groups.get(p[0]) == groups.get(p[1])  # noqa: E731
        for p in pairs:
            if not clash(p):
                continue
            for q in pairs:
                if q is p or q[1] is None:
                    continue
                if not clash([p[0], q[1]]) and not clash([q[0], p[1]]):
                    p[1], q[1] = q[1], p[1]
                    break
    fid = (db.execute("SELECT MAX(fid) FROM pes_cup_fixture WHERE compe_id=?", (cid,)).fetchone()[0] or 0) + 1
    rnd, ties = 1, size // 2
    while ties >= 1:
        for s in range(ties):
            a, b = pairs[s] if rnd == 1 else (None, None)
            db.execute("INSERT INTO pes_cup_fixture (compe_id, fid, stage, round, slot, a, b) VALUES (?,?,'K',?,?,?,?)",
                       (cid, fid, rnd, s, a, b))
            fid += 1
        rnd, ties = rnd + 1, ties // 2
    if third and n >= 4:
        db.execute("INSERT INTO pes_cup_fixture (compe_id, fid, stage, round, slot) VALUES (?,?,'T',?,0)", (cid, fid, rnd - 1))
    now = time.time()
    for f in _rows(db, cid, "AND stage='K' AND round=1"):
        if (f["a"] is None) != (f["b"] is None):                 # a bye
            w = f["a"] if f["a"] is not None else f["b"]
            db.execute("UPDATE pes_cup_fixture SET winner=?, how='bye', t=? WHERE compe_id=? AND fid=?",
                       (w, now, cid, f["fid"]))
            _advance(db, cid, f, w)


def group_count(n, size=4, qualify=QUALIFY):
    return plan(n, size, qualify)["groups"]


def draw(svc, c, groups=None, rng=None):
    """Close the entry and make the draw. groups: None = as the rules say; True = a group stage first (the rules'
    group size, or 4); False = a knockout only."""
    cid = c["compe_id"]
    rng = rng or random.Random()
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if db.execute("SELECT 1 FROM pes_cup_fixture WHERE compe_id=?", (cid,)).fetchone():
            raise ValueError("the draw is already made")
        pids = [r[0] for r in db.execute("SELECT pid FROM pes_tourney_entry WHERE compe_id=? ORDER BY joined, pid", (cid,))]
        if len(pids) < 2:
            raise ValueError("a cup needs at least 2 players")
        size, qualify, fill, legs = group_rules(c)
        if groups is None:
            groups = size > 0
        if groups:
            g = plan(len(pids), size or 4, qualify, fill, legs)["groups"]
        rng.shuffle(pids)
        if groups:
            fid = 1
            for i in range(g):
                members = pids[i::g]
                slot = 0
                for leg in range(legs):                    # the second time round, the other way round
                    for x in range(len(members)):
                        for y in range(x + 1, len(members)):
                            a, b = (members[x], members[y]) if leg == 0 else (members[y], members[x])
                            db.execute("INSERT INTO pes_cup_fixture (compe_id, fid, stage, grp, slot, a, b) "
                                       "VALUES (?,?,'G',?,?,?,?)", (cid, fid, i, slot, a, b))
                            fid, slot = fid + 1, slot + 1
        else:
            _make_knockout(db, cid, pids, third=third_place(c))
    log.info("CUP %d drawn: %d players, %s", cid, len(pids), "groups" if groups else "knockout")
    _finish(svc, c, [(f"{c['name']}: the draw is made ({len(pids)} players, "
                      f"{'groups first' if groups else 'knockout'})", "match", pids)], False)


def undraw(svc, c):
    """Take the draw back (entry opens again); only while no match of it has been played."""
    cid = c["compe_id"]
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        if db.execute("SELECT 1 FROM pes_cup_fixture WHERE compe_id=? AND (how IN ('played','walkover') OR replays>0)",
                      (cid,)).fetchone():
            raise ValueError("matches have been played: the draw stays")
        db.execute("DELETE FROM pes_cup_fixture WHERE compe_id=?", (cid,))


def delete(db, cid):
    _setup(db)
    db.execute("DELETE FROM pes_cup_fixture WHERE compe_id=?", (cid,))


# ---- pairing ------------------------------------------------------------------------------------------------
def _open_tie(db, cid, a, b):
    r = _rows(db, cid, "AND winner IS NULL AND ((a=? AND b=?) OR (a=? AND b=?))", (a, b, b, a))
    return r[0] if r else None


def can_pair(svc, cid, a, b):
    """Two searching players of a cup are paired only when they have a tie to play against each other."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        return _open_tie(db, cid, a, b) is not None


def to_play(svc, cid, pid):
    """The opponents the player can play now (open ties with both players known)."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = _rows(db, cid, "AND winner IS NULL AND a IS NOT NULL AND b IS NOT NULL AND (a=? OR b=?)", (pid, pid))
    return [f["b"] if f["a"] == pid else f["a"] for f in rows]


def out(svc, cid, pid):
    """True when the draw is made and the player has nothing left to play in the cup: not in the draw, knocked
    out, or the cup is over. (False while they may still get a match: before the draw, or between ties.)"""
    fx = fixtures(svc, cid)
    if not fx or any(f["winner"] is None and pid in (f["a"], f["b"]) for f in fx):
        return False
    if _stage(fx) == "groups":                            # played all their group matches: may still go through
        return not any(pid in (f["a"], f["b"]) for f in fx)
    return True


def _wake(svc):
    """A new round: friends already searching may now have an opponent."""
    try:
        from . import pesmatch
        pesmatch._try_pair(svc)
        pesmatch.cup_sweep(svc)                           # ... or nothing left to play
    except Exception:  # noqa: BLE001 - the draw itself stands
        log.exception("pairing after a cup change")


# ---- tables -------------------------------------------------------------------------------------------------
def _tables(fx):
    """[(group, [row])] from the group fixtures; row: pid, played, win, draw, lose, gf, ga, pts; best first."""
    groups = {}
    for f in fx:
        if f["stage"] != "G":
            continue
        t = groups.setdefault(f["grp"], {})
        for pid in (f["a"], f["b"]):
            t.setdefault(pid, {"pid": pid, "played": 0, "win": 0, "draw": 0, "lose": 0, "gf": 0, "ga": 0, "pts": 0})
        if f["winner"] is None:
            continue
        for me, opp, gf, ga in ((f["a"], f["b"], f["a_goals"], f["b_goals"]), (f["b"], f["a"], f["b_goals"], f["a_goals"])):
            r = t[me]
            r["played"] += 1; r["gf"] += gf or 0; r["ga"] += ga or 0
            if f["winner"] == me:
                r["win"] += 1; r["pts"] += 3
            elif f["winner"] == opp:
                r["lose"] += 1
            else:
                r["draw"] += 1; r["pts"] += 1
    key = lambda r: (-r["pts"], -(r["gf"] - r["ga"]), -r["gf"], r["pid"])     # noqa: E731
    return [(g, sorted(t.values(), key=key)) for g, t in sorted(groups.items())]


def _across(r):
    """Players of different groups compared (seeding, the best third-placed): per match played, as groups may
    differ in size."""
    p = max(r["played"], 1)
    return (-r["pts"] / p, -(r["gf"] - r["ga"]) / p, -r["gf"] / p, r["pid"])


def _extra(fx, qualify, fill):
    """How many of the next place go through as well (plan's extra, from the groups as drawn)."""
    if not fill:
        return 0
    g = len({f["grp"] for f in fx if f["stage"] == "G"})
    return min(_pow2(g * qualify) - g * qualify, g)


def _qualifiers(fx, qualify=QUALIFY, fill=False):
    """(seeds best first, {pid: group}): group winners in order of their records, then the runners-up, then the
    best of the next place (fill)."""
    tables = [(g, [r for r in t if r["pid"] > 0]) for g, t in _tables(fx)]     # (a deleted player: the next one)
    seeds, groups = [], {}
    extra = _extra(fx, qualify, fill)
    for place in range(qualify + (1 if extra else 0)):
        rows = sorted([t[place] for _, t in tables if len(t) > place], key=_across)
        if place == qualify:
            rows = rows[:extra]
        seeds += [r["pid"] for r in rows]
        for r in rows:
            groups[r["pid"]] = next(g for g, t in tables if r in t)
    return seeds, groups


def reach(fx):
    """{pid: how far they got in the knockout}: the round they last played in, one more for the cup's winner,
    half a round more for the winner of the third-place match."""
    out = {}
    last = max((f["round"] for f in fx if f["stage"] == "K"), default=0)
    for f in fx:
        if f["stage"] != "K":
            continue
        for pid in (f["a"], f["b"]):
            if pid is not None:
                out[pid] = max(out.get(pid, 0), f["round"])
        if f["round"] == last and f["winner"]:
            out[f["winner"]] = last + 1
    for f in fx:
        if f["stage"] == "T" and f["winner"]:
            out[f["winner"]] = last - 0.5
    return out


def order(svc, cid, rows):
    """pestourney.standings rows (rank, pid, …) in cup order: knockout progress first, then the points order."""
    r = reach(fixtures(svc, cid))
    rows = sorted(rows, key=lambda x: (-r.get(x[1], 0), x[0]))
    return [(i, *x[1:]) for i, x in enumerate(rows, 1)]


# ---- results ------------------------------------------------------------------------------------------------
def _progress(db, c, f, winner, lines, names):
    """After a tie is decided: the next knockout round, the knockout after the groups, or the end."""
    cid = c["compe_id"]
    if f["stage"] == "T":
        lines.append((f"{names(winner)} took third place in {c['name']}", "result", [winner]))
        return _stage(_rows(db, cid)) == "done"
    if f["stage"] == "K":
        _advance(db, cid, f, winner)
        nxt = _rows(db, cid, "AND stage='K' AND round=? AND slot=?", (f["round"] + 1, f["slot"] // 2))
        if not nxt:
            lines.append((f"{names(winner)} won {c['name']}", "result", [winner]))
            return _stage(_rows(db, cid)) == "done"
        n = nxt[0]
        if n["a"] is not None and n["b"] is not None:
            ties = db.execute("SELECT COUNT(*) FROM pes_cup_fixture WHERE compe_id=? AND stage='K' AND round=?",
                              (cid, n["round"])).fetchone()[0]
            lines.append((f"{c['name']}, {round_name(ties).lower().rstrip('s')}: {names(n['a'])} v {names(n['b'])}",
                          "match", [n["a"], n["b"]]))
        third = _rows(db, cid, "AND stage='T' AND round=?", (f["round"] + 1,))
        if third:                                         # a semi-final: its loser plays for third place
            t = third[0]
            loser = f["b"] if winner == f["a"] else f["a"]
            db.execute(f"UPDATE pes_cup_fixture SET {'a' if f['slot'] % 2 == 0 else 'b'}=? WHERE compe_id=? AND fid=?",
                       (loser, cid, t["fid"]))
            other = t["b"] if f["slot"] % 2 == 0 else t["a"]
            if other is not None:
                a, b = (loser, other) if f["slot"] % 2 == 0 else (other, loser)
                lines.append((f"{c['name']}, third place: {names(a)} v {names(b)}", "match", [a, b]))
        return False
    fx = _rows(db, cid)
    if all(x["winner"] is not None for x in fx if x["stage"] == "G") and not any(x["stage"] == "K" for x in fx):
        _, qualify, fill, _ = group_rules(c)
        seeds, groups = _qualifiers(fx, qualify, fill)
        if seeds:
            _make_knockout(db, cid, seeds, groups, third=third_place(c))
            lines.append((f"{c['name']}: the groups are finished. Through to the knockout: "
                          + ", ".join(names(p) for p in seeds), "match", seeds))
    return False


def _award(db, cid, f, winner):
    """The tie goes to `winner` 3-0 without being played; it counts in the table the game shows (Rankings, the
    competition's menu) like a played 3-0."""
    loser = f["b"] if winner == f["a"] else f["a"]
    ag, bg = WALKOVER if winner == f["a"] else WALKOVER[::-1]
    db.execute("UPDATE pes_cup_fixture SET a_goals=?, b_goals=?, winner=?, how='walkover', t=? WHERE compe_id=? AND fid=?",
               (ag, bg, winner, time.time(), cid, f["fid"]))
    for pid, col, pts, gf, ga in ((winner, "win", 3, WALKOVER[0], WALKOVER[1]), (loser, "lose", 0, WALKOVER[1], WALKOVER[0])):
        db.execute(f"UPDATE pes_tourney_entry SET points=points+?, {col}={col}+1, goals_for=goals_for+?, "
                   "goals_against=goals_against+? WHERE compe_id=? AND pid=?", (pts, gf, ga, cid, pid))


def _after(db, c, f, winner, lines, names):
    """A tie was decided: what follows from it (_progress), then every tie that now has a deleted player in it
    goes to the opponent. Returns True when the cup is over."""
    cid = c["compe_id"]
    _progress(db, c, f, winner, lines, names)
    for _ in range(1000):
        gone = _rows(db, cid, "AND winner IS NULL AND a IS NOT NULL AND b IS NOT NULL AND (a<0 OR b<0)")
        if not gone:
            break
        g = gone[0]
        w = g["a"] if g["b"] < 0 else g["b"]
        _award(db, cid, g, w)
        _progress(db, c, g, w, lines, names)
    return _stage(_rows(db, cid)) == "done"


def _finish(svc, c, lines, done):
    """The activity lines of a cup change (also to Discord, with the result notices), the end, the next pairings."""
    from . import events
    for text, kind, pids in lines:
        events.feed(svc, text, kind, pids)
        if kind != "info":                                # (info: the host's own calls)
            try:
                from . import pesdiscord
                pesdiscord.cup(svc, text)
            except Exception:  # noqa: BLE001 - a notification must never break the cup
                log.exception("discord cup notice")
    if done:
        from . import pestourney
        pestourney.end_now(svc, c["compe_id"])
    _wake(svc)


GONE = "a deleted player"


def _names(svc):
    from . import events
    return lambda pid: GONE if pid is not None and pid < 0 else events.name_of(svc, pid)


def result(svc, c, home, away, home_goals, away_goals, match_id, pk=None):
    """A finished cup match (pestourney.record, once per match). pk: {"HOME": n, "AWAY": n} after a shootout."""
    cid = c["compe_id"]
    lines, done, names = [], False, _names(svc)
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        f = _open_tie(db, cid, home, away)
        if f is None or f["match_id"] == match_id:
            return
        ag, bg = (home_goals, away_goals) if f["a"] == home else (away_goals, home_goals)
        apk, bpk = ((pk or {}).get("HOME"), (pk or {}).get("AWAY")) if f["a"] == home else ((pk or {}).get("AWAY"), (pk or {}).get("HOME"))
        if ag != bg:
            winner = f["a"] if ag > bg else f["b"]
        elif apk is not None and bpk is not None and apk != bpk:
            winner = f["a"] if apk > bpk else f["b"]
        elif f["stage"] == "G":
            winner, apk, bpk = 0, None, None
        else:                                             # a knockout tie that ended level: played again
            db.execute("UPDATE pes_cup_fixture SET replays=replays+1, match_id=? WHERE compe_id=? AND fid=?",
                       (match_id, cid, f["fid"]))
            lines.append((f"{c['name']}: {names(f['a'])} and {names(f['b'])} drew {ag}–{bg}. They play it again",
                          "match", [f["a"], f["b"]]))
            winner = None
        if winner is not None:
            db.execute("UPDATE pes_cup_fixture SET a_goals=?, b_goals=?, a_pk=?, b_pk=?, winner=?, how='played', "
                       "match_id=?, t=? WHERE compe_id=? AND fid=?",
                       (ag, bg, apk, bpk, winner, match_id, time.time(), cid, f["fid"]))
            done = _after(db, c, f, winner, lines, names)
    _finish(svc, c, lines, done)


def walkover(svc, c, fid, winner):
    """The host awards an open tie (a friend who never shows up): a 3-0 win."""
    cid = c["compe_id"]
    lines, names = [], _names(svc)
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        try:
            fid, winner = int(fid), int(winner)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("bad tie")
        r = _rows(db, cid, "AND fid=?", (fid,))
        if not r:
            raise KeyError("no such tie")
        f = r[0]
        if f["winner"] is not None or f["a"] is None or f["b"] is None or winner not in (f["a"], f["b"]):
            raise ValueError("that tie can't be awarded")
        _award(db, cid, f, winner)
        loser = f["b"] if winner == f["a"] else f["a"]
        lines.append((f"{c['name']}: the admin awarded {names(winner)} the tie against {names(loser)}", "info",
                      [winner, loser]))
        done = _after(db, c, f, winner, lines, names)
    _finish(svc, c, lines, done)


def forget(svc, pid):
    """A deleted player (pestourney.forget calls this first): in every draw they become -pid at once, so they do
    not go through from a group and the number, which a new player may get, no longer points at them; then every
    open tie with a deleted player goes to the opponent (_after: also ties they would reach later)."""
    from . import pestourney
    if not isinstance(pid, int) or pid <= 0:
        return
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        cids = [r[0] for r in db.execute("SELECT DISTINCT compe_id FROM pes_cup_fixture WHERE a=? OR b=? OR winner=?",
                                         (pid, pid, pid))]
        for col in ("a", "b", "winner"):          # (before any walkover: a group it finishes must not seed them)
            db.execute(f"UPDATE pes_cup_fixture SET {col}=? WHERE {col}=?", (-pid, pid))
    names = _names(svc)
    for cid in cids:
        c = pestourney.get(svc, cid)
        lines, done = [], False
        with svc.accounts.lock, db:
            if c is None or _stage(_rows(db, cid)) == "done":
                continue
            g = _rows(db, cid, "AND winner IS NULL AND a IS NOT NULL AND b IS NOT NULL AND (a=? OR b=?)", (-pid, -pid))
            for f in g:
                w = f["b"] if f["a"] == -pid else f["a"]
                if w is not None and w > 0:
                    lines.append((f"{c['name']}: {names(w)} was given the tie against a deleted player", "info", [w]))
            if g:
                w = g[0]["b"] if g[0]["a"] == -pid else g[0]["a"]
                _award(db, cid, g[0], w)
                done = _after(db, c, g[0], w, lines, names)       # (the other ties with a deleted player too)
            else:                                 # nothing open: a tie they were waiting in (the final, third place)
                for x in _rows(db, cid, "AND winner IS NULL AND a IS NOT NULL AND b IS NOT NULL AND (a<0 OR b<0)")[:1]:
                    w = x["a"] if x["b"] < 0 else x["b"]
                    _award(db, cid, x, w)
                    done = _after(db, c, x, w, lines, names)
        if lines or done:
            _finish(svc, c, lines, done)


def restore(svc, pid):
    """A deleted player was put back (pesundo): their name is back in the draws. Ties given away stay given away."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for col in ("a", "b", "winner"):
            db.execute(f"UPDATE pes_cup_fixture SET {col}=? WHERE {col}=?", (pid, -pid))


# ---- what the game and the admin page show ------------------------------------------------------------------
def _score(f):
    if f["how"] == "bye":
        return "bye"
    s = f"{f['a_goals']}-{f['b_goals']}"
    if f["a_pk"] is not None and f["b_pk"] is not None:
        s += f" ({f['a_pk']}-{f['b_pk']} on penalties)"
    return s + (" (awarded)" if f["how"] == "walkover" else "")


def final_of(svc, c, a, b):
    """The open tie between a and b, when it is the cup's final (else None)."""
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        f = _open_tie(db, c["compe_id"], a, b)
        if f is None or f["stage"] != "K":
            return None
        last = db.execute("SELECT MAX(round) FROM pes_cup_fixture WHERE compe_id=? AND stage='K'", (c["compe_id"],)).fetchone()[0]
    return f if f["round"] == last else None


def bracket(svc, c, team_of):
    """The knockout for the game's own bracket screen (pesmod, /mod/bracket): (size, slots, ties, third).
    size: the first round's places (a power of two); slots: [team id or 0] per place, top to bottom (the
    left half, then the right half, as the screen draws them); ties: [(round, index, team a, team b, goals a,
    goals b, winner 0 / 1 / -1, played)] with round 1 = the first round and index top to bottom; third: the
    third-place match the same way, or None. team_of(pid) = the team that player entered with."""
    fx = fixtures(svc, c["compe_id"])
    ko = [f for f in fx if f["stage"] == "K"]
    if not ko:
        return None
    first = [f for f in ko if f["round"] == 1]
    size = 2 * len(first)
    slots = [0] * size
    for f in first:
        slots[2 * f["slot"]] = team_of(f["a"]) if f["a"] is not None else 0
        slots[2 * f["slot"] + 1] = team_of(f["b"]) if f["b"] is not None else 0

    def row(f, rnd):
        a = team_of(f["a"]) if f["a"] is not None else 0
        b = team_of(f["b"]) if f["b"] is not None else 0
        won = -1 if not f["winner"] else 0 if f["winner"] == f["a"] else 1
        ga, gb = f["a_goals"] or 0, f["b_goals"] or 0             # (a shootout: level goals, the winner's line)
        return (rnd, f["slot"], a, b, ga, gb, won, int(f["how"] in ("played", "walkover")))
    ties = [row(f, f["round"]) for f in sorted(ko, key=lambda f: (f["round"], f["slot"]))
            if f["how"] != "bye" and (f["a"] is not None or f["b"] is not None)]
    third = next((row(f, 0) for f in fx if f["stage"] == "T" and (f["a"] is not None or f["b"] is not None)), None)
    return size, slots, ties, third


def snapshot(svc, c):
    """For the admin page: the stage, the group tables and every tie by round."""
    fx = fixtures(svc, c["compe_id"])
    nm = _names(svc)
    name = lambda pid: None if pid is None else {"pid": pid, "name": nm(pid).capitalize() if pid < 0 else nm(pid)}     # noqa: E731

    def tie(f):
        return {"fid": f["fid"], "a": name(f["a"]), "b": name(f["b"]), "a_goals": f["a_goals"], "b_goals": f["b_goals"],
                "a_pk": f["a_pk"], "b_pk": f["b_pk"], "winner": f["winner"], "how": f["how"], "replays": f["replays"],
                "open": f["winner"] is None and f["a"] is not None and f["b"] is not None}
    groups = [{"name": group_name(g), "table": [{**r, "name": name(r["pid"])["name"]} for r in t],
               "ties": [tie(f) for f in fx if f["stage"] == "G" and f["grp"] == g]} for g, t in _tables(fx)]
    rounds = {}
    for f in fx:
        if f["stage"] == "K":
            rounds.setdefault(f["round"], []).append(f)
    third = [tie(f) for f in fx if f["stage"] == "T"]
    _, qualify, fill, _ = group_rules(c)
    ko_seeds = {p for f in fx if f["stage"] == "K" and f["round"] == 1 for p in (f["a"], f["b"]) if p is not None}
    started = any(f["stage"] == "G" and f["winner"] is not None for f in fx)
    through = sorted(ko_seeds if ko_seeds and groups else _qualifiers(fx, qualify, fill)[0] if started else [])
    return {"stage": _stage(fx), "groups": groups, "qualify": qualify, "extra": _extra(fx, qualify, fill),
            "through": through,                       # the groups' qualifiers (as the tables stand, until the knockout)
            "rounds": [{"name": round_name(len(v)), "ties": [tie(f) for f in v]} for _, v in sorted(rounds.items())]
                      + ([{"name": "Third place", "ties": third}] if third else [])}


def outline(svc, c, limit=1900):
    """The cup in the game's Competition Outline: the draw, the tables, the results and what is left to play."""
    fx = fixtures(svc, c["compe_id"])
    st = _stage(fx)
    nm = _names(svc)
    name = lambda pid: "?" if pid is None else nm(pid)     # noqa: E731
    rules = "A level match goes to extra time and penalties. Leaving a match early counts as a 0-3 loss."
    if st == "entry":
        db = _db(svc)
        with svc.accounts.lock, db:
            pids = [r[0] for r in db.execute("SELECT pid FROM pes_tourney_entry WHERE compe_id=? ORDER BY joined", (c["compe_id"],))]
        who = ", ".join(name(p) for p in pids) if pids else "nobody yet"
        kind = "a cup with groups first" if group_rules(c)[0] else "a knockout cup"
        return (f"{c['name']} is {kind}. Enter with a team; the draw is made when everyone is in.\n"
                f"Entered: {who}.\n{rules}")
    out = []
    ko = [f for f in fx if f["stage"] == "K"]
    by_round = {}
    for f in ko:
        by_round.setdefault(f["round"], []).append(f)
    sections = []                                                 # the latest round first
    for rnd in sorted(by_round, reverse=True):
        sections.append((round_name(len(by_round[rnd])).upper(), by_round[rnd]))
        if rnd == max(by_round):
            sections.append(("THIRD PLACE", [f for f in fx if f["stage"] == "T"]))
    for title, ties in sections:
        if all(f["a"] is None and f["b"] is None for f in ties):
            continue
        out.append(title)
        for f in ties:
            if f["winner"] is not None and f["how"] == "bye":
                out.append(f"{name(f['winner'])}: bye")
            elif f["winner"] is not None:
                out.append(f"{name(f['a'])} {_score(f)} {name(f['b'])}")
            else:
                out.append(f"{name(f['a'])} v {name(f['b'])}")
        out.append("")
    for g, table in _tables(fx):
        out.append(group_name(g).upper())
        for i, r in enumerate(table, 1):
            out.append(f"{i}. {name(r['pid'])}  {r['pts']} pts  ({r['played']} played, {r['gf']}-{r['ga']})")
        left = [f for f in fx if f["stage"] == "G" and f["grp"] == g and f["winner"] is None]
        if left:
            out.append("To play: " + ", ".join(f"{name(f['a'])} v {name(f['b'])}" for f in left))
        out.append("")
    if st == "done":
        final = max(ko, key=lambda f: f["round"])
        out.insert(0, f"{name(final['winner'])} won {c['name']}.\n")
    elif c["end"] > time.time():
        out.append("To play a match, both players enter the cup and choose Start Match at the same time.")
        if st == "groups":
            _, qualify, fill, _ = group_rules(c)
            extra = _extra(fx, qualify, fill)
            top = "The winner" if qualify == 1 else f"The top {qualify}"
            nxt = {1: "second", 2: "third"}.get(qualify, "next")
            out.append(f"{top} of each group {'goes' if qualify == 1 else 'go'} to the knockout"
                       + (f", with the best {extra} {nxt}-placed." if extra > 1 else
                          f", with the best {nxt}-placed." if extra else "."))
        out.append(rules)
    text = "\n".join(out).strip()
    while len(text) > limit and "\n" in text:                     # a big cup: drop lines from the end
        text = text[:text.rindex("\n")]
    return text
