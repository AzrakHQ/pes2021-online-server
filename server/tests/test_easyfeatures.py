"""Courtesy rating (pesmanner.py), the co-op profile and block list (pescoop.py), messages in a player's
Information list (announce.py)."""
import msgpack

from pesserver import announce, events, pescmds, pescoop, pesmanner, pesmatch, pestourney  # noqa: F401  (handlers)
from test_pesrooms import svc, Sess, unb   # noqa: F401  (fixture)


def _user(svc, pid, name):
    db = pescmds._db(svc)
    with svc.accounts.lock, db:
        db.execute("INSERT OR REPLACE INTO pes_players (pid, username, name, created) VALUES (?, ?, ?, 0)",
                   (pid, f"u{pid}", name))


def _match(svc, rid, a, b, sa, sb, ending):
    """One match as the games report it, through pescmds.dispatch (so events.observe sees every command)."""
    pesmatch._state(svc)["rooms"][rid] = {"owner": a, "members": [a, b], "t": 0}
    for s in (sa, sb):
        pescmds.dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST", "gtimer": 1})
    ending()
    pesmatch._state(svc)["rooms"].pop(rid, None)


def _warn(svc, s):
    return pescmds.dispatch(svc, s, "CMD_GET_MANNER_WARN", {}).get("warn_kind")


def test_complete_matches_never_warn(svc):
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})

    def full_time():
        for s in (a, b):
            pescmds.dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "END", "gtimer": 2})
        # the normal exit: the second player's game reports the link the first one cut
        pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "DISCONN_P2P", "error_code": "GNCX_0003"})
    for n in range(5):
        _match(svc, n + 1, 1, 2, a, b, full_time)
    assert pesmanner.rating(svc, 1) == {"grade": "A", "quits": 0, "matches": 5}
    assert pesmanner.rating(svc, 2)["quits"] == 0
    assert _warn(svc, a) is None and _warn(svc, b) is None


def test_leaving_early_warns_once_per_step(svc):
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    a.connected_at = 1

    def give_up():                     # pid 1 quits from the pause menu; pid 2's game sees the link drop
        pescmds.dispatch(svc, a, "CMD_SEND_ABNORMALEND", {"reason": "GIVEUP"})
        pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "DISCONN_P2P"})

    _match(svc, 1, 1, 2, a, b, give_up)
    assert pesmanner.rating(svc, 1)["quits"] == 1 and pesmanner.rating(svc, 2)["quits"] == 0
    assert _warn(svc, a) == "DOWN_B" and _warn(svc, a) is None          # once
    assert _warn(svc, b) is None
    _match(svc, 2, 1, 2, a, b, give_up)
    assert _warn(svc, a) == "A_TO_B" and _warn(svc, a) is None

    def closes_game():                 # pid 1's connection drops mid-match
        events.disconnected(svc, a)
    _match(svc, 3, 1, 2, a, b, closes_game)
    assert pesmanner.rating(svc, 1)["grade"] == "B"
    assert _warn(svc, a) == "DOWN_C" and _warn(svc, a) is None          # "remains high"
    _match(svc, 4, 1, 2, a, b, give_up)
    assert pesmanner.rating(svc, 1) == {"grade": "C", "quits": 4, "matches": 4}
    assert _warn(svc, a) == "STILL_C" and _warn(svc, a) is None         # "has fallen to C"
    _match(svc, 5, 1, 2, a, b, give_up)
    assert _warn(svc, a) == "STILL_C"                                   # another one at C
    assert pescmds.dispatch(svc, b, "CMD_GET_COOP_PROFILE", {"pid": 1})["user_info"]
    prof = unb(pescmds.dispatch(svc, b, "CMD_GET_COOP_PROFILE", {"pid": 1})["coop_profile_data"])
    assert prof["coop_manner"] == "C"


def test_kick_and_pre_kickoff_do_not_count(svc):
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    a.connected_at = 1
    a.kicked = True
    _match(svc, 1, 1, 2, a, b, lambda: events.disconnected(svc, a))
    # a match that never kicked off
    pesmatch._state(svc)["rooms"][2] = {"owner": 1, "members": [1, 2], "t": 0}
    pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "NO_OPERATION"})
    assert pesmanner.rating(svc, 1)["matches"] == 0 and pesmanner.rating(svc, 2)["matches"] == 0


