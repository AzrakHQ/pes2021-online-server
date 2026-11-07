"""Matchday (eFootball -> Matchday): one standing event, group stage only.

Players pick a side (A or B) and a team from that side's list, then play ordinary 1v1 matches; every match
adds Matchday points to the player and to their side's total. The pick-up stage and the watched final are
left out (they need spectating and the pick-up matching commands), so the event never leaves the group
stage. Reply shapes: notes/re/functions/modes2.md section C; nested maps are gate blobs.
The search is Quick Match with matching_type "MATCHDAY" (its own queue in pesmatch).
"""
import logging, time

import msgpack

from .pescmds import NOERR, cmd, _db, _player
from . import pesmatch

log = logging.getLogger("pesserver.matchday")

COMPE_ID = 1
NAME = "Friends Matchday"
SIDES = {"MATCHDAY_GROUP_A": ("Side A", [101, 108, 114, 127]),      # real team ids (pescmds.TEAM_LIST)
         "MATCHDAY_GROUP_B": ("Side B", [100, 116, 120, 131])}
POINTS = {"WIN": 3, "DRAW": 1, "LOSE": 0}
DAY = 86400


def _pack(d):
    return msgpack.packb(d, use_bin_type=True, unicode_errors="surrogateescape")


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS pes_matchday (pid INTEGER, compe_id INTEGER, grp TEXT, team_id INTEGER,
                  points INTEGER DEFAULT 0, matches INTEGER DEFAULT 0, PRIMARY KEY (pid, compe_id))""")
    try:
        db.execute("ALTER TABLE pes_matchday ADD COLUMN squad TEXT")      # JSON {gameplan_data, size, members}
    except Exception:  # noqa: BLE001  (column exists)
        pass
    db.execute("""CREATE TABLE IF NOT EXISTS pes_matchday_match (pid INTEGER, compe_id INTEGER, match_id TEXT,
                  points INTEGER, fetched INTEGER DEFAULT 0, UNIQUE (pid, compe_id, match_id))""")


def _entry(svc, pid):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT grp, team_id, points, matches FROM pes_matchday WHERE pid=? AND compe_id=?",
                       (pid, COMPE_ID)).fetchone()
    return {"grp": r[0], "team_id": r[1], "points": r[2], "matches": r[3]} if r else None


def _totals(svc):
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        rows = db.execute("SELECT grp, SUM(points), COUNT(*) FROM pes_matchday WHERE compe_id=? GROUP BY grp",
                          (COMPE_ID,)).fetchall()
    return {g: (int(p or 0), n) for g, p, n in rows}


def _dates():
    """A window that is always open: started yesterday, group stage running for another month."""
    now = int(time.time())
    return {"start_date": now - DAY, "entry_limit_date": now + 30 * DAY, "end_group_match_date": now + 30 * DAY,
            "pickup_member_date": now + 31 * DAY, "end_date": now + 32 * DAY}


def _group(grp, name, teams, svc):
    return _pack({"team_list": teams, "team_list_num": len(teams), "group_name": name, "group_desc": "",
                  "is_entry_restricted": "NO", "pre_pickup_users_pid_list": []})


def _compe(svc, pid):
    e = _entry(svc, pid)
    users = sum(n for _, n in _totals(svc).values())
    return {"compe_id": COMPE_ID, "base_id": COMPE_ID, "compe_name": NAME, "is_disp_compe_name": "YES",
            "group_info_a": _group("MATCHDAY_GROUP_A", *SIDES["MATCHDAY_GROUP_A"], svc),
            "group_info_b": _group("MATCHDAY_GROUP_B", *SIDES["MATCHDAY_GROUP_B"], svc),
            "match_type": "SINGLE_MATCH", "squad_type": "MATCHDAY_SQUAD_SPECIAL", "group_type": "MATCHDAY_GROUP_TYPE_TEAM",
            "rating_rank": "MATCHDAY_RATING_RANK_NONE", **_dates(), "entry_user_num": users, "entry_user_max": 1000,
            "is_end": "NO", "banner_file_info": _pack({"filename": "", "hash_str": ""}),
            "list_banner_file_info": _pack({"filename": "", "hash_str": ""}), "is_use_emblem": "NO",
            "is_use_emblem_list": "NO", "is_entry": "YES" if e else "NO",
            "watch_match_reward_state": "NOT_FINISH_PICKUP_MATCH", "compe_db_id": 0, "is_possible_watch": "NO"}


@cmd("CMD_JOIN_MATCHDAY_MODE")
def join_matchday_mode(svc, sess, req):
    return {"result": NOERR}


@cmd("CMD_GET_MATCHDAY_LIST")
def get_matchday_list(svc, sess, req):
    return {"result": NOERR, "list_num": 1, "list_max": 1, "is_no_check": "NO", "add_limit_rating": 0,
            "compe_list": [_pack(_compe(svc, pesmatch._pid(sess)))]}


@cmd("CMD_GET_MATCHDAY_INFO")
def get_matchday_info(svc, sess, req):
    """compe_info has two levels: the event itself (the same map as a list entry) goes inside the blob
    `info_overview`; keys put flat in compe_info are ignored, which gave compe_id 0 and, with
    end_group_match_date 0, "The Group Match Stage has ended." (matchday2.md)."""
    pid = pesmatch._pid(sess)
    e = _entry(svc, pid)
    ov = _compe(svc, pid)
    info = {"info_overview": _pack(ov),
            "start_match_limit_date": ov["end_group_match_date"],
            "entry_pickup_match_start_date": ov["pickup_member_date"],   # the "results in" countdown counts to this
            "watch_match_start_date": ov["end_date"], "compe_db_id": 0,
            "is_check_eula": "NO", "eula_url": "", "ball_id": 0, "group_match_time": 10, "pickup_match_time": 10,
            "watch_wait_time": 0, "use_stadium_id": 0,
            "fixed_timezone": "TIME_ZONE_TYPE_NONE", "fixed_weather": "WEATHER_TYPE_NONE", "fixed_season": "SEASON_TYPE_NONE",
            "is_fix_condition": "NO", "is_equalization": "NO", "is_pesleague_stadium": "NO", "is_efootball_pro_stadium": "NO",
            "joined_matchday_group": e["grp"] if e else "", "pickup_select": "NO",
            "team_id": e["team_id"] if e else 0, "pickup_cut_off_rank": 0}
    # all_reward_info is an array of blobs read without a type check: never send it as one blob (a guessed one
    # crashed the game, live 2026-11-06); left out = no rewards
    return {"result": NOERR, "compe_info": _pack(info)}


@cmd("CMD_JOIN_MATCHDAY")
def join_matchday(svc, sess, req):
    pid = pesmatch._pid(sess)
    grp = str(req.get("entry_matchday_group") or "")
    if grp not in SIDES:
        grp = "MATCHDAY_GROUP_A" if str(req.get("entry_matchday_group")) in ("0", "A") else "MATCHDAY_GROUP_B"
    team = int(req.get("team_id") or 0)
    # the squad the game builds for this team (player ids) and its game plan: the game reads them back with
    # GET_MATCHDAY_SP_GAMEPLAN before a match; empty, the match load crashes (live 2026-11-06)
    import json
    members = req.get("sp_member_list") or []
    members = [members] if isinstance(members, int) else [int(m) for m in members if str(m).lstrip("-").isdigit()]
    squad = json.dumps({"gameplan_data": str(req.get("sp_gameplan_data") or ""),
                        "gameplan_data_size": int(req.get("sp_gameplan_data_size") or 0), "members": members})
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        db.execute("INSERT INTO pes_matchday (pid, compe_id, grp, team_id, squad) VALUES (?,?,?,?,?) "
                   "ON CONFLICT (pid, compe_id) DO UPDATE SET grp=excluded.grp, team_id=excluded.team_id, squad=excluded.squad",
                   (pid, COMPE_ID, grp, team, squad))
    log.info("MATCHDAY %s joined %s with team %s", pid, grp, team)
    return {"result": NOERR}


@cmd("CMD_GET_MATCHDAY_MAINMENU_INFO")
def get_matchday_mainmenu_info(svc, sess, req):
    pid = pesmatch._pid(sess)
    e = _entry(svc, pid) or {"points": 0, "matches": 0, "grp": ""}
    tot = _totals(svc)
    pa, pb = tot.get("MATCHDAY_GROUP_A", (0, 0))[0], tot.get("MATCHDAY_GROUP_B", (0, 0))[0]
    ratio = round(100 * pa / (pa + pb)) if pa + pb else 50
    gm = _pack({"matchday_point": e["points"], "matchday_rank": 0, "matchday_ranking_user_num": sum(n for _, n in tot.values()),
                "matchday_ratio": ratio, "total_matchday_point_group_a": pa, "total_matchday_point_group_b": pb,
                "pickup_match_score_group_a": 0, "pickup_match_score_group_b": 0, "is_hide_graph": "NO"})
    pu = _pack({"pickup_users_group_a": [], "pickup_user_group_a_num": 0, "pickup_users_group_b": [],
                "pickup_user_group_b_num": 0, "is_failed_pickup_users": "NO"})
    mm = {"matchday_progress": "MATCHDAY_PROGRESS_GROUP_MATCH", "group_match_info": gm, "pickup_user_info": pu,
          "is_final_reward": "NO", "final_reward_list": [], "is_get_final_reward": "NO",
          "is_watch_match": "NO", "pickup_reward_type": "MATCHDAY_PICKUP_REWARD_NONE", "pickup_reward_list": []}
    return {"result": NOERR, "mainmenu_info": _pack(mm)}


@cmd("CMD_GET_MATCHDAY_ACHIEVEMENT_REWARD")
def get_matchday_achievement_reward(svc, sess, req):
    """After a match: the points this match added (shown on the Matchday points screen)."""
    pid = pesmatch._pid(sess)
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT COALESCE(SUM(points), 0) FROM pes_matchday_match WHERE pid=? AND compe_id=? AND fetched=0",
                       (pid, COMPE_ID)).fetchone()
        db.execute("UPDATE pes_matchday_match SET fetched=1 WHERE pid=? AND compe_id=?", (pid, COMPE_ID))
    # The points screen shows "Breakdown: Gameplay Review <gameplay_quality>, Squad Rating x<squad_rating>". Our
    # points are win 3 / draw 1 / loss 0, so: the review is the points and the squad factor 1.
    pts = int(r[0])
    return {"result": NOERR, "add_matchday_point": pts, "gameplay_quality": pts, "squad_rating": 1,
            "achievement_reward_list": []}


@cmd("CMD_GET_MATCHDAY_SP_GAMEPLAN")
def get_matchday_sp_gameplan(svc, sess, req):
    """Give the game back the squad and game plan it sent with JOIN_MATCHDAY."""
    import json
    pid = pesmatch._pid(sess)
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        r = db.execute("SELECT squad FROM pes_matchday WHERE pid=? AND compe_id=?", (pid, COMPE_ID)).fetchone()
    sq = json.loads(r[0]) if r and r[0] else None
    if not sq:
        return {"result": NOERR}
    return {"result": NOERR, "gameplan_data": sq["gameplan_data"], "gameplan_data_size": sq["gameplan_data_size"],
            "sp_member_list": sq["members"]}


@cmd("CMD_GET_MATCHDAY_WATCH_MATCH_REWARD")
def get_matchday_watch_match_reward(svc, sess, req):
    return {"result": NOERR, "is_exist_watch_match_reward": "NO", "watch_match_reward_list": []}


def record(svc, room):
    """Called once per finished MATCHDAY match (pesmatch, at the verdict)."""
    goals = room.get("goals", set())
    score = {s: sum(1 for _, g in goals if g == s) for s in ("HOME", "AWAY")}
    home = room["owner"]
    away = next(p for p in room["members"] if p != home)
    mid = room.get("match_id") or f"{int(room.get('t', time.time()))}-{home}-{away}"
    db = _db(svc)
    with svc.accounts.lock, db:
        _setup(db)
        for me, side in ((home, "HOME"), (away, "AWAY")):
            mine, theirs = (score["HOME"], score["AWAY"]) if side == "HOME" else (score["AWAY"], score["HOME"])
            res = "WIN" if mine > theirs else "LOSE" if mine < theirs else "DRAW"
            cur = db.execute("INSERT OR IGNORE INTO pes_matchday_match (pid, compe_id, match_id, points) VALUES (?,?,?,?)",
                             (me, COMPE_ID, mid, POINTS[res]))
            if cur.rowcount:
                db.execute("UPDATE pes_matchday SET points=points+?, matches=matches+1 WHERE pid=? AND compe_id=?",
                           (POINTS[res], me, COMPE_ID))
                log.info("MATCHDAY %s: %s (+%d)", me, res, POINTS[res])
