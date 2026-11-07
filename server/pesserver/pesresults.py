"""A player's own match history and play analysis, as the game's menus ask for them.

- CMD_GET_GAMERESULTS {pid, game_res_type, pesleague_compe_id} (notes/re/functions/results.md, sweep.md): the
  past-results lists (Online Information -> Match Records, Online Divisions -> Past Game Results). game_res_type
  "ALL" = every match, "RANK" = Online Divisions. Each entry has the players, teams and score; A on a player there
  offers "Add to Match Block List" (the only place the game offers it, notes/re/functions/menus.md).
  Entries use the same layout as the replay Match List (pesreplay), which the game showed correctly.
- CMD_SEND_MATCH_ANALYZE {pid, analyze_data_type, analyze_data: {46 play stats}} is uploaded after Online Divisions
  (and myClub) matches; CMD_GET_MATCH_ANALYZE {pid, target_pid, analyze_data_type} returns them for the "Play
  Analysis" pages of a profile. The game aggregates the last 10 itself, so the server stores and returns them.
"""
import json, logging, time

import msgpack

from .pescmds import NOERR, cmd, _db

log = logging.getLogger("pesserver.results")
HISTORY = 30
ANALYZE_KEEP = 10
# match_category: an exact name from the game's 25-name enum (0x1437f2310); unknown names count as RANK_MATCH.
# The list can only label Online Divisions, Competition, eFootball League, Matchday or myClub, so Quick Match,
# lobby and co-op matches use names with no label (static 0x140be28dd, live 2026-11-07). The name also picks
# the profile page that opens from the entry (ONLINE_COOP: the co-op page).
CATEGORY = {"Quick Match": "PESLEAGUE_LOBBY", "Online Divisions": "RANK_MATCH", "Matchday": "MATCHDAY",
            "Friendly Match Lobby": "PESLEAGUE_LOBBY", "Online Co-op": "ONLINE_COOP"}


def _pack(o):
    return msgpack.packb(o, use_bin_type=True)


def _pname(name, pid):
    return _pack({"name": name or "", "xuid_one": "", "is_deleted": "NO",
                  "psn_user_id": _pack({"account_id": pid or 0, "online_id": "", "is_valid_cache": "YES"})})


def _entry(t, home, away, hname, aname, hs, as_, hteam, ateam, category):
    def score(n):
        return _pack({"score_1st": n or 0, "score_2nd": 0, "score_ex1st": 0, "score_ex2nd": 0, "score_pk": 0})
    players = [_pack({"pid": p or 0, "pname": _pname(n, p), "side": side, "is_com": "NO", "cpu_level": "NORMAL",
                      "is_follow": "NO", "is_guest": "NO"}) for p, n, side in ((home, hname, "HOME"), (away, aname, "AWAY"))]
    return _pack({"date": int(t or 0), "ex_type": "", "pk_flag": "NO", "no_match": "NO", "player": players,
                  "team": [int(hteam or 0), int(ateam or 0)], "score": [score(hs), score(as_)],
                  "match_category": category, "gameend_reason": "NORMAL_END"})


def history(svc, pid, kind="ALL"):
    db = _db(svc)
    out = []
    with svc.accounts.lock, db:
        if kind in ("ALL", "RANK"):
            try:
                cols = {r[1] for r in db.execute("PRAGMA table_info(pes_matches)")}
            except Exception:  # noqa: BLE001
                cols = set()
            if cols:
                team = "home_team, away_team" if "home_team" in cols else "NULL, NULL"
                mode = "mode" if "mode" in cols else "NULL"
                q = (f"SELECT ended, home_pid, away_pid, home_name, away_name, home_score, away_score, {team}, {mode}"
                     " FROM pes_matches WHERE (home_pid=? OR away_pid=?)")
                if "voided" in cols:
                    q += " AND voided=0"
                for r in db.execute(q + " ORDER BY id DESC LIMIT ?", (pid, pid, HISTORY)):
                    cat = CATEGORY.get(r[9] or "Quick Match", "PESLEAGUE_LOBBY")
                    if kind == "RANK" and cat != "RANK_MATCH":
                        continue
                    out.append(_entry(*r[:9], cat))
    return out


@cmd("CMD_GET_GAMERESULTS")
def get_gameresults(svc, sess, req):
    pid = getattr(sess, "pes_pid", None)
    if not isinstance(pid, int):
        return {"result": NOERR}
    rl = history(svc, pid, str(req.get("game_res_type") or "ALL").upper())
    return {"result": NOERR, "rlist": rl, "rlist_num": len(rl)}


# ---- play analysis --------------------------------------------------------------------------------------
def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_match_analyze (id INTEGER PRIMARY KEY, pid INTEGER, kind TEXT,
                  data TEXT, t REAL)""")


def _map(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        v = v.encode("utf-8", "surrogateescape")
    if isinstance(v, (bytes, bytearray)):
        try:
            out = msgpack.unpackb(bytes(v), raw=False, strict_map_key=False)
            return out if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


@cmd("CMD_SEND_MATCH_ANALYZE")
def send_match_analyze(svc, sess, req):
    pid = getattr(sess, "pes_pid", None)
    data = _map(req.get("analyze_data"))
    kind = str(req.get("analyze_data_type") or "ONLINE_DIVISIONS")
    if isinstance(pid, int) and data:
        clean = {str(k): v for k, v in data.items()
                 if isinstance(v, (int, str)) or (isinstance(v, list) and all(isinstance(x, int) for x in v))}
        db = _db(svc)
        with svc.accounts.lock, db:
            _setup(db)
            db.execute("INSERT INTO pes_match_analyze (pid, kind, data, t) VALUES (?,?,?,?)",
                       (pid, kind, json.dumps(clean), time.time()))
            db.execute("DELETE FROM pes_match_analyze WHERE pid=? AND kind=? AND id NOT IN (SELECT id FROM "
                       "pes_match_analyze WHERE pid=? AND kind=? ORDER BY id DESC LIMIT ?)",
                       (pid, kind, pid, kind, ANALYZE_KEEP))
    return {"result": NOERR}


@cmd("CMD_GET_MATCH_ANALYZE")
def get_match_analyze(svc, sess, req):
    who = req.get("target_pid") if isinstance(req.get("target_pid"), int) else getattr(sess, "pes_pid", None)
    kind = str(req.get("analyze_data_type") or "ONLINE_DIVISIONS")
    if not isinstance(who, int):
        return {"result": NOERR}
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT data FROM pes_match_analyze WHERE pid=? AND kind=? ORDER BY id DESC LIMIT ?",
                          (who, kind, ANALYZE_KEEP)).fetchall()
    lst = [json.loads(r[0]) for r in reversed(rows)]          # oldest first; the game keeps the last 10
    # inline maps, not blobs (parser 0x141c9f290)
    return {"result": NOERR, "analyze_data_size": len(lst), "analyze_data_list": lst}


def forget(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("DELETE FROM pes_match_analyze WHERE pid=?", (pid,))