def test_coop_block_list(svc):
    from pesserver import pescoop
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a = Sess(1)
    r = pescmds.dispatch(svc, a, "CMD_GET_COOP_REJECT_LIST", {})
    assert r["reject_list"] == [] and r["reject_list_num"] == 0
    r = pescmds.dispatch(svc, a, "CMD_SET_COOP_REJECT", {"set_reject_pid_list": [2, 1], "unset_reject_pid_list": []})
    assert r["reject_list_num"] == 1
    e = unb(r["reject_list"][0])
    assert e["pid"] == 2 and unb(e["pname"])["name"] == "Bob"
    assert pescoop.blocked(svc, 2, 1) and pescoop.blocked(svc, 1, 2)
    r = pescmds.dispatch(svc, a, "CMD_SET_COOP_REJECT", {"unset_reject_pid_list": [2]})
    assert r["reject_list_num"] == 0 and not pescoop.blocked(svc, 1, 2)
    pescmds.dispatch(svc, a, "CMD_SET_COOP_REJECT", {"set_reject_pid_list": [2]})
    pescoop.forget(svc, 2)                       # pid 2 deleted on the admin page
    assert not pescoop.blocked(svc, 1, 2)


def test_blocked_players_are_not_paired_in_coop(svc):
    from pesserver import pescoop
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    for s in (a, b):
        s.mod_features = ("coop1",)
    pescmds.dispatch(svc, a, "CMD_SET_COOP_REJECT", {"set_reject_pid_list": [2]})
    q = pescoop._queue(svc)
    q[:] = [(1, a, 0), (2, b, 0)]
    pescoop._try_pair(svc)
    assert [x[0] for x in q] == [1, 2]                # still waiting


def test_message_stays_in_the_players_information_list(svc):
    a, b = Sess(1), Sess(2)
    assert pescmds.dispatch(svc, a, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 1}) == {"result": "NOERR"}
    announce.tell(svc, 1, "Hello", "See you at 9")
    r = pescmds.dispatch(svc, a, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 1, "lang": "US", "region": "REGION_US"})
    assert r["annnounce_info_list_num"] == 1
    item = unb(r["announce_info_list"][0])
    assert item["announce_type"] == "PERSONAL_INFO" and item["title"] == "Hello"
    body = pescmds.dispatch(svc, a, "CMD_GET_INFOBODY", {"pid": 1, "announce_id": item["announce_id"],
                                                         "is_private": "YES"})
    assert unb(body["announce_info"])["body"] == "See you at 9"
    # nobody else sees it
    assert pescmds.dispatch(svc, b, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 2}) == {"result": "NOERR"}
    assert pescmds.dispatch(svc, b, "CMD_GET_INFOBODY", {"pid": 2, "announce_id": item["announce_id"],
                                                         "is_private": "YES"}) == {"result": "NOERR"}
    # no second copy in the box before Quick Match; the list keeps at most INBOX_MAX, each with its own id
    assert pescmds.dispatch(svc, a, "CMD_GET_URGENT_ANNOUNCEMENT", {"pid": 1}) == {"result": "NOERR"}
    for n in range(announce.INBOX_MAX + 2):
        announce.tell(svc, 1, f"T{n}", "x")
    ids = [m["id"] for m in announce._load(svc)["inbox"]["1"]]
    assert len(set(ids)) == len(ids)
    assert pescmds.dispatch(svc, a, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 1})["annnounce_info_list_num"] == announce.INBOX_MAX
    announce.forget(svc, 1)
    assert pescmds.dispatch(svc, a, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 1}) == {"result": "NOERR"}


def test_lobby_welcome_once_per_player(svc):
    a, b = Sess(1), Sess(2)
    r = pescmds.dispatch(svc, a, "CMD_GET_LOBBYINFO", {"lobby_id": 1})
    assert r["has_info"] == "YES" and r["title"] and r["sentence_info"]
    assert pescmds.validate("CMD_GET_LOBBYINFO", r)
    assert pescmds.dispatch(svc, a, "CMD_GET_LOBBYINFO", {"lobby_id": 1})["has_info"] == "NO"
    assert pescmds.dispatch(svc, b, "CMD_GET_LOBBYINFO", {"lobby_id": 1})["has_info"] == "YES"
    announce.forget(svc, 1)                      # a deleted player's number starts clean
    assert pescmds.dispatch(svc, a, "CMD_GET_LOBBYINFO", {"lobby_id": 1})["has_info"] == "YES"


