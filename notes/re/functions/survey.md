# Survey (main session): catalog, error codes, big modes

## Command catalog
433 CMD_* names in the binary (catalog.json). 33 handled by our server, 40 more seen in our logs and answered
with a bare NOERR, 360 never seen (their menus were never opened). Areas: myClub 125, Match 48, Co-op 42,
Store & platform 30, Login & account 28, Matchday 20, Clans 17, Lobby & rooms 17, Connectivity 17,
PES League 17, In-match sync 14, News & info 12, Events 12, Social 11, Matchmaking 7, Divisions 6,
Fair play 4, Random Selection 4, Spectating 2.

## Error codes [static]
186 call sites of the error reporter 0x1411546b0(mgr, func-name string, file path, line r9d, sub-code [rsp+0x20]).
Two on-screen formats share the 4-letter source-file hash (errcode.py):
- `XXXX<line>` e.g. EYRH763 (OnlineModeTaskQuickMatching.cpp line 763)
- `XXXX_<sub-code 4 digits>_...` e.g. GNCX_0003_0x20000000 = OnlineModeMultiplay.cpp sub-code 3 (site at line 505);
  MMGC_0002 = MultiplayCmdTeamDataSync.cpp sub-code 2 (line 118).
errtable.json maps every (file, line, sub-code) → nearest message (e.g. MXNV374 "Error SetConfig()",
MXNV431 "Error CreateMultiplaySession", MXNV522 "Revison check TIMEOUT", MXNV533 "DATAPACK error",
MXNV537 "Other user disconnected", QULW194 ERR_NGWORDINPNAME on CmdCreatePlayer, SGJC1006 live-data "wrong hash").
Message pairing is nearest-string, so treat as [inferred] unless confirmed in a group report.

## myClub (125 commands) [static fields, inferred semantics]
Server-authoritative for everything: club (CREATE_MYCLUB_TEAM base_team_id, coach), squads (CREATE/COPY/
SET_MYCLUB_SQUAD_*, gameplan_data blobs), owned players (GET_MYCLUB_GAMEPLAYERLIST: gameplayer_id = the game's
own player DB id, gameplayer_unique_id = the owned card, level/exp/contract_term/rating/tiredness/
parameters), coaches, agents and draw rates (GET_MYCLUB_MARKET_AGENTLIST, GET_MYCLUB_AGENT_DRAW_RATE,
USE_MYCLUB_AGENT), scouts + auctions (~18 cmds, live bidding pushes WATCH_MYCLUB_AUCTION_SCOUT_DETAIL),
items/trainers (USE_MYCLUB_*_ITEM, staff), presents/login bonus/achievements, GP + myClub coin
(pes_point), weekly rankings (HTTP), VS COM (GET_MYCLUB_VSCOM_OPPONENT team_data, SET_MYCLUB_VSCOM_GAMERESULT
→ rating), Sim matches, co-op squads. A faithful revival needs Konami's card parameters (featured/iconic
players' boosted stats), agent pools and economy tables, none of which are in the client.
Feasible subset ("myClub lite"): base players from the local DB as cards, fixed default coach list, free
agents with our own pools, VS COM + PvP with our own rating. Large effort.

## Clans (17), PES League (17), Matchday (20), Events/COMPEV (12) [static fields]
- Clans: create/search/join/request/expel, settings, member online mode, HTTP clan ranking with division.
  Mostly CRUD on a clan table + lists of player blobs (pid, pname, online ids).
- PES League (eSports): competitions with entry windows (GET_PESLEAGUE_COMPE_LIST/INFO/RANKING/RESULT_LIST/
  REWARD), 1v1 main menu with matching times. Needs a scheduler + standings.
- Matchday: two-sided group events with pickup matches (START_MATCHDAY_PICKUP_MATCHING, push
  WATCH_MATCHDAY_PICKUP_MATCHING with member host_addr like quick match), points, final watched match.
- Events (COMPEV): event categories with banners, VS COM / online competitions with progress counters.

## Social (11)
- Friends = **follow list**: FOLLOW_USER, GET_FOLLOW_LIST (pid, pname, is_locked, is_clan_member), LOCK_FOLLOW_USER.
- Text chat: SEND_TEXTCHAT → push WATCH_TEXTCHAT {channel, from_pid, name, statement} — server relays.
- Blocks: GET_BLOCKLIST {bklist{block_id, name, player_num, max_player_num}}, JOIN_BLOCK/LEAVE_BLOCK,
  REQWATCHL_BLOCK_MATCH_LIST — "blocks" look like lobby sub-areas (see rooms report).

## Fair play (4)
CHECK_CHEAT, GET_MANNER_WARN {warn_kind}, SEND_KICK_REQUEST, SEND_TAMPER_DATA {pid, match_category, data,
error_code} (an anti-tamper report the game sends; never seen in our logs).

## In-match sync (14)
TEAM_DATA_SYNC, USER_DATA_SYNC, BL_DATA_SYNC, SYNC_COMMENTARY, SELECT_SIDE, COMPLETE/UPDATE_TEAM_SELECT,
CMD_ERR, CMD_LACK, CMD_SENDFAILED, LOST_GUEST, SET_GUEST, REVISION_CHECK, RECONNECT {pid, platform,
patch_version, dlc_version, online_mode → key, key_size}. Most have no request/reply schema: likely
peer-to-peer multiplay commands (MultiplayCmdTeamDataSync.cpp), not server commands [inferred].
