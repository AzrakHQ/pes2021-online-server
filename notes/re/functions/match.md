# Group "match" — online match setup, keep-alive, teardown (PES 2021 1.01.00, static RE)

## Corrections from later findings (2026-11-05)
- Live (livetests.md): CMD_RECONNECT survives a ~7 s server outage during a match (APLR_0002 at ~15 s); in the menus an outage gives VNBP_0002 with no reconnect. The AFK end came after about 70 s without input.


Tags: **[static]** read in code (address cited), **[log]** seen in our traffic (logs/*.log), **[inferred]**, **[guess]**.
Working files: the working folder (dumps not published; general tools in `notes/re/functions/tools/`) (asm dumps, `errtable.txt` = the client's per-command server-error table,
`parserofs.py`, `enumtbl.py`).

## How it works (overview)

After WATCH_MATCHING FINISH, `TaskQuickMatching` (0x14111a690) creates `onlinemode::TaskMatchSession`
(ctor 0x141142e20, update fn 0x1411430b0, vtable 0x142878280) and fills its member-pid vector (task+0x90) from
the WATCH_MATCHING members and a ping limit (task+0x88) [static 0x14111aa33..0x14111aa78]. The session task does
the server-side part of setup (match id, sync barrier, server env, TURN list), then builds the P2P multiplay
session. Peer-to-peer connection itself runs in an OnlineSystem thread (0x1411d9620) that uses
SEND/WATCH_SYSTEM_NOTICE START/END as a rendezvous through our server. When the session succeeds, the quick-match
task continues: SEND_MATCHING_RESULT → (owner only) SET_GAMEMEMBER → SET_GAMEMEMBERENV → team sync etc.
[static 0x14111df30 states 0xe..0x17].

During the match, every server report (CHANGE_GAMEPHASE, ADD_FOUL, ADD_SCORE, SET_GAMEPLAYER_PLAYTIME,
SEND_ABNORMALEND, SEND_ALIVE, …) is not sent directly: the game pushes an `onlinesystem::gamequeue::<Cmd>` object
into the OnlineSystem game queue; `TaskGameQueueProcessor` (0x141124df0) and per-type functors
(0x141120ed0, 0x141121090, … 15 of them) turn each into a CommandObject and wait for the reply. **The only reply
value the queue reacts to is `result == "ERR_CHEAT"`** (see below). A per-match watchdog in match::Online
(0x14213dab0, MatchOnline.cpp) enforces the GET_SERVER_ENV timeouts and raises abnormal ends.

Real sequence (server-dev.log 18:19:24 → 18:53:04, full 90-min match) [log]:
```
WATCH_MATCHING(push) → GET_GAMERELAY(HTTP) → GET_MATCHID → SEND_SYNC_NOTICE "1,"/"2," → (WATCH_SYNC_NOTICE push)
→ GET_SERVER_ENV → [GET_TURN_SERVER_LIST HTTP] → SEND_SYSTEM_NOTICE "START:pid=..;time=..;" (both) → push
→ SEND_SYSTEM_NOTICE "END:pid=..;time=..;" (both) → push → ~10–25 s P2P/relay quality check →
SET_GAMERELAY_CHECK_RES → WATCH_GAMERELAY_CHECK_RES(push) → SEND_MATCHING_RESULT (both) → SET_GAMEMEMBER (owner)
→ CHANGE_GAMEPHASE DATASYNC → SET_GAMEMEMBERENV (owner) → GET_PLAYER_PARAMETERLIST → SET_GAMETEAM + GET_MATCHID
→ CHANGE_GAMEPHASE TEAMSELECT → MATCHMENU → GAMELOAD → SEND_MATCH_TEAM_INFO → ENTRY → 1ST … ADD_FOUL/ADD_SCORE …
BREAK1 → 2ND … → SET_GAMEPLAYER_PLAYTIME → CHANGE_GAMEPHASE END → SET_PLAYDATA_MATCH(HTTP) → SEND_ABNORMALEND
DISCONN_P2P (2nd leaver) → SEND_STATS_DATA → SEND_GAME_RESULT → REQ_GAMEADDPOINT → LEAVE_ROOM
```

## TaskMatchSession state machine (fn 0x1411430b0, state = task+0x70, jump table 0x141144420)

Task fields: +0x70 state, +0x74 result code, +0x78 retry counter, +0x80 retry timestamp, +0x88 ping limit,
+0x8c "skip match id" (= game-mode global [0x140ff0a20()+0x58]→dword == 3, i.e. direct/LAN online [inferred]),
+0x90/+0x98 member pid vector, +0xa8 multiplay session config (type dword at +0xa8), +0x118 CmdGetMatchid,
+0x120 CmdStartSendGameSequenceData, +0x128 CmdGetServerEnv, +0x130 CmdGetTurnServerList, +0x138 CmdSendSyncNotice,
+0x140 sync-notice timestamp, +0x148 error text. `cmd+0x98 == 1` means "reply result was not NOERR"
(confirmed by the game-queue functor reading the result string only when +0x98 != 0, 0x141120f63) [static].

Every tick first checks the gate connection (0x14114e7e0(mgr,2,0)); if lost → MXNV_0002 "DISCONNECT PES SERVER",
result 6, state 23 [static 0x1411430e3..0x14114323e].

| st | what it does | waits for / next | error (on-screen code = MXNV_00NN, NN decimal) |
|---|---|---|---|
| 0 | → 1 | | |
| 1 | if +0x8c: skip → 2. Else create **CmdGetMatchid** (factory 0x140ff7aa0) | → 2 | create fail: MXNV_0003 (line 155; msg text wrongly says "Failed to create CMD_GET_SERVER_ENV"), result 6 |
| 2 | poll GET_MATCHID. If reply error → fatal. Then if [g+0x58]==1 (normal PES-server online) build+send **SEND_SYNC_NOTICE** (0x141144a10) → 3; else → 5 | | "Command error CMD_GET_MATCHID" MXNV_0004 (l.170), result 5; send fail MXNV_0023 (l.195) |
| 3 | poll SEND_SYNC_NOTICE reply; record now → +0x140 | → 4 | reply error MXNV_0024 (l.206), result 5 |
| 4 | 0x141145640: wait for one WATCH_SYNC_NOTICE in queue, **max 10 000 ms**; timeout is NOT an error | → 5 (got one or timeout) | only if OnlineSystem gone: MXNV_0025 (l.231) |
| 5 | if GetMatchid reply `upload == "YES"` (0x140ff7cd0; or debug param `direct_online_observe_record`==1) create **CmdStartSendGameSequenceData** → 6; else → 7 | | create fail MXNV_0005 (l.248) |
| 6 | poll START_SEND_GAME_SEQUENCE_DATA | → 7 | reply error MXNV_0006 (l.264), result 5 |
| 7 | create **CmdGetServerEnv** | → 8 | create fail MXNV_0007 (l.277) |
| 8 | poll GET_SERVER_ENV; reply error → fatal. Then [g+0x58]==1 → 9, else → 11 | | "Command error CMD_GET_SERVER_ENV" MXNV_0008 (l.295), result 5 |
| 9 | create CmdGetTurnServerList (HTTP) (null → 11) | → 10 | none |
| 10 | wait TURN list (result ignored here) | → 11 | none |
| 11 | SetConfig() 0x141144d80 (builds P2P/TURN config, also checks `upload`) | ok → [+0xa8]==0 ? 12 : 14 | MXNV_0009 "Error SetConfig()" (l.374) |
| 12/13 | 0x14114b630 helper (NAT/session prep) wait | → 14 | |
| 14/15 | OnlineModeMultiplay init 0x141000e60, wait 0x141001c00 | → 16 | |
| 16 | if retry counter ==1 wait 15 000 ms (0x3a98) | → 17 | |
| 17 | CreateMultiplaySession 0x1411444e0 | → 18 | MXNV_0010 (l.431) |
| 18 | get daemon/backend; session state 0x1411ac0c0: <4 wait, ==4 connected → (multiplay+0x209) → 20; >4 → 19 | | MXNV_0011/0012 (null daemon/backend); **MXNV_0013 (l.476) session failed** |
| 19 | retry: only if [+0xa8]==0 and ++retry<2 → 14; ctor sets retry=2 (no retry) unless platform id==0x10 | else → 23 | |
| 20 | InitOnlineModeMultiplay 0x141144570 | → 21 | MXNV_0014 (l.495) |
| 21 | P2P revision check, multiplay+0x5c4: 0 wait, 1 ok, 2 "Revison check TIMEOUT" (60 s, 0x141003320), 3 "Revision error [..]", 4 "DATAPACK error", 5 "Other user disconnected", other "UNKNOWN" | ok → 22 | MXNV_0015/0016/0017/0018/0019; result 2 (timeout, peer gone) or 3 |
| 22 | 0x141144600: if ping limit +0x88 !=0 and [+0xa8]==1, ping check (0x14115c830) | → 25 | "Ping range over" MXNV_0022 (l.850), result 4 |
| 23/24 | error teardown (multiplay stop 0x141000e60, wait) | → 25 | |
| 25 | finish 0x1411455b0 | → 26 (done) | |

Result +0x74: 1 ok, 2 peer disconnected / revision timeout, 3 multiplay failure, 4 ping range over, 5 server command
error, 6 internal/create failure. [static]
[log] MXNV_0013 appears in SEND_MATCHING_RESULT.error_code and LEAVE_ROOM.error_code in early failed P2P tests —
consistent with "multiplay session never reached state 4".

## Notice formats (all three SEND/WATCH pairs)

All three request packers send `{pid_list_num, pid_list[int], data STR}` and the pushes carry `{data STR}`
(parsers 0x141e457f0 / 0x141e437d0 / 0x141d87a50: data → Result+0x1d0) [static]. The client never looks at who
sent a notice except via what's inside `data`.

### CMD_SEND_SYNC_NOTICE / CMD_WATCH_SYNC_NOTICE — match-session barrier
- Transport: gate MENU. Built in 0x141144a10 (TaskMatchSession state 2).
- `data` = for every member pid ≠ my pid: `"<pid>,"` (ostream `<< pid << ","`) → 1v1: pid 2 sends `"1,"`,
  pid 1 sends `"2,"` [static 0x141144b40..0x141144b70; log]. Meaning: "I reached the sync point; addressed to these pids".
- `pid_list` is copied from a **global vector that the game never clears between matches** (pushed with
  0x141144970 into [OnlineSystem+0x30]+8; only the CmdWatchSyncNotice ctor 0x141210960 clears it). So pid_list grows
  by one copy per match in a session: logs show `pid_list_num 5, pid_list [1,1,1,1,1]` on the 5th match [log].
- Receiver: WATCH_SYNC_NOTICE data is queued (0x141210b20 → [g+0x30]+0x40). TaskMatchSession state 4 pops ONE entry
  and **ignores its content** (0x141144860 copies the string and discards it) [static]. 10 s timeout, no error.
- Misbehaviour: none fatal. Duplicates leave stale entries in the queue, so the *next* match's barrier passes
  instantly (no real synchronisation). A non-NOERR reply to SEND_SYNC_NOTICE aborts the session (MXNV_0024).
- **Server should:** dedupe pid_list (`set(pid_list) - {sender}`), push `CMD_WATCH_SYNC_NOTICE {result NOERR, data}`
  once per target. Reply NOERR. (Today pesmatch relays N copies.) Confidence high.

### CMD_SEND_SYSTEM_NOTICE / CMD_WATCH_SYSTEM_NOTICE — P2P connect rendezvous (START/END)
- Built in 0x1411d45f0, called from the P2P connect thread 0x1411d9620 (via 0x1411da320 START, 0x1411da200 END).
- Format: `"<KEY>:pid=<my pid>;time=<t>;"` with KEY ∈ {`START`, `END`} (only two keywords exist; pointer slots
  0x1434d6428/30, separators ':' 'pid=' ';' 'time=' at 0x1434d6438..50). `pid` = sender's own pid (obj+0x908);
  `time` = 0x14115fec0() = current server-synchronised UNIX seconds (obj+0x89c) [static; log time=1793809165].
- pid_list = the other session members (obj+8 + i*0x214, count obj+0x438). Context ([g+0x28]) is cleared after
  every connect attempt (0x1411d980d..), so no accumulation here [static; log pid_list_num 1].
- Protocol per handshake (0x1411da320/0x1411da200): send notice; wait for the SEND reply (abort flag obj+0x8f8);
  then poll every 16 ms for **up to 30 000 ms** (0x7530) until the *head* of the WATCH queue
  contains KEY, has `pid=` that is in the member list, and has `time=` (stored obj+0x8a0); then pop it
  (0x1411d9e60) [static]. Sequence: START handshake → P2P connect steps 0x1411d81d0, 0x1411d7700 → END handshake →
  state obj+0x868 = 4 (success); any failure → cleanup, state 3→2 [static 0x1411d9789..0x1411d97c0].
- **Head-of-queue bug:** the parser only peeks the front entry and does not pop it when it doesn't match. A
  duplicate/stale START (or any foreign text) at the head blocks the END wait → 30 s timeout → P2P connect fails
  [static 0x1411d9ec8..0x1411da050].
- `time` is stored but its consumer was not found [inferred: clock-offset/telemetry].
- **Server should:** relay `data` unchanged as `CMD_WATCH_SYSTEM_NOTICE {result NOERR, data}` exactly once to each
  listed pid, preserving order (START before END per sender). Never send it to the sender; never inject extra
  notices. Reply NOERR. Optionally store per-room "START seen/END seen" for diagnostics (both END ⇒ P2P up).

### CMD_SEND_NOTICE / CMD_WATCH_NOTICE — JSON room notices (not used by Quick Match)
- `data` is a **JSON object** (picojson-style builder 0x140383xxx, serializer 0x1403971b0) with
  `notice_key` + fields [static 0x141090720..0x141091b00]:
  - `MOMROOM_START_COUNTDOWN` {from_pid, reason, count_zero}
  - `MOMROOM_RESULT_TIMEOUT` {from_pid, timeout}
  - `MOMROOM_REQ_RESULT_TIMEOUT` {from_pid, roomid}
  - `ROOM_SHARE_INFO` {from_pid} or {from_pid, has_team_data YES/NO, team_power, chemistry, team_id, team_name}
  - `SYNC_PROGRESS` {pid_list}
  - keys present but no builder found: MOMROOM_CANCEL_COUNTDOWN, MOMROOM_REQUEST_COUNTDOWN,
    MOMROOM_SEND_XSESSION_INFO (+`xsession_info`), MOMROOM_CONNECT_COMPLETE, QUICK_PING_RES.
- Users: TaskSyncSelectSideInMulti, TaskMOMLobbyRoomMain, TaskLobbyRoomMain, TaskSyncProgress (0x141147880),
  ProcessOnlineLobbyMultiPostCreateJoinRoom — lobby/co-op rooms. Never seen in our logs [log].
- **Server should:** same generic relay as the other two (once per pid, dedupe). Needed when rooms/co-op land.

## CMD_GET_MATCHID — server-assigned match id
- Transport gate MENU; request `{svrtype, client}`. Sent by TaskMatchSession (state 1) and again by the
  team-select flow with SET_GAMETEAM (0x141376730; coop 0x141135600) [static; log both].
- Reply: `match_id` (any type; non-nil converted to string, Result+0x1d0) and `upload` STR (Result+0x1f0).
  `upload == "YES"` (enum table 0x1437f3868: 0 "NO", 1 "YES") → client sends CMD_START_SEND_GAME_SEQUENCE_DATA and
  starts streaming CMD_SEND_GAME_SEQUENCE_DATA {pid, match_id, game_seq_data[{type, fromId, channelId,
  sequenceNumber, data, data_len}], game_seq_data_num} (SendGameSeqTask 0x1411daed0) — replay/observer upload
  [static 0x140ff7cd0, strings 0x142b3b758]. match_id is later echoed in SEND_STATS_DATA and goes to error telemetry
  (MATCH_ID) [log; inferred 0x1411bb3b0].
- Errors: non-NOERR → MXNV_0004, session aborted. Documented server errors: ERR_DATABASE, ERR_ROOMNOTFOUND.
- **Server should:** keep current behaviour (stable per-room id string, `upload: "NO"`). Same id for both players
  and for the second GET_MATCHID. Do not send `upload: YES` unless we implement SEND_GAME_SEQUENCE_DATA.

## CMD_START_SEND_GAME_SEQUENCE_DATA
- Only sent when GET_MATCHID.upload == "YES". Request `{pid, svrtype, client}`. Reply: result only.
  Non-NOERR → MXNV_0006, abort. Never seen [log]. **Server:** NOERR if ever received.

## CMD_GET_SERVER_ENV — match environment
- Sent from TaskMatchSession state 7 (+ elsewhere at login 0x1401dbec0). Request `{svrtype, client}` (both
  "NotImplement" in logs) [log].
- Parser 0x141e46950 reads more keys than kb listed. Copied **unconditionally** into ParameterEnvironment
  ([OnlineSystem+0x10]) by 0x141210dd0, so missing keys keep the Result-ctor defaults (0x141e45ee0) [static]:

| key (type) | default | env off | use |
|---|---|---|---|
| match_client_timeout_load_timeout NUM | 180 | +0x3a0 | seconds; GameModeMatchListenerMatch counts frames per load state, limit = fps × value → LOAD_TIMEOUT, QVHE1459 [static 0x141fd4d29] |
| match_client_timeout_long_outofplay NUM | 120 | +0x3a4 | seconds ×1000 → match watchdog reason OUTPLAY_LONG [static 0x14213db5a] |
| match_client_timeout_init_not_end NUM | 240 | +0x3a8 | ×1000 → watchdog reason LOAD_TIMEOUT (match init not finished) [static 0x14213dbde] |
| match_client_timeout_cmd_lack NUM | 180 | +0x3ac | ×1000 → watchdog reason CMD_LACK [static 0x14213dc67] |
| trigger_keep_alive NUM | 1 | +0x398 | read in 0x14121c950.. (OnlineSystem) [inferred keep-alive switch] |
| matchrejectpid_max NUM | 0 | +0x39c | match reject list size |
| quality_check_wait_sec NUM | 3600 | +0x390 (×1000 ms) | relay-quality re-check interval |
| is_revision_check STR | "YES" | +0x38c (==YES) | revision check switch (consumer 0x141252740) |
| is_check_myclub_level STR | "YES" | +0x38d | myClub |
| max_division STR | "D12" | +0x380 (index in 13-entry enum 0x141b99db0) | divisions |
| teamlobby_game_rating_border_normal/hard NUM | 600/700 | +0x384/+0x388 | team lobby |
| log_update_level NUM | 0 | +0x3b0 | telemetry |
| konamiid_api_retry_sec NUM | 0 | +0x3b4 | |
| lobby_ping_good/normal NUM | 0/0 | +0x3b8/+0x3bc | ping bars |
| quick_ping_good/normal NUM | 0/0 | +0x3c0/+0x3c4 | quick-match ping bars |
| proto_opt STR | "NotImplement" | → 0x141174b20 ([OnlineSystem+0x40]) | protocol option hash (PROTO_OPT_HASH in logs) |

- With the timeouts = 0: init_not_end 0 ⇒ immediate LOAD_TIMEOUT (what we hit before) [log + static].
  In mode [match+0x116c]==1 the watchdog uses hard-coded values instead (10 s/300 s/16 s/30 s) [static].
- Errors: non-NOERR → MXNV_0008, abort. Documented: ERR_DATABASE.
- **Server should:** keep sending the four timeouts (our 180/120/240/180 = defaults); fine to omit the rest. If we
  ever want longer pauses/AFK tolerance, raise long_outofplay/cmd_lack.

## Match watchdog / abnormal end (MatchOnline.cpp 0x14213dab0, MatchOnlineObserverForPesServer.cpp 0x142167e60)
Per match state it arms a timer (ms) with a reason index (esi) into the **abnormal-end reason enum** (table
0x1437f3d20, init 0x140216d70) [static]:

`0 CHEAT, 1 INJURY, 2 GIVEUP, 3 EXIT, 4 SLOWDOWN, 5 NO_OPERATION, 6 COMPE_TIMELIMIT, 7 CMD_ERR, 8 MODE_ERR,
9 SYNC_FAILED, 10 CONFLICT, 11 LOAD_TIMEOUT, 12 GAMERELAY, 13 BUFFER_OVER, 14 DEGRADE_SC, 15 DISCONN_SC,
16 CMD_SENDFAILED, 17 OUTPLAY_LONG, 18 CMD_LACK, 19 TIME_ERR, 20 NOTRUN, 21 READY_TIMEOUT, 22 DISCONN_NTL,
23 DISCONN_P2P, 24 DEGRADE_P2P, 25 NOERR`

Watchdog arms: OUTPLAY_LONG(long_outofplay), LOAD_TIMEOUT(init_not_end), CMD_LACK(cmd_lack), DISCONN_P2P (10 s /
300 s or 30 s), NOTRUN (60/90 s), SYNC_FAILED (180 s), BUFFER_OVER, NO_OPERATION ((setting+30)×2 s or
(setting+60)×1 s — the AFK rule) [static 0x14213db52..0x14213dd45].

## CMD_SEND_ABNORMALEND — tell server a match ended abnormally
- Transport gate MENU, via game queue `gamequeue::AbnormalEnd` (queue entry: +0x10 reason idx, +0x18 hex code,
  +0x1c 2). Request `{reason STR (enum above, from obj+0x130), error_code STR}`; error_code =
  `<last on-screen code>_0x<8 hex>` (0x1410f5b80, "_0x") [static 0x1410f5d80; log].
- Who queues what [static, sites of vtable 0x1425ba188]:
  - GameModeMatchListenerMatch 0x141fd4de7: LOAD_TIMEOUT (QVHE1459) — also DEGRADE_SC variant.
  - OnlineModeMultiplay 0x1410027e0: variable (DISCONN_P2P with GNCX_0003 seen).
  - TaskQuickMatching ActSubSendAbnormalend 0x14111c760: GAMERELAY (12) / DEGRADE_SC, code 0x20000000 (EYRH1530).
  - TaskSyncTeamData 0x14107a140, TaskSyncSelectSideInMulti 0x1410869c0, TaskSyncManager 0x14112d1f0,
    RsLottery 0x1413760e0: MODE_ERR (8), code 0x20000000.
  - TaskWatchGame 0x141120e40: CMD_ERR (7). 0x141070fd0: COMPE_TIMELIMIT (6).
  - GameQueueProcessor ERR_CHEAT handlers (0x1411233b0 … 0x141124c30): CHEAT (0), FAMY242.
  - MatchOnlineObserverForPesServer 0x142167e60: watchdog reasons (DISCONN_P2P, TIME_ERR, …), incl. NO_OPERATION
    (VIXT2253 = MatchOnline.cpp line 2253).
  - MenuMatchDiscontinueDialog 0x1404e55f0: player quits from pause menu [inferred GIVEUP/EXIT].
- Seen [log]: DISCONN_P2P/GNCX_0003 (×7, always the 2nd player leaving post-match), NO_OPERATION/VIXT2253 (×5),
  LOAD_TIMEOUT/QVHE1459 and /MMGC_0002_0_262143 (×2 each), GAMERELAY/EYRH1530, DEGRADE_SC/EYRH1530.
- Reply: result only; through the game queue only ERR_CHEAT matters. Documented: ERR_DATABASE, ERR_NOTINROOM,
  ERR_ROOMNOTFOUND.
- **Server should:** reply NOERR; store {room, pid, reason, error_code, phase, gtimer}; mark the match abnormal and,
  if the match was running, treat the *other* player as the one who stayed (DISCONN_* from the reporter usually
  means the peer left). Ignore DISCONN_P2P/GNCX_0003 that arrives after both sent CHANGE_GAMEPHASE END.

## CMD_CHANGE_GAMEPHASE — phase report (game queue)
- Request `{phase STR, gtimer NUM}` (packer strings 0x142b0e748). Phase enum (table 0x1437f2420, init 0x140219600)
  [static]: `0 DATASYNC, 1 TEAMSELECT, 2 MATCHMENU, 3 GAMELOAD, 4 ENTRY, 5 1ST, 6 1ST_15MIN, 7 1ST_30MIN, 8 BREAK1,
  9 2ND, 10 2ND_15MIN, 11 2ND_30MIN, 12 BREAK2, 13 EX1ST, 14 BREAK3, 15 EX2ND, 16 BREAK4, 17 PK, 18 END`.
  gtimer = game clock in 1/1000 s? (BREAK1 at 43213, END 94107 ≈ 90 min + stoppage at ~1.05 units/game-s) [log; guess on unit].
  Both clients report every phase [log].
- Reply ignored except `ERR_CHEAT` → client raises CHEAT abnormal end (FAMY242) [static 0x141120fe9]. Other errors
  just mark the queue entry failed (entry+9) [static]. Documented errors: ERR_DATABASE, ERR_NOTGAMEHOST,
  ERR_NOTINROOM, ERR_NOTROOMOWNER, ERR_ROOMNOTFOUND, ERR_ROOMSTATNOTMATCH.
- **Server should:** NOERR always; store per-room phase from the home (owner) report. Never ERR_CHEAT.

## CMD_ADD_FOUL / CMD_ADD_SCORE — in-match events (game queue)
- ADD_FOUL request: `card_pid, card_side HOME/AWAY, card_gameplayer_id_info (blob {gameplayer_unique_id,
  gameplayer_id}), card_kind (NONE/YELLOW/…), injury_pid, injury_side, injury_gameplayer_id (blob), injury_data
  (blob {part, symptom, level, mcount_left}), injury_all, injury_one, gtimer` [static packer 0x141d1bed0; log].
- ADD_SCORE request: `goal_side, goal_gameplayer_id_info (blob), goal_player_side, goal_player_pid,
  assist_side, assist_gameplayer_id_info, assist_player_side, assist_player_pid (0 = none), gtimer` [static
  0x141d1d870; log 22:28:02 `goal_side AWAY, goal_player_pid 2`]. Both clients send each event (same gtimer).
- **There is no CMD_ADD_GOAL in the binary** (no string) — `events.py` waits for CMD_ADD_GOAL, so live scores never
  update; switch it to CMD_ADD_SCORE/`goal_side` and dedupe by (room, gtimer). Own goals: goal_side ≠
  goal_player_side [inferred]. Replies: NOERR (ERR_CHEAT would trigger CHEAT end).

## CMD_SET_GAMEPLAYER_PLAYTIME (game queue)
- Request `player_list_num, player_list[blob {gameplayer_id_info{gameplayer_unique_id, gameplayer_id},
  play_time (minutes, 90), position (GK/CB/…)}], opponent_player_list_num, opponent_player_list[…]`, sent by both
  just before phase END [static 0x141d6e080; log]. **Server:** NOERR; optional stats.

## CMD_SET_GAMETEAM
- Request `{home_team_id, away_team_id}` (packer strings 0x142b1b008), sent by both at TEAMSELECT, followed by
  GET_MATCHID [log]. 262143 (0x3ffff) = "no team" (seen with failed team sync MMGC_0002_0_262143) [log].
- Reply result only. **Server:** NOERR; store team ids per room (home = owner).

## CMD_SET_GAMEMEMBER / CMD_SET_GAMEMEMBERENV (owner only)
- Quick-match states 0x12–0x15 (0x14111df30): only when 0x14111e410 says this client is the owner → SET_GAMEMEMBER
  `{}` then SET_GAMEMEMBERENV `{list_num, envlist[blob {pid, side HOME/AWAY, sideLeader YES/NO}]}` [static; log one
  per match]. side "NotImplement" in early logs = before we sent `side` in WATCH_MATCHING [log].
- Reply errors ARE checked here (cmd+0x98 → 0x141154560 + fail path 0x14111e680) → match setup aborts
  [static 0x14111cd88, 0x14111d358]. Documented errors: ERR_NOTINROOM, ERR_NOTROOMOWNER, ERR_PLAYERISNOTENOUGH,
  ERR_ROOMNOTFOUND, ERR_ROOMSTATNOTMATCH / ERR_MSGFORMAT, ERR_NOTGAMEHOST.
- **Server should:** NOERR; record envlist sides (authoritative HOME/AWAY for result accounting).

## CMD_SEND_MATCHING_RESULT
- Sent by both after the match session (quick-match state 0xf). Request: `member` and `vs_member` = **nested msgpack
  blobs** `{pid, host_addr{address_family, sa_data}, reflexive_addr{…}, relayed_addr{…}, ntl_nat_type}` (self and
  opponent), `error_code` ("" ok, else e.g. MXNV_0013 / EYRH1050), `game_sycom` (P2P / NotImplement),
  `gamerelay_ip_addr` (NotImplement), `gamerelay_ip_port`, `quality_select_kind` (SELECTED/UNSELECTED),
  `quality_antenna` (0/1) [static packer 0x141c7e5a0; log].
- Reply: wait function 0x14111c9c0 does not check the result; it branches on the session state [static].
- **Server should:** NOERR; log it — it's the best per-match connectivity record (non-empty error_code = setup failed).

## CMD_LEAVE_ROOM
- Request `{error_code}`: "" after a normal match, otherwise the last on-screen code (EYRH763 matching timeout,
  GNCX_0003 peer link closed, MXNV_0013, VIXT2253 NO_OPERATION) [log]. Senders 0x141134980, 0x14113a3e0, 0x14137e390.
- **Server should:** NOERR; remove pid from room; close room when empty; error_code tells why.

## CMD_SEND_ALIVE / CMD_SEND_ALIVEPLAYER / CMD_WATCH_CHECK_ALIVE_PLAYER — server-driven liveness probe
- CMD_WATCH_CHECK_ALIVE_PLAYER is a **server push** (watch registered by TaskWatchGame 0x141120730, which also
  watches CmdWatchGameaddpoint). On receipt the client (0x1410d8a40) queues
  `gamequeue::SendAlive {pid = my pid}` and, if a multiplay session is up, `gamequeue::SendAlivePlayer
  {pid, pid_list(+num) of P2P peers it still hears}` [static; packer strings 0x142b182a8].
- CMD_SEND_ALIVE request `{pid, reason}`; reason = current match status in the abnormal-end enum (NOERR normally).
  If status is NOERR in an online match it probes the P2P link (0x14115d6a0): link down → status DISCONN_SC (15),
  link up but server path degraded → DEGRADE_SC (14) (LZFP101/LZFP106) [static 0x1410f6220].
- Never seen in logs (we never push the probe) [log].
- **Server could:** when a client's gate session or P2P goes quiet mid-match, push
  `CMD_WATCH_CHECK_ALIVE_PLAYER {result NOERR}` to the *other* player and use their SEND_ALIVE/ALIVEPLAYER answer to
  decide who disconnected. Reply NOERR. Optional.

## Reconnect (OnlineSystemClientReconnect.cpp)
- Task "reconnect" (0x1411e1e70) is started by OnlineSystem `DisconnectAll` (0x14114daf0) after a gate drop;
  state machine 0x1411e1a00 (states 1–10): tear down → CMD_GET_SVRLIST (0x1411e2140) → reconnect gate →
  **CMD_RECONNECT** `{pid, platform, patch_version, dlc_version, online_mode}` → reply `{key STR, key_size NUM}`
  (same shape as SET_CURRENTPLAYER's session key) → resume [static; kb cmd CMD_RECONNECT].
  Documented errors: ERR_DATABASE, ERR_LOGIN_FAILED. Errors APLR (line 102, 497).
- Today unhandled → bare NOERR with **no key** [guess: client then can't install a session cipher and the
  reconnect fails].
- **Server should:** implement CMD_RECONNECT exactly like CMD_SET_CURRENTPLAYER (new key + key_size, bind the new
  socket to the existing pid/session and its room). Needs a live test.

## Server error table (bonus)
The client carries a table of (command, server error code, message id) at 0x1428a1170, 765 rows
(the working folder (dumps not published; general tools in `notes/re/functions/tools/`)). It lists exactly which ERR_* each command may return — useful for every group.

## Recommendations (summary for our server)
1. Notices: dedupe `pid_list`, exclude sender, deliver each WATCH_*_NOTICE exactly once, keep order. (pesmatch now
   relays duplicates — harmless for SYNC, dangerous for SYSTEM START/END if it ever happens there.)
2. Live score: handle **CMD_ADD_SCORE** (`goal_side`), not CMD_ADD_GOAL (does not exist); dedupe both reports.
3. Never return `ERR_CHEAT` to any in-match command (forces CHEAT abnormal end). Keep `upload: "NO"`.
4. Keep GET_SERVER_ENV timeouts non-zero (seconds); they are the AFK/load/command-lack limits.
5. Store: match_id, sides (SET_GAMEMEMBERENV), teams (SET_GAMETEAM), phases, fouls/goals, ABNORMALEND
   {reason,error_code}, MATCHING_RESULT error_code, LEAVE_ROOM error_code.
6. Implement CMD_RECONNECT (key like SET_CURRENTPLAYER). Optional: push WATCH_CHECK_ALIVE_PLAYER to arbitrate
   disconnects.

## Still unknown
- Exact meaning of `[0x140ff0a20()+0x58]` values (1 = normal online, 3 = no match id/direct) and of task+0xa8 types
  (0/1/3) — would need a debugger or the QuickMatching init path; affects only retries and ping check.
- Who sets multiplay+0x5c4 = 3 ("Revision error") and what strings the DATAPACK check compares (P2P payload) —
  live test with mismatched DLC/live-update on two PCs.
- Consumer of the peer's `time=` (obj+0x8a0) and of trigger_keep_alive (+0x398).
- Whether the client tolerates a bare-NOERR CMD_RECONNECT — test by killing the gate TCP mid-menu.
- Unit of gtimer (looks like ~ms of game clock) — confirm with a timed goal.
- On-screen code format: "MXNV_0013" = 4 hex-hash letters + `_` + decimal code (%04d) for errors reported with a
  code; "EYRH763" = letters + line for others — inferred from logs, formatter not located.