def test_both_idle_both_marked_and_a_later_drop_does_not_count(svc):
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    b.connected_at = 1

    def both_idle():                   # live 2026-11-07: both games report NO_OPERATION within 5 ms
        pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "NO_OPERATION"})
        pescmds.dispatch(svc, a, "CMD_SEND_ABNORMALEND", {"reason": "NO_OPERATION"})
        events.disconnected(svc, b)    # then one of them closes the game
    _match(svc, 1, 1, 2, a, b, both_idle)
    assert pesmanner.rating(svc, 1)["quits"] == 1 and pesmanner.rating(svc, 2)["quits"] == 1
    assert pesmanner.rating(svc, 2)["matches"] == 1          # not counted twice


def test_opponent_link_lost_then_drop_is_not_blamed(svc):
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    b.connected_at = 1

    def link_lost():                   # a network problem: both see the link go, then one closes the game
        pescmds.dispatch(svc, a, "CMD_SEND_ABNORMALEND", {"reason": "DISCONN_P2P"})
        events.disconnected(svc, b)
    _match(svc, 1, 1, 2, a, b, link_lost)
    assert pesmanner.rating(svc, 1)["quits"] == 0 and pesmanner.rating(svc, 2)["quits"] == 0


def test_avatar_round_trip(svc):
    import base64, hashlib
    from pesserver import pesavatar  # noqa: F401
    raw = bytes(range(256)) * (0xC000 // 256)
    data, md5 = base64.b64encode(raw).decode(), hashlib.md5(raw).hexdigest()
    a, b = Sess(1), Sess(2)
    # nobody has one yet: bare reply (the game shows its silhouette)
    assert pescmds.dispatch(svc, None, "CMD_GET_AVATAR_HTTP", {"pid": "1", "urltype": "AVATAR"}) == {"result": "NOERR"}
    assert pescmds.dispatch(svc, a, "CMD_SET_AVATAR", {"pid": 2, "avatar_data_size": len(raw), "avatar_data": data,
                                                       "avatar_md5": md5}) == {"result": "NOERR"}
    r = pescmds.dispatch(svc, None, "CMD_GET_AVATAR_HTTP", {"pid": "1", "urltype": "AVATAR"})   # own pid, not "pid 2"
    assert r["avatar_data"] == data and r["avatar_data_size"] == 0xC000 and r["avatar_md5"] == md5
    assert pescmds.validate("CMD_GET_AVATAR_HTTP", r)
    assert pescmds.dispatch(svc, None, "CMD_GET_AVATAR_HTTP", {"pid": "2"}) == {"result": "NOERR"}
    # garbage and clearing
    pescmds.dispatch(svc, a, "CMD_SET_AVATAR", {"avatar_data": "!!not base64", "avatar_data_size": 9})
    assert pescmds.dispatch(svc, None, "CMD_GET_AVATAR_HTTP", {"pid": "1"})["avatar_md5"] == md5
    pescmds.dispatch(svc, a, "CMD_SET_AVATAR", {"avatar_data": "", "avatar_data_size": 0})
    assert pescmds.dispatch(svc, None, "CMD_GET_AVATAR_HTTP", {"pid": "1"}) == {"result": "NOERR"}


def test_long_fields_are_shortened_in_the_log():
    from pesserver.ncl import _short
    assert _short("x" * 70000) == "<70000 chars>" and _short("short") == "short" and _short(5) == 5


def test_every_feature_module_is_loaded_by_the_service():
    """The service imports the handler modules itself (service.py); a module missing there answers nothing live."""
    import re, pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("pesserver", "service.py").read_text()
    line = next(l for l in src.splitlines() if "register their handlers" in l)
    for mod in ("pesrooms", "pescoop", "pesdivisions", "pesmatchday", "pesmanner", "pesavatar", "pesprofile",
                "pesblock", "pesreplay", "pesresults", "pesranking", "peswords", "announce"):
        assert re.search(rf"\b{mod}\b", line), mod


def test_profile_round_trip(svc):
    from pesserver import pesprofile  # noqa: F401
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    a.pes_username = "u1"
    # as the game sends it (live): one nested blob; the gate hands nested maps over as surrogate-escaped text
    blob = msgpack.packb({"date": 0, "favorite_team": "Arsenal", "favorite_player": "Saka", "intro": "Hello",
                          "is_enable_myPES": "NO"}, use_bin_type=True).decode("utf-8", "surrogateescape")
    assert pescmds.dispatch(svc, a, "CMD_SET_PLAYERPROFILE", {"profile": blob}) == {"result": "NOERR"}
    who = msgpack.packb({"pid": 1, "pname": msgpack.packb({"name": ""})}).decode("utf-8", "surrogateescape")
    r = pescmds.dispatch(svc, b, "CMD_GET_PLAYERINFO", {"pid": 2, "user_id": who})        # Bob looks at Ann
    assert (r["favoriteTeam"], r["favoritePlayer"], r["intro"]) == ("Arsenal", "Saka", "Hello")
    assert unb(r["pname"])["name"] == "Ann" and r["pid"] == 1
    assert unb(r["manner"]) == {"manner": "A", "disconnect_rate": "LOW_RATE"}               # a blob, never a string
    assert pescmds.validate("CMD_GET_PLAYERINFO", r)
    r = pescmds.dispatch(svc, b, "CMD_GET_PLAYERINFO", {"pid": 2})                          # own, no profile yet
    assert r["favoriteTeam"] == "" and unb(r["pname"])["name"] == "Bob"


def test_match_block_list_keeps_players_apart(svc):
    import asyncio
    from pesserver import pesblock
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob"); _user(svc, 3, "Cid")
    a, b, c = Sess(1), Sess(2), Sess(3)
    assert pescmds.dispatch(svc, a, "CMD_SET_MATCHREJECTPID", {"pid": 1, "reject_pid": 2, "is_reject": "YES"}) == {"result": "NOERR"}
    r = pescmds.dispatch(svc, a, "CMD_GET_MATCHREJECTLIST", {"pid": 1})
    assert r["reject_list_num"] == 1 and unb(unb(r["reject_list"][0])["pname"])["name"] == "Bob"
    assert pesblock.blocked(svc, 2, 1)

    async def go():
        st = pesmatch._state(svc)
        st["queue"] = [(1, a), (2, b)]
        assert pesmatch._next_pair(svc) is None           # Ann blocked Bob: no pair either way
        st["queue"].append((3, c))
        assert pesmatch._next_pair(svc) == (0, 2)         # Ann meets Cid instead
    asyncio.run(go())
    pescmds.dispatch(svc, a, "CMD_SET_MATCHREJECTPID", {"pid": 1, "reject_pid": 2, "is_reject": "NO"})
    assert not pesblock.blocked(svc, 1, 2)


def test_replay_recorded_and_listed(svc):
    import base64
    from pesserver import pesreplay
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    a.pes_username, b.pes_username = "u1", "u2"
    pesmatch._state(svc)["rooms"][7] = {"owner": 1, "members": [1, 2], "t": 1793900000,
                                        "goals": {(10, "HOME"), (20, "HOME"), (30, "AWAY")}, "teams": {1: 101, 2: 108}}
    r = pescmds.dispatch(svc, a, "CMD_GET_MATCHID", {})
    mid = r["match_id"]
    assert r["upload"] == "YES" and mid
    for s in (a, b):                                   # both games start uploading; Ann's was first
        assert pescmds.dispatch(svc, s, "CMD_START_SEND_GAME_SEQUENCE_DATA", {"pid": s.pes_pid}) == {"result": "NOERR"}

    def rec(kind, data):
        return msgpack.packb({"type": kind, "data": base64.b64encode(data.ljust(256, b"\0")).decode(),
                              "data_len": len(data)}, use_bin_type=True)
    pescmds.dispatch(svc, a, "CMD_SEND_GAME_SEQUENCE_DATA", {"match_id": mid, "game_seq_data": [rec(0, b"HDR"), rec(2, b"AB")]})
    pescmds.dispatch(svc, b, "CMD_SEND_GAME_SEQUENCE_DATA", {"match_id": mid, "game_seq_data": [rec(2, b"XX")]})   # dropped
    pescmds.dispatch(svc, a, "CMD_LEAVE_GAME", {"pid": 1})                                                         # closes it
    f1, f2 = pesreplay.chunk_file(svc, mid, "1.enc"), pesreplay.chunk_file(svc, mid, "2.enc")
    assert pesreplay._dec(open(f1, "rb").read()) == b"\x00HDRAB"
    assert pesreplay._dec(open(f2, "rb").read()) == b"\x00\x03\x00END_OF_DATA"
    assert pesreplay.chunk_file(svc, mid, "3.enc") is None and pesreplay.chunk_file(svc, "../x", "1.enc") is None
    lst = pescmds.dispatch(svc, b, "CMD_GET_WATCHING_MATCH_LIST", {"match_category": "PESLEAGUE_LOBBY", "index": 0})
    assert lst["match_list_num"] == 1
    e = unb(lst["match_list"][0])
    gr = unb(e["game_result"])
    assert e["match_id"] == mid and gr["team"] == [101, 108]
    assert [unb(unb(p)["pname"])["name"] for p in gr["player"]] == ["Ann", "Bob"]
    assert [unb(s)["score_1st"] for s in gr["score"]] == [2, 1]
    info = pescmds.dispatch(svc, b, "CMD_GET_PESLEAGUE_COMPE_INFO", {"compe_id": 1})
    assert unb(info["compe_data"])["match_type"] == "PES_LEAGUE_MATCH_1VS1_LOBBY"
    urls = {unb(u)["type"]: unb(u)["url"] for u in pescmds.dispatch(svc, b, "CMD_GET_URLLIST", {})["ulist"]}
    assert urls["WATCHING_GAME"].endswith("/r")


def test_match_history_and_analysis(svc):
    from pesserver import pesresults  # noqa: F401
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    events._st(svc)                                       # creates pes_matches (+ the team columns)
    db = svc.accounts.db
    with svc.accounts.lock, db:
        db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name, home_score,"
                   " away_score, reason, mode, home_team, away_team) VALUES (1, 0, 1793900000, 1, 2, 'Ann', 'Bob', 2, 1,"
                   " '', 'Online Divisions', 101, 108)")
        db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name, home_score,"
                   " away_score, reason, mode) VALUES (2, 0, 1793900100, 2, 1, 'Bob', 'Ann', 0, 0, '', 'Quick Match')")
    r = pescmds.dispatch(svc, a, "CMD_GET_GAMERESULTS", {"pid": 1, "game_res_type": "ALL"})
    assert r["rlist_num"] == 2
    e = unb(r["rlist"][1])
    assert e["team"] == [101, 108] and e["match_category"] == "RANK_MATCH"
    assert unb(r["rlist"][0])["match_category"] == "PESLEAGUE_LOBBY"                    # Quick Match: no label
    assert [unb(unb(p)["pname"])["name"] for p in e["player"]] == ["Ann", "Bob"]
    assert pescmds.dispatch(svc, a, "CMD_GET_GAMERESULTS", {"pid": 1, "game_res_type": "RANK"})["rlist_num"] == 1
    assert pescmds.dispatch(svc, a, "CMD_GET_SERVER_ENV", {})["matchrejectpid_max"] > 0
    # analysis: what the game uploads comes back as inline maps, newest 10
    for n in range(12):
        pescmds.dispatch(svc, a, "CMD_SEND_MATCH_ANALYZE", {"pid": 1, "analyze_data_type": "ONLINE_DIVISIONS",
                         "analyze_data": {"game_res": "WIN", "posession": 50 + n, "intercept_area": list(range(9))}})
    r = pescmds.dispatch(svc, b, "CMD_GET_MATCH_ANALYZE", {"pid": 2, "target_pid": 1, "analyze_data_type": "ONLINE_DIVISIONS"})
    assert r["analyze_data_size"] == 10 and unb(r["analyze_data_list"][-1])["posession"] == 61
    assert unb(r["analyze_data_list"][0])["intercept_area"] == list(range(9))          # blobs, never inline maps
    assert pescmds.dispatch(svc, b, "CMD_GET_MATCH_ANALYZE", {"target_pid": 1, "analyze_data_type": "MYCLUB_DIVISIONS"})["analyze_data_size"] == 0


