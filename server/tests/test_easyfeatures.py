"""Courtesy rating (pesmanner.py), the co-op profile and block list (pescoop.py), messages in a player's
Information list (announce.py)."""
import msgpack

from pesserver import announce, events, pescmds, pescoop, pesmanner, pesmatch  # noqa: F401  (handlers)
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
