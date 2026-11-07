# Results group: post-match reporting, rating push, divisions and rankings

## Corrections from later findings (2026-11-05)
- **Quick Match never shows the rating screen** (mode groups 13/19/28/29 only, static table; sweep.md). The verdict push still ends the 20 s wait.
- Value sets are known (sweep.md): `division` "D1"…"D12"/"NONE", ranking `sort` RATING/WIN_NUM/MATCH_NUM/WIN_STRAIGHT/WIN_RATE, `order` "ASC".


Static RE of PES2021.exe 1.01.00 plus our own traffic logs. Tags: **[static]** read in code at the
address, **[log]** seen in our traffic, **[inferred]**, **[guess]**. Working files (asm dumps, decoders,
decoded log JSON) are in the working folder (dumps not published; general tools in `notes/re/functions/tools/`). New helper scripts there:
`logdec.py` (turns a server-log RX line into JSON, unpacking the nested msgpack blobs),
`ptrace.py` (shows a reply parser's structure: key, nesting slot, type, destination),
`enumtab.py` (enum index to string tables), `desc.py` (finds command descriptors and OnResult handlers).

## How the area works

```
match running ─ game queue (TaskGameQueueProcessor 0x141122cc0): SetGameplayerPlaytime, ChangeGamephase,
                SendMatchTeamInfo, SendDivisionsMatchAnalyze, SetPositionRating, AbnormalEnd, SendAlivePlayer …
CHANGE_GAMEPHASE END (both) ── highlights / full-time screens (~60 s in our tests)
TaskWatchGame (OnlineModeTaskWatchGame.cpp, update 0x141120730) runs during the match and the post-match screens:
   holds two watchers: CmdWatchGameaddpoint (push CMD_WATCH_GAMEADDPOINT) + CmdWatchCheckAlivePlayer
   on disconnect: waits for the server's verdict, sends CMD_REQ_GAMEADDPOINT after 10 s, gives up after 20 s
   then posts "Online/Match/ProcEnd"
process::OnlineMatchEnd (update 0x14137e390, "Online/Match/ProcEnd"):
   CMD_SET_PLAYDATA_MATCH (HTTP; separate sender) → CMD_SEND_STATS_DATA → CMD_SEND_GAME_RESULT → CMD_LEAVE_ROOM
then by mode: OnlineMatchRatingPre (0x141379220, "skip" or "normal" → Online/Match/MenuRating = menu::RatingChange)
              → OnlineMatchRatingEnd (0x14137a400; next flow by gamemode: rankmatch/myclub/pesleague/matchday)
   Online Divisions only (gamemode 24): OnlineDivisionMatchPreMatchResult (0x14136d6a0: CMD_GET_DIVISION_INFO)
              → Online/Match/MenuDivisionPoint → OnlineDivisionMatchPostMatchResult (0x141370ad0)
```
Process-class table (vtable slot 24/26 = update) [static]:

| process | update fn | command / transition |
|---|---|---|
| OnlineMatchEnd | 0x14137e390 | SendStatsData, SendGameResult, LeaveRoom; mode strings rank_match/quick_match/… |
| OnlineMatchRatingPre | 0x141379220 | none: reads the WATCH_GAMEADDPOINT data, "skip"/"normal" |
| OnlineMatchRatingEnd | 0x14137a400 | switch on gamemode-0x18 → "rankmatch"(24, default) / "myclub"(31,36,38,43,44) / "pesleague"(40,55,57) / "matchday"(59) |
| OnlineDivisionMatchPreMain | 0x141382f70 | CmdGetDivisionInfo → "end_season"/"matchday"/"proceed"/"return" |
| OnlineDivisionMatchPreSeasonEnd | 0x141387360 | CmdGetDivisionInfo, CmdFinishSeason |
| OnlineDivisionMatchPreMatchResult | 0x14136d6a0 | CmdGetDivisionInfo (after the match) |
| OnlineDivisionMatchPostMatchResult | 0x141370ad0 | none ("proceed") |
| OnlineDivisionMatchGetGameResults | 0x14139a920 | CmdGetDivisionGameresults (= CMD_GET_GAMERESULTS), CmdGetRejectList |
| OnlineDivisionMatchGetDivisionRecords | 0x14136a5d0 | CmdGetDivisionPointInfo → Online/DivMatch/MenuDivisionRecords |
| OnlineQuickGetDivisionTeamList | 0x14139b740 | CmdGetOftenUsedTeamList |

The gamemode ids come from the play_data JSON `gamemode` and from mode-manager field `+0xf0`:
**24 = Online Divisions ("rank match", OC_RANK), 25 = our Quick Match** [log: play_data gamemode 25 under
OC_QUICK; static: DivPre compares 0x18, OnlineMatchEnd compares 0x19]. Matching types present in the binary:
OC_FREE, OC_QUICK, OC_RANK, OC_RANKING [static strings]. **Divisions, division points and division rankings
belong to the Online Divisions mode (OC_RANK), not to Quick Match.** In our Quick Match traffic, no division
or ranking command is ever sent [log].

---

## Post-match reports (client → server)

### CMD_SEND_GAME_RESULT: the client's own end-of-match report (one per client)
- Transport: gate, svrtype MENU. Packer 0x141c638c0; Option vtable 0x142af45c8.
- When: OnlineMatchEnd state 8 (0x14137e6d2) after SEND_STATS_DATA; then LEAVE_ROOM. Also sent on aborts
  (game_phase DATASYNC). The reply is only polled for completion; `result` is never checked
  (0x14137e750..e79e) [static]. A bare NOERR is enough.
- Request (decoded completed match, logs/server-docker-host.log:1687, pid 1, Arsenal v Arsenal 0–0) [log + packer order]:
  - `pid` reporter's pid · `side` reporter's side HOME/AWAY · `game_phase` phase at the end (END,
    1ST, 1ST_15MIN, 2ND_30MIN, DATASYNC …) · `home_score`/`away_score` · `home_team_id`/`away_team_id`
    (101 Arsenal) · `is_cheat` YES/NO · `error_code` '' or on-screen code (GNCX_0003, VIXT2253, EYRH1530)
    · `abnormalend_reason` NOERR or one of the end-reason enum below (DISCONN_P2P, NO_OPERATION, GAMERELAY,
    DEGRADE_SC seen) · `gameend_reason` is always 'NotImplement' (the client leaves the end reason to the server).
  - Network diagnostics, no gameplay meaning: `communication_time` (ms, 2026917) · `link_type` (6; 255=unset) ·
    `rtt_max/mean/var`, `transport_rtt_max/mean/var`, `rx_packet_loss_rate_*`, `rx_recently_packet_loss_rate_*`,
    `receive_idle_time` (mean > max in the sample, so the units differ between fields) · `match_time` ms of
    match clock (1931585) · `cursor_data_list` [blob per local controller {cursor_id, name,
    cursor_executed_command_count, …_in_mc_active, input_delay_in_mc_active_mean/max,
    input_delay_buffer_fatal_cond_determn_time_max, match_delay_mean/max, match_delay_mean_inactive/max_inactive,
    match_stop_count_self_buffer_empty_in_mc_active}], `cursor_data_list_num` · `match_stop_count_buffer_empty_in_mc_active`,
    `executed_command_count(_in_mc_active)` (97469 = frames), `match_hz_min/mean/variance` (×100 Hz: 5405 ≈ 54 Hz) ·
    `base_private_network_quality`, `base_network_quality`, `private_network_quality`, `network_quality`,
    `is_quality_bad`, `is_quality_check_failed`, `is_degrade_private_network`, `is_degrade_network` ·
    `pes_srv_rtt_nums/mean/variance/min/max` (game-server ping samples: 69 / 33 ms …).
  - Unset values are all-ones: 4294967295 / 65535 / 255, strings 'NotImplement'.
- Errors: none from the reply.
- Server should: store **both** reports per room (keyed by match_id/room + pid). Decide the outcome:
  both say END + NOERR + same score → final; one says DISCONN_P2P etc. → use the other's END report if one
  exists (in every full match we have, one client reports NOERR/END and the other reports DISCONN_P2P/END with
  the same score, because the first to leave cuts the P2P link) [log]; scores differ → mark disputed.
  Our events.py today saves only the first report; it should reconcile both.
- Confidence: high (packer keys and order, plus 20 logged requests).

### CMD_SEND_STATS_DATA: team stats per side
- Gate. Packer 0x141d666b0. Sent in OnlineMatchEnd state 3 (0x14137e508) before GAME_RESULT; the reply is
  only polled for completion [static 0x14137e586].
- Request: `pid`, `match_id` (STR, the GET_MATCHID value, e.g. '1793820726000001'; '' on pre-kick-off aborts),
  `stats` = list of 2 blobs {`side` HOME/AWAY, `inframe_shoot_num` (shots on target), `freekick_num`,
  `cornerkick_num`, `tackle_success_num`, `save_num`, `pass_success_num`, `cut_num` (interceptions),
  `possesion` (%)} [log; every value was 0 in our autoplay 0–0 matches, so units are unconfirmed].
- Server should: store per (match, side), plus averages for a stats page. Reply NOERR.

### CMD_SEND_MATCH_TEAM_INFO: line-ups, sent before kick-off
- Gate, through the game queue (`onlinesystem::gamequeue::SendMatchTeamInfo`, TaskGameQueueProcessor 0x141122cc0).
  Packer 0x141d62d30. Sent ~20 s before kick-off by both clients [log 19:33:03].
- Request: `pid` (sender), `match_team_info` = list of blobs, one per team (the log shows the opponent first):
  {`pid` team owner, `team_spirit` 0, `cpu_level` 5, `is_cost_over` 0, `chemistry` [11 × 99],
  `player_role_list` [3 blobs {`role` [11 ints: 0,1,1,3,2,4,4,7,6,8,12 = position codes per slot]}],
  `is_use_variable_formation` 0, `starting_order` [23 blobs {`gameplayer_id_info` blob {gameplayer_unique_id 0,
  gameplayer_id = player DB id}, `level` 0, `condition` 0..4 (form arrow), `stamina` 100}]} [log 517].
- Server should: store (match, pid) → line-up, used for player-of-match and "most used players". NOERR.

### CMD_SET_GAMEPLAYER_PLAYTIME (game queue, same area)
- {`player_list_num` 11, `player_list` [blob {gameplayer_id_info{unique_id, id}, play_time 90, position 'GK'…}],
  `opponent_player_list_num`, `opponent_player_list` [same]}; sent just before CHANGE_GAMEPHASE END [log 1634].
  Gives minutes played and the final position per player.

### CMD_SET_PLAYDATA_MATCH: full match summary as JSON (HTTP urltype PLAYDATA_MATCH)
- HTTP POST form `msgid, rqid, urltype=PLAYDATA_MATCH, pid, play_data=<JSON>` (serializer 0x141c14ed0).
  Senders 0x1412f95f0, 0x1412ffd40, 0x1404dda60. Sent ~2 s before STATS/RESULT [log 1677/1704].
- `play_data` JSON [log, full sample in log_1677.json]: `gamemode` 25 · `competition_id` 65535 (none) ·
  `fixture`, `leg`, `season_num` 0 · `match_level` 5 · `match_time` 10 (match length setting, minutes) ·
  `match_skip` · `coarch_mode` (coach mode, misspelled by the game) · `main_pad_side` 1/0 (sender's side) ·
  `opponent_pid` [11 slots, the first one is the opponent] · `stadium_id` 52, `weather`, `time_zone`,
  `random_*` · `vs_com` false · `scorer` [goal scorer player ids; [0] for none] · `win_lose` 2 (0–0 →
  2 = draw [inferred]; 0/1 = win/lose [guess]) · `team` [2 × {`team_id`, `team_power` 1464,
  `uniform`, `is_random`, `member` [29 slots, null for empty slots: {`player_id`, `play_time`,
  `rating` (match rating 5.0–7.0, step 0.5), `player_overall`, `strength` [3]}]}].
- **Best single source for player match ratings and goal scorers.** The reply parser only knows `result`.
- Server should: store the JSON as is and extract scorers and per-player ratings. NOERR.

### CMD_SEND_DOWNLOAD_STATS_DATA (not a match result)
- Gate, packer 0x141e49cc0: `pid, filename, file_hash, download_type, download_time, downloaded_size,
  average_bps, max_bps, min_bps, result_kind`. A download-telemetry report from the system layer
  (factory 0x14116dee0). Not in our logs. NOERR is enough; logging it is optional.

---

## Rating verdict: CMD_REQ_GAMEADDPOINT / push CMD_WATCH_GAMEADDPOINT

### TaskWatchGame state machine (0x141120730, task+0x80 = state) [static]
- State 0: create watchers `CmdWatchGameaddpoint` (→ task+0x70) and `CmdWatchCheckAlivePlayer` (→ +0x78).
  State 1.
- State 1, M = match context `[[0x1436fced8]+0x40]` (fields: +9, +0xa, +0xc, +0xd flags, +0x10 dword
  (initialised to 25 at 0x141052530), +0x18 "verdict received", +0x20 verdict struct):
  - watcher +0x98 (CommandObject error flag) set on the gameaddpoint watcher → error **OLXH144** (sub 2);
    on the alive watcher → **OLXH152** (sub 3); → state 2.
  - **M+0x18 set** (push arrived): if `verdict.gameend & ~4` != 0 (anything except NORMAL_END=0 /
    INJURY=4) → error **OLXH186** (sub 5); else if M+0x10 is 25 or 1 → clear the global pending-error flag
    (0x14114e6b0: `[err+0x120]=0`) → **state 3 (done, no ProcEnd forced)**; else error **OLXH171** (sub 4).
  - otherwise, when `M+0xa==0 && M+0xc==1 && M+0x10!=1 && M+0xd!=0`: if M+9==1 → state 2 at once;
    else start timers: at **+10 000 ms send CMD_REQ_GAMEADDPOINT** (fire-and-forget; reply not kept),
    at **+20 000 ms give up** → state 2.
- State 2: post "Online/Match/ProcEnd" (sets M+0xa), free both watchers → state 3.
- What this means (matches the logs): the client whose P2P link died (DISCONN_P2P) waits for the server's
  verdict. We never push, so it sends REQ after 10 s and gives up after 20 s. The other client goes straight on
  (M+9). In logs/server-docker-host.log 20:06:53 → REQ 20:07:04 → STATS 20:07:14, and in server-dev.log
  18:53:04 → REQ 18:53:14 → STATS 18:53:25 [log]. **Without the push the cost is a 20 s stall for one player
  and no rating screen (RatingPre says "skip")**. Nothing breaks.

### CMD_REQ_GAMEADDPOINT
- Gate, request {msgid, rqid, svrtype, client, pid} (5-key map, packer 0x141d58c60) [static + log];
  `pid` = the requester. Reply parser 0x141d59260: msgid/result/rqid only.
- Server should: reply NOERR **and immediately push CMD_WATCH_GAMEADDPOINT to that session** (see below).

### CMD_WATCH_GAMEADDPOINT (push), full layout
Parser 0x141d7f540 (Result size 0x248, parser `this` = Result+0x118); OnResult handler 0x141100010
(descriptor 0x142858788) converts it into the match context and sets M+0x18 [static]. Fields marked ★ are
**missing from schemas.json** (the extractor misses keys compared with 0x140cf3990):
```
{ msgid:"CMD_WATCH_GAMEADDPOINT", rqid, result:"NOERR",
  player_count: NUM                      (Result+0x1d0; count = min(player_count, len(player_list), 22))
  player_list: [ blob {                  (vector Result+0x1d8/+0x1e0, 0x190-byte entries)
      pid NUM,                           → entry+0x28; the entry whose pid == own pid is "me"
      pes_pname: blob{ name STR, xuid_one STR, psn_user_id: blob{account_id NUM, online_id STR,
                       is_valid_cache STR}, is_deleted STR },
      rating_type STR   enum: NONE?(0) RANKMATCH LEAGUE_A LEAGUE_A_COACH LEAGUE_B LEAGUE_B_COACH LEAGUE_C
                        LEAGUE_C_COACH LEAGUE_PREMIERE LEAGUE_PREMIERE_COACH          → entry+0x40
      rating NUM        new rating                                                     → entry+0x30
      oldrating NUM     rating before the match                                        → entry+0x34
      rating_boost_type STR  NONE?(0) / ROOKIE / CONTWIN                               → entry+0x44
      rating_boost_rate NUM                                                            → entry+0x48
      rating_compe NUM                                                                 → entry+0x38
      ★team_id NUM → +0x3c   ★side STR HOME/AWAY → +0x54   ★contWin_num NUM (win streak) → +0x58
      ★skill_rank STR SKILL_RANK_NONE?(0), SKILL_RANK_12 … SKILL_RANK_1                → +0x4c
      ★is_rankup_skill_rank STR YES/NO   ★is_first_skillrank STR YES/NO → +0x50 } ],
  ★winner_side STR  WINNER_NONE?(0) / WINNER_HOME / WINNER_AWAY                         → verdict+0x848
  ★gameend_reason STR  enum (0x1437f3130): NORMAL_END(0) DISCONN OWNGOAL CHEAT INJURY(4) GIVEUP EXIT SLOWDOWN
       NO_OPERATION COMPE_TIMELIMIT CMD_ERR MODE_ERR SYNC_FAILED CONFLICT LOAD_TIMEOUT GAMERELAY BUFFER_OVER
       DEGRADE_SC DISCONN_SC CMD_SENDFAILED OUTPLAY_LONG CMD_LACK TIME_ERR NOTRUN READY_TIMEOUT DISCONN_NTL
       DISCONN_P2P DEGRADE_P2P                                                           → verdict+0x84c
  ★is_pk STR YES/NO (decided on penalties)                                              → verdict+0x850 }
```
Enum index 0 values marked "?" come from the next string in .rdata and are [inferred]. Unknown strings
become index 0, **except gameend_reason**: it starts at 28 (0x141100142), so a missing or unknown
gameend_reason triggers OLXH186.
- What the game shows (RatingPre 0x141379220 → Online/Match/MenuRating) [static]: it runs only when the mode
  index (0x14149f4f0, a runtime-filled table) is in {13,19,28,29}, M+0x18 is set, and not
  (`gameend≠0 && winner_side==0`, 0x141052660). Then for "me" and for the opponent it builds
  {side, oldrating, gain = (rating>rating_compe ? rating−rating_compe : rating), boost_type, boost_rate,
  rating_type} and returns "normal" (screen shown), otherwise "skip". So the rating screen shows
  **oldrating → rating, the boost (rookie / win streak) and the win streak**.
- Server should (recommended, [inferred], confirm with a live test):
  1. Keep a rating per pid (Elo, start 1000, K≈32, draws ±0 on equal ratings) and a win streak.
  2. Build the verdict as soon as the outcome is known: when both clients have sent CHANGE_GAMEPHASE END
     (score from the CMD_ADD_GOAL tally or SEND_GAME_RESULT), or on CMD_REQ_GAMEADDPOINT (answer at once
     with the best result available), and push it to **every room member still connected**, rqid 0, kind 0x60
     like the other pushes. Use `gameend_reason:"NORMAL_END"` when game_phase reached END, otherwise the
     real reason (any value other than NORMAL_END/INJURY makes the client show OLXH186).
  3. Example push: `{"msgid":"CMD_WATCH_GAMEADDPOINT","rqid":0,"result":"NOERR","player_count":2,
     "player_list":[blob{pid:1,pes_pname:blob{name:"A",xuid_one:"",psn_user_id:blob{account_id:0,online_id:"",is_valid_cache:"NO"},is_deleted:"NO"},
     rating_type:"RANKMATCH",rating:1016,oldrating:1000,rating_boost_type:"NONE",rating_boost_rate:0,rating_compe:0,
     team_id:101,side:"HOME",contWin_num:1,skill_rank:"SKILL_RANK_NONE",is_rankup_skill_rank:"NO",is_first_skillrank:"NO"}, …],
     "winner_side":"WINNER_HOME","gameend_reason":"NORMAL_END","is_pk":"NO"}`. Nested objects must be
     msgpack **blobs** (bin/str holding packed maps), like the other gate lists.
  4. Never push to a client still in a match it has not finished, or with a wrong pid: the pid match picks "me".
- Confidence: layout high [static]; push timing and whether a push to the non-waiting client is used: medium.

### CMD_WATCH_CHECK_ALIVE_PLAYER (push)
- Parser 0x141ce2580: only msgid/rqid/result. Handler 0x1410d8a40 (descriptor 0x142847298) posts an event
  carrying pid-ish `[ctx+0x15c]`; it is the server's "are you still there?" probe, answered through the game
  queue's SendAlivePlayer [inferred]. An error result on this watcher → OLXH152. Server: there is no need to
  push it. If we do, send result NOERR.

---

## Divisions and rankings (Online Divisions mode, gamemode 24 / OC_RANK)

### CMD_GET_DIVISION_INFO: my division, points, thresholds, recent matches
- Gate, request {msgid, rqid, svrtype, client, pid, `is_season_end`} (6-key packer 0x141d29900).
- Callers: PreMain 0x141382f70 (entering the mode; decides "end_season"/"proceed"), PreSeasonEnd 0x141387360
  (+CmdFinishSeason), **PreMatchResult 0x14136d6a0 right after each match**, before
  Online/Match/MenuDivisionPoint (menu::OnlineMatchDivisionInfo::CreateObjectPostMatch, fn 0x140da5ce0).
  Waits on 0x1413c7ab0; status 1 → next state ("proceed"), otherwise "return" (back out) [static].
- Reply (parser 0x141d29fc0, ★ = missing from schemas.json):
```
{ result,
  pname: blob{ name, xuid_one, psn_user_id: blob{account_id NUM, online_id, is_valid_cache}, is_deleted },
  division_info: blob{
     division STR, demotion_point NUM (+0x180), promotion_point NUM (+0x184),
     doublerank_promotion_point NUM (+0x188), other_match_num NUM (+0x18c),
     match_record: blob{ ★win_count ★draw_count ★lose_count ★win_s_max (best streak) ★rating },
     match_history_list: [ blob{ division_point NUM, ★enemy_team_id, ★home_score, ★away_score,
          ★enemy_side STR, ★enemy_name STR, ★is_finished_match STR, ★win_lose STR, ★enemy_cpu_level STR } ] } }
```
- **The thresholds come from the server** (promotion/demotion/double promotion points). I found no
  client-side threshold table. The menus have labels divisionCurrentPoint / divisionUpperPoint /
  divisionLowerPoint / maxDivisionPoint, icons division_1..division_12, icon_divisionUp/Down,
  dialogDivisionChange, SE_PROMOTION_AND_RELEGATION [static strings]. The icon picker (0x140ea2c70) takes an
  index 0..12: 0 → division_unknown, 1 → division_12 … 12 → division_1, the same 13-step layout as the
  SKILL_RANK_NONE/12…1 enum.
- `division` string format: **unknown**. The best candidate is the 13-value rank enum (index 1 = lowest
  division) [inferred]; see "Still unknown".
- Server should: keep per pid {division, points, season, W/D/L, best streak, history}. Reply with the
  thresholds of the current division. Suggested rules (PES-style, [guess]): win 3 / draw 1 / loss 0,
  10-match season window, promotion_point and demotion_point per division, double promotion at a higher
  threshold.

### CMD_GET_DIVISION_POINT_INFO: division records screen
- Gate, 5-key request (header + pid) (0x141d2c250). Caller: OnlineDivisionMatchGetDivisionRecords 0x14136a5d0
  → Online/DivMatch/MenuDivisionRecords (OnlineSeasonDivisionPointRecord).
- Reply (0x141d2c8f0): `server_num` NUM (+0xb8), `division_list` [blob{`division_type` STR, `point` NUM,
  `rank` NUM}] (+0xc0). One row per division category (probably ONLINE_DIVISIONS / MYCLUB_DIVISIONS
  [inferred]); rank = position among server_num players.

### CMD_GET_DIVISION_RANKING / _BYID (HTTP DIVISION_RANK / DIVISION_RANK_BYID)
- Request form: `msgid, rqid, urltype, start_index, view_num, division` (serializer 0x141d2dec0); BYID:
  `pid, xuid_one, np_account_id, view_num, division` (0x141d30a90; default id string '0000000000000000').
- Callers: the generic ranking task (0x14106e3f0 → 0x14106e6f0 / 0x14106e8d0; UI event ids 0x107100a..0x1071017),
  menu Online/DivMatch/MenuDivisionRanking, Online/DivMatch/MenuSelectDivisionRanking, ProcReturnDivisionRanking.
- Reply (parser 0x141d2ed10). HTTP replies use **inline** maps, not blobs (rank_list items are read in place) [static]:
```
{ result, date NUM (+0xb8), total_rank_num NUM (+0xd0),
  rank_list: [ { user: { pid NUM, pname:{name, xuid_one, psn_user_id:{account_id, online_id, is_valid_cache},
                         is_deleted}, division STR, favoriteTeam STR, ★favoritePlayer, ★intro, ★manner,
                         ★match_record:{win_count, draw_count, lose_count, win_s_max, rating} },
                 ★rank NUM, ★point NUM } ] }
```
### CMD_GET_RANKING / _BYID (HTTP RANK / RANK_BYID)
- Form: `start_index, view_num, sort, order` (0x141d42150); BYID: `pid, xuid_one, np_account_id, view_num,
  sort, order` (0x141d44d70). The reply has the same shape as DIVISION_RANKING (parser 0x141d42f80).
  Same ranking task (0x14106f470/0x14106f650). The sort/order values come from the UI and were not resolved.
- Server should (all four): SELECT from the ratings table ordered by points (division ranking: filter by
  division) or by rating; page with start_index/view_num; BYID returns the window around the requester.
  Reply raw msgpack over HTTP like the other web API calls.

- **Live 2026-11-07 (pesranking.py):**
  - **Opening:** Online Stats (main-menu item 0x1a) → Overall Ranked Match Rankings sends HTTP
    `CMD_GET_RANKING {urltype RANK, start_index 0, view_num 100, sort RATING, order ASC}`.
  - **Bare reply:** "Unable to find any ranked users. *Rankings are updated regularly. *Users who have not yet
    played 10 games will not be ranked in Win Rate rankings."
  - **With the inline-map reply above:** a "Rating" list (rank, user name, the sort value as `point`; LB/RB =
    5 pages) and a **User Information** panel: avatar, name, Division badge from `user.division` ("D7" → 7;
    "" → "?"), Courtesy Rating from `user.manner`, Matches, W/D/L and Win Rate from `match_record`, then
    Favorite Team, Favorite Player and Comment. "Last Updated" comes from `date`.
  - **`user.manner` here is a plain string** ("B" showed B; an inline map {manner} was ignored → C), unlike
    GET_PLAYERINFO, where it must be a blob.
  - Footer: A User Information, X See Rank, Y Sort.

### CMD_GET_GAMERESULTS (match history list)
- Gate, 7-key request (header + 3 keys, one default 'ALL' in ctor 0x141c76d90, names not resolved).
  CommandObject variants: plain (Online/Information/InformationCmdGetGameResults), RankMatch
  ("CmdGetDivisionGameresults", Online/DivMatch/ProcGetGameResults → MenuPastGameResults), MyClub, Coop, PesLg.
- Reply (0x141c77920): `rlist_num`, `rlist` [blob{ date NUM, ex_type STR, pk_flag STR, no_match STR,
  player [blob{pid, pname{…}, side, is_com, cpu_level, is_follow, is_guest}], team (team ids),
  score [blob{score_1st, score_2nd, score_ex1st, score_ex2nd, score_pk}], match_category STR,
  gameend_reason STR }].
- Server should: build it from the stored match rows (newest first, ~20).

### CMD_GET_HEROSTATS (career totals)
- Gate, 5-key request. Reply (0x141d38f30): total_match_num, total_win_num, total_lose_num, total_goal_num,
  total_lostgoal_num, division_match_num, division_win_num, myclub_division_(sim_)match_num/win_num (NUM),
  is_current_match_win/lose (STR YES/NO). **No code creates it by name** (the "CmdGetHerostats" string is only
  in the registry 0x1434d2e40) [static], so it is probably unused in 1.01. Low priority; a filled reply costs nothing.

### CMD_GET_MATCH_ANALYZE_ENV, CMD_GET_PLAYER_PARAMETERLIST, CMD_GET_GP_ACHIEVEMENT (not post-match rewards)
- MATCH_ANALYZE_ENV: sent once after login [log 19:28:28]. Reply (0x141ccdcd0) = int arrays `posession`,
  `passtype`, `deffence`, `match_up`, `favorite_play`, plus `favorite_play_rate` NUM. These are the thresholds
  of the match-analysis / play-style screens (MatchMenuSubDivisionsAnalyze) [inferred]. Bare NOERR works [log].
- PLAYER_PARAMETERLIST: sent pre-match with the 30-man squad {pid, player_list [blob{gameplayer_unique_id,
  gameplayer_id}], is_equalization NO} [log 461]. Reply (0x141d8a120): `param_playerlist` [blob{gameplayer_id_info
  blob{unique_id, id}, `parameters` [NUM…]}], `is_cost_over` STR. It returns server-side player ability
  values (myClub cards / live form). Bare NOERR works for normal clubs [log].
- GP_ACHIEVEMENT: login and pre-match [log]. Reply (0x141c908b0): `all_gp` NUM, `all_achievement_bit_array`
  [NUM], `pes_point` NUM (wallet and achievements). Not a match reward; post-match GP would be a myClub topic.

---

## Recommended DB schema (SQLite)
```sql
CREATE TABLE match(id INTEGER PRIMARY KEY, match_id TEXT, room INTEGER, mode INTEGER /*25 quick,24 div*/,
  started REAL, ended REAL, home_pid INT, away_pid INT, home_team INT, away_team INT,
  home_score INT, away_score INT, winner TEXT /*HOME|AWAY|NONE*/, is_pk INT, gameend_reason TEXT,
  status TEXT /*final|disputed|aborted*/, stadium INT, play_data_json TEXT);
CREATE TABLE match_report(match INT, pid INT, side TEXT, game_phase TEXT, home_score INT, away_score INT,
  abnormalend_reason TEXT, error_code TEXT, is_cheat TEXT, match_time INT, net_json TEXT, t REAL,
  PRIMARY KEY(match,pid));
CREATE TABLE match_team_stats(match INT, side TEXT, shots_on_target INT, freekicks INT, corners INT,
  tackles INT, saves INT, passes INT, interceptions INT, possession INT, PRIMARY KEY(match,side));
CREATE TABLE match_player(match INT, pid INT, side TEXT, player_id INT, slot INT, position TEXT,
  play_time INT, rating REAL, overall INT, goals INT, condition INT, PRIMARY KEY(match,pid,slot));
CREATE TABLE player_rating(pid INT PRIMARY KEY, rating INT DEFAULT 1000, games INT, wins INT, draws INT,
  losses INT, goals_for INT, goals_against INT, streak INT, best_streak INT, updated REAL);
CREATE TABLE rating_change(match INT, pid INT, oldrating INT, rating INT, boost_type TEXT, boost_rate INT,
  PRIMARY KEY(match,pid));             -- replay source for WATCH_GAMEADDPOINT
CREATE TABLE division_state(pid INT, season INT, division INT /*1 best..10*/, points INT, matches INT,
  wins INT, draws INT, losses INT, best_streak INT, PRIMARY KEY(pid,season));
CREATE TABLE division_rule(division INT PRIMARY KEY, promotion_point INT, demotion_point INT,
  doublerank_promotion_point INT, season_matches INT);
CREATE TABLE division_history(pid INT, season INT, match INT, division_point INT, enemy_pid INT,
  enemy_team INT, home_score INT, away_score INT, enemy_side TEXT, win_lose TEXT, t REAL);
```
The rankings are views: `SELECT … FROM division_state WHERE season=? AND division=? ORDER BY points DESC`
and `player_rating ORDER BY rating DESC`.

## What our server should change (priority order)
1. Reconcile both SEND_GAME_RESULTs. Store STATS_DATA, PLAYDATA_MATCH (scorers and player ratings) and the
   line-ups. Update player_rating once per match.
2. Answer CMD_REQ_GAMEADDPOINT with NOERR **plus an immediate CMD_WATCH_GAMEADDPOINT push** (removes the 20 s
   stall). Then try pushing to both players at END. Watch for OLXH171/186 and for whether MenuRating
   appears in Quick Match.
3. Fix `notes/re/extract_schemas.py`: keys compared with 0x140cf3990 (std::string == const char*) are
   missing from schemas.json for WATCH_GAMEADDPOINT, GET_DIVISION_INFO and the four ranking replies (and likely
   other commands).
4. Online Divisions (only if the mode can be entered; menus group): GET_DIVISION_INFO (+ thresholds),
   DIVISION_POINT_INFO, DIVISION_RANKING(_BYID), RANKING(_BYID), GAMERESULTS, from the tables above.

## Still unknown (and the live test that settles it)
- Push timing: is a WATCH_GAMEADDPOINT used when the client is not waiting (the M+9 path / after
  TaskWatchGame freed its watcher)? Push at END to both and check whether MenuRating appears and the flow
  still reaches ProcEnd (STATS/RESULT still arrive). Also check that a NORMAL_END push to the waiting client
  (state 3, no forced ProcEnd) does not hang it.
- Whether Quick Match (gamemode 25) is one of the rating-screen modes: the mode-index table 0x1434fc9c0 is
  filled at runtime; the push test above answers it.
- Meaning of M+9/+0xc/+0xd and M+0x10 (25 or 1 = OK). They are set by the multiplay layer and were not traced.
- `division` string values (rank enum names? digits?) and the ranking `sort`/`order` values: open the
  Online Divisions menu and log the requests; try DIVISION_INFO with division "SKILL_RANK_10" and with "3".
- `rating_compe` semantics (it is subtracted from rating for the shown gain) and the boost-rate units.
- How GET_DIVISION_INFO's Result reaches the division data object `[[G]+0xd8]` (no OnResult in its
  descriptor; probably copied by the caller) and which fields MenuDivisionPoint draws.
- The 3 GET_GAMERESULTS request keys; units of the STATS_DATA counters (all 0 so far; play a match with goals).