def test_replay_lobby_gets_its_own_welcome(svc):
    a = Sess(1)
    pescmds.dispatch(svc, a, "CMD_GET_LOBBYLIST", {"lobby_type": "PESLEAGUE"})
    assert pescmds.dispatch(svc, a, "CMD_GET_LOBBYINFO", {"lobby_id": 1})["title"] == "Match Replays"
    pescmds.dispatch(svc, a, "CMD_GET_LOBBYLIST", {"lobby_type": "FREE"})
    assert pescmds.dispatch(svc, a, "CMD_GET_LOBBYINFO", {"lobby_id": 1})["title"] == "Friendly Match Lobby"


def test_profile_disconnections_label(svc):
    from pesserver import pesprofile  # noqa: F401
    _user(svc, 1, "Ann")
    for _ in range(2):
        pesmanner._add(svc, 1, True, "GIVEUP")
    r = pescmds.dispatch(svc, Sess(2), "CMD_GET_PLAYERINFO", {"user_id": msgpack.packb({"pid": 1})})
    assert unb(r["manner"]) == {"manner": "B", "disconnect_rate": "SEMI_HIGH_RATE"}


def test_rankings(svc):
    from pesserver import pesranking  # noqa: F401
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob"); _user(svc, 3, "Cid")
    db = svc.accounts.db
    with svc.accounts.lock, db:
        db.execute("UPDATE pes_players SET rating=? WHERE pid=?", (1100, 2))
    events._st(svc)
    with svc.accounts.lock, db:
        for hs, as_ in ((2, 0), (1, 1), (0, 3)):
            db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name,"
                       " home_score, away_score, reason) VALUES (1, 0, 1, 1, 2, 'Ann', 'Bob', ?, ?, '')", (hs, as_))
    r = pescmds.dispatch(svc, None, "CMD_GET_RANKING", {"urltype": "RANK", "start_index": "0", "view_num": "100",
                                                        "sort": "RATING", "order": "ASC"})
    assert r["total_rank_num"] == 3
    top = r["rank_list"][0]
    assert top["rank"] == 1 and top["user"]["pname"]["name"] == "Bob" and top["point"] == 1100
    assert top["user"]["manner"] == "A" and top["user"]["division"] == ""
    ann = next(e for e in r["rank_list"] if e["user"]["pid"] == 1)["user"]["match_record"]
    assert (ann["win_count"], ann["draw_count"], ann["lose_count"]) == (1, 1, 1)
    w = pescmds.dispatch(svc, None, "CMD_GET_RANKING", {"sort": "WIN_NUM"})["rank_list"]
    assert [e["point"] for e in w][:2] == [1, 1]
    assert pescmds.dispatch(svc, None, "CMD_GET_RANKING", {"sort": "WIN_RATE"})["total_rank_num"] == 0   # < 10 matches
    b = pescmds.dispatch(svc, None, "CMD_GET_RANKING_BYID", {"pid": "3", "sort": "RATING", "view_num": "100"})
    assert any(e["user"]["pid"] == 3 for e in b["rank_list"])


def test_division_ranking(svc):
    import asyncio
    from pesserver import pesdivisions, pesranking  # noqa: F401
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    for pid, s_ in ((1, a), (2, b)):
        s_.pes_username = f"u{pid}"
        svc.online[pid] = s_

    async def go():
        for n in range(3):
            pesmatch._verdict(svc, n + 1, {"owner": 1, "members": [1, 2], "t": n, "mode": "OC_RANK",
                                           "match_id": f"m{n}", "goals": {(100, "HOME")}, "phase": {1: "END", 2: "END"}})
    asyncio.run(go())
    r = pescmds.dispatch(svc, None, "CMD_GET_DIVISION_RANKING", {"division": "D10", "start_index": "0", "view_num": "100"})
    assert r["total_rank_num"] == 2 and r["rank_list"][0]["user"]["pid"] == 1 and r["rank_list"][0]["point"] == 9
    assert r["rank_list"][0]["user"]["match_record"]["win_count"] == 3
    assert pescmds.dispatch(svc, None, "CMD_GET_DIVISION_RANKING", {"division": "D1"})["total_rank_num"] == 0
    assert pescmds.validate("CMD_GET_DIVISION_RANKING", r)


def test_word_filter(svc, tmp_path):
    from pesserver import peswords  # noqa: F401
    a = Sess(1); a.pes_username = "u1"
    _user(svc, 1, "")
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "anything"}) == {"result": "NOERR"}   # no file
    (tmp_path / "blocked_words.txt").write_text("# host's list\nbadword\nvery rude phrase\nhell\n")
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "Room of BadWord"})["result"] == "ERR_NGWORD"
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "b.a.d.w.o.r.d"})["result"] == "ERR_NGWORD"
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "a VERY  rude phrase"})["result"] == "ERR_NGWORD"
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "Arsenal FC"})["result"] == "NOERR"
    assert pescmds.dispatch(svc, a, "CMD_CHECK_STRING", {"str": "hello there"})["result"] == "NOERR"   # whole words
    assert pescmds.dispatch(svc, a, "CMD_CREATE_PLAYER", {"name": "BadWord"})["result"] == "ERR_NGWORDINPNAME"
    assert pescmds.dispatch(svc, a, "CMD_CREATE_PLAYER", {"name": "Ann"})["result"] == "NOERR"


def _live_match(svc, rid, sa, sb, mode="OC_RANK"):
    pesmatch._state(svc)["rooms"][rid] = {"owner": sa.pes_pid, "members": [sa.pes_pid, sb.pes_pid], "t": 0,
                                          "mode": mode, "match_id": f"f{rid}", "goals": set()}
    for s in (sa, sb):
        pescmds.dispatch(svc, s, "CMD_CHANGE_GAMEPHASE", {"phase": "1ST", "gtimer": 1})
    return events._st(svc)["live"][rid]


def test_quitter_forfeits(svc):
    from pesserver import pesdivisions
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    a.pes_username, b.pes_username = "u1", "u2"
    lv = _live_match(svc, 5, a, b)
    pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "GIVEUP"})      # Bob quits
    pescmds.dispatch(svc, a, "CMD_SEND_ABNORMALEND", {"reason": "DISCONN_P2P"})  # Ann's game sees the link go
    lv["forfeit_decide"]()
    db = svc.accounts.db
    ra, rb = (db.execute("SELECT rating FROM pes_players WHERE pid=?", (p,)).fetchone()[0] for p in (1, 2))
    assert ra > 1000 > rb
    ia = unb(unb(pescmds.dispatch(svc, a, "CMD_GET_DIVISION_INFO", {})["division_info"])["match_history_list"][0])
    ib = unb(unb(pescmds.dispatch(svc, b, "CMD_GET_DIVISION_INFO", {})["division_info"])["match_history_list"][0])
    assert (ia["win_lose"], ia["division_point"], ia["home_score"], ia["away_score"]) == ("WIN", 3, 3, 0)
    assert ib["win_lose"] == "LOSE"
    assert pesmatch.forfeit(svc, 5, 2) is False                                   # once only
    assert pesmatch._verdict(svc, 5, pesmatch._state(svc)["rooms"][5]) is None    # no rating push later


def test_both_idle_no_winner(svc):
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    lv = _live_match(svc, 6, a, b, mode="OC_QUICK")
    for s in (a, b):
        pescmds.dispatch(svc, s, "CMD_SEND_ABNORMALEND", {"reason": "NO_OPERATION"})
    lv["forfeit_decide"]()
    assert "forfeit" not in pesmatch._state(svc)["rooms"][6]
    db = svc.accounts.db
    assert db.execute("SELECT rating FROM pes_players WHERE pid=1").fetchone()[0] == 1000


def test_often_used_teams(svc):
    import asyncio
    from pesserver import pesdivisions  # noqa: F401
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    for pid, s_ in ((1, a), (2, b)):
        s_.pes_username = f"u{pid}"
        svc.online[pid] = s_
    assert pescmds.dispatch(svc, a, "CMD_GET_OFTEN_USED_TEAM_LIST", {"pid": 1})["used_team_id_list_num"] == 0

    async def go():
        for n, team in enumerate((101, 45, 101)):
            pesmatch._verdict(svc, n + 1, {"owner": 1, "members": [1, 2], "t": n, "mode": "OC_RANK", "match_id": f"o{n}",
                                           "goals": set(), "phase": {1: "END", 2: "END"}, "teams": {1: team, 2: 8}})
    asyncio.run(go())
    r = pescmds.dispatch(svc, a, "CMD_GET_OFTEN_USED_TEAM_LIST", {"pid": 1})
    assert r["used_team_id_list"] == [101, 45] and r["last_used_team_id"] == 101
    assert pescmds.validate("CMD_GET_OFTEN_USED_TEAM_LIST", r)


def test_review_fixes(svc):
    """Fixes from the 2026-11-08 review: block list rules, session pid first, forfeit in the saved result,
    no normal verdict while a forfeit is pending, deleted players leave nothing behind."""
    from pesserver import pesblock, pesdivisions
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    svc.online.update({1: a, 2: b})
    a.pes_username, b.pes_username = "u1", "u2"
    pescmds.dispatch(svc, a, "CMD_SET_MATCHREJECTPID", {"pid": 1, "reject_pid": 999, "is_reject": "YES"})
    assert pesblock.rejects(svc, 1) == []                        # nobody called 999
    announce.tell(svc, 2, "Private", "for Bob only")
    r = pescmds.dispatch(svc, a, "CMD_GET_PRIVATEINFOTITLELIST", {"pid": 2})   # Ann asks for Bob's list
    assert r == {"result": "NOERR"}
    # a quit: the forfeit decides, a normal verdict does not sneak in first
    lv = _live_match(svc, 9, a, b)
    pescmds.dispatch(svc, b, "CMD_SEND_ABNORMALEND", {"reason": "GIVEUP"})
    room = pesmatch._state(svc)["rooms"][9]
    room["phase"] = {1: "END", 2: "1ST"}
    assert pesmatch._verdict(svc, 9, room) is None
    events._st(svc)
    db = svc.accounts.db
    with svc.accounts.lock, db:
        db.execute("INSERT INTO pes_matches(room, started, ended, home_pid, away_pid, home_name, away_name, home_score,"
                   " away_score, reason) VALUES (9, 0, ?, 1, 2, 'Ann', 'Bob', 0, 1, 'GIVEUP')", (__import__("time").time(),))
    lv["forfeit_decide"]()
    row = db.execute("SELECT home_score, away_score, reason, left_pid FROM pes_matches WHERE room=9").fetchone()
    assert tuple(row) == (3, 0, "FORFEIT", 2)
    # delete Bob: nothing of his stays behind
    pescmds.delete_player(svc, 2)
    pesdivisions.forget(svc, 2)
    assert db.execute("SELECT COUNT(*) FROM pes_division_match WHERE pid=2").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM pes_matches WHERE left_pid=2").fetchone()[0] == 0


def test_replay_closed_when_the_recorder_drops(svc):
    from pesserver import pesreplay
    _user(svc, 1, "Ann"); _user(svc, 2, "Bob")
    a, b = Sess(1), Sess(2)
    a.pes_username, b.pes_username = "u1", "u2"
    pesmatch._state(svc)["rooms"][4] = {"owner": 1, "members": [1, 2], "t": 1793900000}
    mid = pescmds.dispatch(svc, a, "CMD_GET_MATCHID", {})["match_id"]
    pescmds.dispatch(svc, a, "CMD_START_SEND_GAME_SEQUENCE_DATA", {"pid": 1})
    assert mid in pesreplay._live
    pesreplay.disconnected(svc, 1)
    assert mid not in pesreplay._live
    meta = dict(pesreplay.recordings(svc))[mid]
    assert meta["closed"] is True


def test_a_new_early_exit_warns_even_when_an_old_one_rolls_out(svc):
    """Grade C with 5 exits in the last 10. The next match is another exit and the oldest one rolls out of the
    window at the same time: still 5. Counting alone, that exit got no "still C" warning."""
    a = Sess(1)
    svc.online[1] = a
    for left in (1, 1, 1, 1, 1, 0, 0, 0, 0, 0):
        pesmanner._add(svc, 1, bool(left), "GIVEUP" if left else "")
    assert pesmanner.rating(svc, 1)["quits"] == 5 and _warn(svc, a) == "STILL_C" and _warn(svc, a) is None
    pesmanner._add(svc, 1, True, "GIVEUP")
    assert pesmanner.rating(svc, 1)["quits"] == 5 and _warn(svc, a) == "STILL_C" and _warn(svc, a) is None
