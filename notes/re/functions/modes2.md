# modes2: Online Divisions, events (COMPEV), Matchday — what the client sends and what our server must answer

> **Live (2026-11-06):** A1/A5 confirmed. With a bare GET_DIVISION_INFO the game runs the season-end path (a second GET_DIVISION_INFO, FINISH_SEASON, "Season Results" with an empty record, then "Congratulations! You have been promoted to Division 11." — our GET_SERVER_ENV sends `max_division "D12"`), then hangs on an empty screen after GET_DIVISION_INFO `is_season_end:"YES"`. With the A5.2 reply (D10, 10 matches left, one 3-point win) the **Online Divisions** screen shows the Division 10 badge, D9 at 12 points, 3 points, the first match "W 1-0" and the enemy team crest (team id 101 = Arsenal). "To Next Match" sends GET_OFTEN_USED_TEAM_LIST (team select shows "Season Points Info" / "Frequently Selected Teams"), then GET_QUICKMATCHOPTION, and **START_MATCHING {matching_type:"OC_RANK"}**; our Quick Match pairing matched the two copies and both showed **"View Opponent — Connection status with opponent. Proceed? [Find Another] [Proceed]"** (not shown in Quick Match). Not built into the server yet.


Static only (PES2021.exe 1.01.00 + the flow JSONs from dt16_all.cpk). Tags: **[static]** with address,
**[data]** flow JSON, **[log]** our older server logs, **[inferred]**, **[guess]**.
Scope change applied: Online Tournament / PES League is covered by another session and is left out here.
Dumps and helper scripts are in the working folder (not published).

## TL;DR
- **Online Divisions is Quick Match plus 4 extra replies.** You open it from eFootball → Online Divisions.
  The "Kick Off" button runs the *same* Quick flow and the same START_MATCHING / WATCH_MATCHING. Only
  `matching_type` differs: **"OC_RANK"** instead of "OC_QUICK" [static 0x1410fe029 switch on game mode].
  The extra replies are GET_DIVISION_INFO (blob layout below, **must** have `other_match_num > 0`),
  GET_OFTEN_USED_TEAM_LIST (sent only in Divisions), the verdict push before the rating screen, and FINISH_SEASON.
- **Current division points are not a field.** The client adds up `division_point` over
  `match_history_list`, and that list holds at most 10 entries. So a season is 10 matches [static 0x1410881e0, 0x141d29016].
  `other_match_num` = matches left. **0 makes the client run the season-end screens.**
  A bare NOERR gives other_match_num 0, so the season-end path runs every time the mode is opened [static 0x141383115].
- **Events (COMPEV) have no menu entry of their own.** The ONLINE_EVENT mode (0x32) is not in the top-menu table
  [static 0x1401c7fb0]. Its only flow, EventCategoryList, offers just "PES League" [data]. The COMPEV commands are sent only by
  myClub's Events / VS COM events (`process::OnlineEvCompe*`) and by Online Co-op event rooms. Not worth faking
  without myClub. The co-op part belongs to the co-op work.
- **Matchday group-stage matches look feasible.** They need no spectating. Spectating is only for the final
  "pick-up" match, and we can leave that out. Chain: JOIN_MATCHDAY_MODE → GET_MATCHDAY_LIST (1 fake event) →
  GET_MATCHDAY_INFO → JOIN_MATCHDAY → GET_MATCHDAY_MAINMENU_INFO (progress GROUP_MATCH) → START_MATCHING
  `matching_type "MATCHDAY"` [static 0x1410feb83]. The reply nesting is partly inferred. Use overrides.json live to settle it.
- **Server change for all modes:** `pesmatch._try_pair` ignores `matching_type`. It must queue OC_QUICK, OC_RANK and
  MATCHDAY separately. MATCHDAY also needs the same `compe_id`.

---------------------------------------------------------------------------------------------------
## Shared front part (all three modes)
Top menu decide → `SetMode(mode)` → TopMenuModeInit → `Online/Login/LoginFlow` [static lobby.md; data].
The login part is the same as Quick Match [log server-docker-host.log 19:29:17]:
`JOIN_ONLINE_MODE {pid, online_mode, patch_version, dlc_version, livedata_version, extradata_version}` →
SEND_IPANDPORT_NTL → GET_SERVER_ENV → CHECK_CHEAT → GET_URGENT_ANNOUNCEMENT {online_mode, announcement_type PRE_MAIN}
→ GET_MANNER_WARN → GET_PRIVATEINFOTITLELIST. We already answer all of these.

| Menu item (eFootball →) | mode (+0xf0) | group | online_mode | LoginFlow event → first flow |
|---|---|---|---|---|
| Online Divisions | 24 ONLINE_RANK | 13 | "RANKMATCH" | `00_rankmatch` → Online/DivMatch/ProcInit |
| Matchday | 59 MATCHDAY (0x3b) | 29 | "MATCHDAY" | `13_matchday` → Online/MyClub/Matchday/MatchdayProcInit |
| (none) | 50 ONLINE_EVENT | 22 | "" | `13_event` → PesLg/PesLgProcPreEventCategory |

Post-login switch 0x140febef0 on group (jump table 0x140fec3dc): 13→"rankmatch", 29→"matchday", 22→"event"
[static]. online_mode mapping: sweep.md [static 0x1410f2600].

How errors look (messages.md): a command's error reply shows the server-error table text (data/server_errors.tsv)
and then the screen's "return" edge, which usually leads back to the top menu. To gate a mode we haven't built,
reply `JOIN_ONLINE_MODE result:"ERR_MODE_BLOCKED"`. It has its own message [static sweep.md].

---------------------------------------------------------------------------------------------------
## A. Online Divisions (online_mode RANKMATCH, game mode 24)

### A1. Command sequence from the menu entry to "Searching for an opponent"
Flow [data DivMatch/*.json, Quick/*.json] with the process code [static]:

1. Login part above, `online_mode "RANKMATCH"`.
2. `DivMatch/ProcInit` (process::OnlineDivisionMatchInit, update 0x1413802f0). No command. It resets the
   division data object `[g+0xd8]` (0x141088190) → "proceed".
3. `DivMatch/ProcPreMain` (update 0x141382f70) → **CMD_GET_DIVISION_INFO {pid, is_season_end}** (packer 0x141d29900).
   `is_season_end` is "YES" only when the data flag +0x79 is set; it is cleared after sending
   (request filler 0x1410ea350) [static].
   - Reply failed (status ≠ 1) → "return" → DivMatch/ProcEnd → TopMenu. Dialog per table: ERR_DATABASE → "An error
     occurred while accessing the {rf} database." (VBFV_0013); other ERR_* → generic text + `CODE:VBFV_00NN`.
   - Reply OK: the OnResult converter (**slot 9 = 0x1410ea440**; results.md said there was none) copies the
     reply into `[g+0xd8]`:

     | reply field (inside `division_info` blob) | data offset | used for |
     |---|---|---|
     | `division` STR "D1".."D12"/"NONE" → enum index 0..12 | +0x30 | icon, "Division N" text |
     | `demotion_point` | +0x34 | divisionLowerPoint |
     | `promotion_point` | +0x38 | divisionUpperPoint |
     | `doublerank_promotion_point` | +0x3c | |
     | `other_match_num` | **+0x40** | **0 → season end** |
     | `match_record` blob {win_count, draw_count, lose_count, win_s_max, rating} | +0x44.. | records |
     | `match_history_list` [blob {division_point, enemy_team_id, home_score, away_score, enemy_side, enemy_name, is_finished_match, win_lose, enemy_cpu_level}] | vector +0x60/+0x68 (20-byte entries) | **sum of division_point = current points** |

     [static 0x1410ea557..0x1410ea814; offsets Result+0x278..0x2c8 = division_info struct, ctor 0x141d28f70]
   - Then PreMain state 3: `if other_match_num != 0 → "proceed"` (MenuMain), `else → set +0x78, "end_season"`
     → DivMatch/ProcPreSeasonEnd [static 0x141383103..0x141383138].
4. `DivMatch/MenuMain` (menu::OnlineMatchDivisionInfo). It draws division, points and thresholds from the data object
   (0x140da3eb0 → 0x140da43e0: current = 0x1410881e0 sum, upper = +0x38, lower = +0x34) [static].
   No command seen in code [inferred]. GET_SERVER_ENV `max_division` (default "D12") is read here (0x141029610) [static sweep].
   Kick Off (`0_kickoff`) → Online/Quick/QuickProcessInit.
5. `Quick/QuickProcessInit` (0x141385640) → "proceed" for mode 24 → `QuickProcessGetDivisionTeamList`
   (0x14139b740). That step sends **CMD_GET_OFTEN_USED_TEAM_LIST {pid} only when mode == 24**
   (0x140c50380 compares +0xf0 with 0x18; Quick Match skips it, which matches our logs) [static]. Reply
   `{used_team_id_list_num NUM, used_team_id_list [NUM], last_used_team_id NUM}`. Status ≠ 1 → "return".
6. QuickTeamSelect (menu; team list = GET_GAME_TEAM_LIST from login) → QuickProcessGetOption
   - **The online team list is the server's** (2026-11-08): GET_GAME_TEAM_LIST `team_list` [blob{team_id, team_class,
     division_point}] (parser 0x141ca6c00, a growable vector of 0x30-byte entries, no fixed cap seen) is all the
     teams the online team selects offer. Ours sent 8 placeholder teams, so Quick Match showed only those. Now
     `server/pesserver/team_list.json`: 663 teams with a squad from Team.bin (tools/mkteamlist.py). `team_class`
     enum [static 0x140218f00]: TEAM_NONE, TEAM_SAME, TEAM_E, TEAM_D, TEAM_C, TEAM_B, TEAM_A; Konami's classes are
     unknown, ours rank teams by their best 11 players (15/20/30/20/15 %). The team select with 663 teams is not
     seen live yet.
   (0x1413716b0: **GET_QUICKMATCHOPTION**) → QuickMatchSettings → QuickProcessProceedOptionCheck (0x141373610:
   SET_QUICKMATCHOPTION) → QuickProcessMaintenanceInfo (**GET_MAINTENANCE_INFO**) → QuickProcessStartSearching →
   QuickSearching [data]. This is the same as Quick Match [log].
7. TaskQuickMatching (0x14111d640): `[task+0x97]` set (Matchday pick-up only) → CmdStartMatchdayPickupMatching,
   else **CMD_START_MATCHING**. Then it waits for the push **CMD_WATCH_MATCHING** (same reply as Quick Match).
   It has a 300 s search timeout for modes 24/25/31/36 (bitmask 0x1083000000, +0x96, +300000 ms → EYRH763) [static 0x14111a41a].

### A2. How Divisions matching differs from Quick Match
- **Same commands, same reply shapes.** The request filler 0x1410fd610 switches on `mode − 0x18` (tables 0x1410feef0 /
  0x1410feed4) and writes `matching_type` from enum 0x1437f2da0
  `{0 OC_QUICK, 1 OC_RANK, 2 MYCLUB_RANK, 3 PES_LEAGUE, 4 ONLINE_COOP, 5 MYCLUB_COOP, 6 ONLINE_VSCOM_COOP,
  7 MYCLUB_VSCOM_COOP, 8 MATCHDAY}` [static 0x14021d9e0]:
  mode 24 → **"OC_RANK"**, 25 → "OC_QUICK", 31/36 → "MYCLUB_RANK", 40/41/54–57 → "PES_LEAGUE",
  43/44/59/60 → **"MATCHDAY"** [static 0x1410fe056 / 0x1410fe273 / 0x1410feb83].
- The OC_RANK branch also fills option fields +0x340 / +0x380 / +0x3a0 a little differently. It uses
  enum 0x141b99e10[6] instead of the computed rating_level/class strings, and it does not set +0x3a0
  (`use_team_class_type`?) [static 0x1410fe1ca..0x1410fe244]. Server: store them, don't depend on them.
- WATCH_MATCHING member blobs also carry `division` NUM, `rating` NUM, `team_name`, `team_power_lv`
  (parser 0x141d82ac0). For Divisions, send `division` = the enum index (1 = D12 … 12 = D1) and `rating` [inferred].
- **Server:** pair only equal `matching_type`. Mark the room `mode="OC_RANK"` so the verdict code knows to
  update divisions.

### A3. After the match (what the screens read)
Flow [data]: Online/Match/ProcEnd `00_rank_match` → ProcRatingPre → **MenuRating** (group 13 is one of the
rating-screen groups {13,19,28,29}, 0x141379262) → ProcWarningManner → ProcCheckNetworkQuality → ProcRatingEnd
`0_rankmatch` → **ProcDivisionPointPre** (OnlineDivisionMatchPreMatchResult 0x14136d6a0: **GET_DIVISION_INFO again**)
→ **MenuDivisionPoint** → ProcDivisionPointEnd → DivMatch/ProcPreSeasonEnd → (other_match_num > 0) ProcPostSeasonEnd
→ MenuMain.
- **Rating screen:** RatingPre shows it only if the verdict push has already arrived (M+0x18 set). Otherwise it says "skip"
  (results.md). Today we push only in reply to REQ_GAMEADDPOINT, and only the client that lost the P2P link sends that. So in
  Divisions, **push CMD_WATCH_GAMEADDPOINT to both players when both have reported CHANGE_GAMEPHASE END**. That is the
  existing `PESSERVER_VERDICT_AT_END=1` path; make it the default for OC_RANK rooms.
  Body: as `pesmatch._verdict` builds it now: `rating_type "RANKMATCH"`, rating/oldrating, `rating_compe 0`,
  `rating_boost_type "NONE"`, `winner_side`, `gameend_reason "NORMAL_END"`, `is_pk "NO"`, player_list as blobs.
  The verdict holds **no division fields**. Division points reach the screen only through the next GET_DIVISION_INFO.
- Risk check on OLXH171: TaskWatchGame accepts the verdict only if `M+0x10 ∈ {25, 1}`. M+0x10 is set to 25 in
  0x141052530. I found no writer through `[g+0x40]` in all 2534 functions that call the OnlineMode getter. So it most
  likely stays 25 in Divisions too [inferred; the first live division match confirms it].
- **MenuDivisionPoint** shows the reply's data: division, sum(history.division_point) → promotion_point /
  demotion_point. So the server must **append the finished match to `match_history_list`** before the client
  asks again, and decrement `other_match_num`.
- **Season end** (other_match_num = 0 after match 10): ProcPreSeasonEnd (0x141387360) sends GET_DIVISION_INFO (flag set →
  `is_season_end` probably "YES"), then **CMD_FINISH_SEASON {pid}** (reply result only). Then MenuSeasonResults
  (`0_promotion` → MenuDivisionPromotionRelegation, or `1_normal`) → ProcPostSeasonEnd → MenuMain.
  Which reply field picks promotion vs normal was not traced (menu::MenuOnlineSeasonResult vtable 0x14277f658)
  [needs live test]. FINISH_SEASON errors: ERR_DATABASE → generic DBMS_0013.
- **Season end, live 2026-11-07** (scenarios/divisions_season.json, divisions_screens.json,
  divisions_boundaries.json; one copy, GET_DIVISION_INFO patched):
  - **Order:** GET_DIVISION_INFO {is_season_end NO} twice → **CMD_FINISH_SEASON** → GET_DIVISION_INFO
    {is_season_end YES} → Online/DivMatch/MenuSeasonResults. FINISH_SEASON comes *before* the YES request (the
    note above had it the other way round).
  - **The game decides the outcome itself.** It uses the season's points (sum of history division_point) and the
    thresholds of the reply from before FINISH_SEASON. What the server sends after FINISH_SEASON makes no
    difference: the screens were the same with the YES reply saying D10, D9 or D8.
    - Points ≥ promotion_point → MenuDivisionPromotionRelegation: "Congratulations! You have been promoted to
      Division N". With points ≥ doublerank_promotion_point it skips a division (30 points in D10 → "Division 8").
    - Points < demotion_point → MenuDivisionNormalDialog "Unfortunately, you have been relegated to Division N+1."
      (D9: 6 points relegated, 7 stayed. D5 with 0 points: "relegated to Division 6".)
    - Otherwise "You will be in Division 9 again next season." (D9: 7 and 14 points stayed, 15 promoted).
  - These are the same rules as pesdivisions.FINISH_SEASON (≥ double → up 2, ≥ promotion → up 1, < demotion →
    down 1), so the game and the server agree.
  - **Season Results screen:** User Information (name, division icon, Matches = number of history entries, W/D/L
    and win rate from `match_record`), then Season Points (points needed for promotion, points earned).
    `match_record` therefore means this season, not the career (server fixed 2026-11-07).
  - Then the main screen shows the new season's dialog from the next reply: "Division 10 / This is the first of 10
    games this season. / You will need to obtain at least 0 Season Points to stay in the division. / Get 12 or
    more Season Points to earn a promotion."

### A3b. A full Online Divisions match, live 2026-11-07 (two copies, unattended, `pesflow noafk`)
- **Entry:** `pesflow go divisions` → Online/DivMatch/MenuMain (after the season dialog) → A "To Next Match" →
  the same Quick screens (QuickTeamSelect, QuickMatchSettings with Rating / Network Connection Restrictions / Pass
  Support Level / Guest User, Go to Matchmaking) → START_MATCHING `OC_RANK` → paired in the Divisions queue.
- **At END:**
  - SEND_MATCH_ANALYZE from both;
  - the server recorded the result ("DIVISION 1: DRAW 0-0 vs 2 (+1)") and pushed WATCH_GAMEADDPOINT to both;
  - SEND_GAME_RESULT carried `gameend_reason "NORMAL_END"` (Quick Match sends "NotImplement").
- **After the match screens:** Online/Match/MenuRating, "Changes in rating": both players with avatars,
  "1000 > 1000 ( - )" for a draw; footer A Next, X User List, Y Add to Match Block List. Then
  ProcDivisionPointPre → Online/DivMatch/MenuMain, where the season chart shows match 1: 1 point, "D",
  "0 - 0" and the opponent team's crest.

- **Team select (OnlineSeasonTeamSelect, live 2026-11-07):**
  - "Frequently Selected Teams" lists GET_OFTEN_USED_TEAM_LIST `used_team_id_list` (a plain int list) with
    crests. The server fills it from the player's own Divisions teams (SET_GAMETEAM).
  - The "Season Points 0" next to each team (and on the selected team) comes from another field, not found yet.
- **Word filter, live:** a profile comment with a word from blocked_words.txt → CHECK_STRING → ERR_NGWORD →
  "Includes unusable letters or phrases." The game checks favourite team, favourite player and comment when
  leaving Edit Profile.

### A4. Minimal implementation spec (one season that never ends by date, 10 divisions)
The client has no season calendar. The "season" is just `other_match_num` counting down [static]. So:
- **Divisions:** use "D10" (lowest) … "D1" (top). Send GET_SERVER_ENV `max_division:"D10"` so the menus stop at 10
  [inferred: env+0x380 bounds the shown divisions]. New players start in **D10**.
- **Season = 10 matches per player** (history capacity 10 [static]). A season never ends by date. It ends
  when the player has played 10 matches. Then the client itself runs the season-end screens and sends FINISH_SEASON. On
  FINISH_SEASON the server applies promotion/relegation and starts the next 10.
  (Alternative: never send other_match_num 0 and promote right after a match. That skips the client's
  season-results screens. Not recommended.)
- **Points table (proposal, [guess], PES style):** win 3, draw 1, loss 0 (max 30 per season). Per division:

  | division | promotion_point | doublerank_promotion_point | demotion_point |
  |---|---|---|---|
  | D10 | 12 | 21 | 0 (cannot drop) |
  | D9–D2 | 15 | 24 | 7 |
  | D1 | 31 (unreachable: top) | 31 | 9 |

  At FINISH_SEASON: points ≥ double → up 2, ≥ promotion → up 1, < demotion → down 1. Then clear the history.
- **Store (SQLite):** `division_state(pid PK, division INT 1..10, season INT, best_streak INT, wins, draws, losses)`
  and `division_match(pid, season, match_id, division_point, enemy_team_id, home_score, away_score, enemy_side,
  enemy_name, win_lose, t)`. The Elo rating stays in pes_players (rating, win_streak), as now.
- **Replies:**
  - GET_DIVISION_INFO (gate; nested maps are **blobs**):
    ```
    {result:"NOERR",
     pname: blob{name, xuid_one:"", psn_user_id: blob{account_id:0, online_id:"", is_valid_cache:"NO"}, is_deleted:"NO"},
     division_info: blob{ division:"D10", demotion_point:0, promotion_point:12, doublerank_promotion_point:21,
        other_match_num: 10 - len(history),
        match_record: blob{win_count, draw_count, lose_count, win_s_max, rating},
        match_history_list: [ blob{division_point:3, enemy_team_id:101, home_score:2, away_score:1,
             enemy_side:"AWAY", enemy_name:"Bob", is_finished_match:"YES", win_lose:"WIN"[guess], enemy_cpu_level:""} … ] } }
    ```
    `rating` inside match_record is moved with movss (a float) [static 0x1410ea825]. Send a float, or test whether an int is accepted.
  - GET_OFTEN_USED_TEAM_LIST: `{result, used_team_id_list_num:0, used_team_id_list:[], last_used_team_id:0}`
    (or the last few team ids the player used).
  - FINISH_SEASON: NOERR after applying the season.
  - Optional screens: GET_DIVISION_POINT_INFO `{server_num, division_list:[blob{division_type:"ONLINE_DIVISIONS",
    point, rank}]}` (enum {ONLINE_DIVISIONS, MYCLUB_DIVISIONS}, 0x14021a6d0). GET_GAMERESULTS with
    `game_res_type "RANK"`. GET_DIVISION_RANKING(_BYID) and GET_RANKING (HTTP, inline maps; results.md).
- **Verdict / bookkeeping at END:** in OC_RANK rooms compute the result once (as `_verdict` does). Update Elo, then
  append a `division_match` row per player with 3/1/0. Push the verdict to both players.

### A5. Live test (one PC, two copies; stop at the first proving signal)
1. **Menu entry, one copy, no server change.** eFootball → Online Divisions. Expect in the log
   `JOIN_ONLINE_MODE {online_mode:"RANKMATCH"}` … then `GET_DIVISION_INFO {pid, is_season_end:"NO"}`. With the bare NOERR
   expect the season-end path: a second `GET_DIVISION_INFO` and `CMD_FINISH_SEASON`, then a season-results screen.
   That proves the other_match_num rule. Stop.
2. **Main screen, one copy, overrides.json:**
   `{"CMD_GET_DIVISION_INFO":{"division_info":{"__blob__":{"division":"D10","demotion_point":0,"promotion_point":12,
   "doublerank_promotion_point":21,"other_match_num":10,"match_record":{"__blob__":{"win_count":0,"draw_count":0,
   "lose_count":0,"win_s_max":0,"rating":0.0}},"match_history_list":[{"__blob__":{"division_point":3,"enemy_team_id":101,
   "home_score":1,"away_score":0,"enemy_side":"AWAY","enemy_name":"T","is_finished_match":"YES","win_lose":"WIN"}}]}}}}`.
   Expect the Divisions main screen with the Division 10 icon and **3** points (upper 12, lower 0), and no FINISH_SEASON.
   That proves the sum rule.
3. **Search, both copies:** Kick Off → expect `GET_OFTEN_USED_TEAM_LIST`, team select, `GET_QUICKMATCHOPTION`,
   `GET_MAINTENANCE_INFO`, **`START_MATCHING {matching_type:"OC_RANK"}`**. The current server pairs them as in Quick
   Match. Stop when "opponent found" shows. A full match is needed only once the A4 bookkeeping exists. Then check MenuRating
   and the division-points screen, and look for OLXH171.

---------------------------------------------------------------------------------------------------
## B. Events / COMPEV (Online Tournament and PES League left out)
- **No standalone menu route.** ONLINE_EVENT (mode 0x32) appears in no top-menu record (initializer 0x1401c7fb0 sets
  modes 0x18 / 0x36 / 0x3b …, never 0x32) [static]. Its flow `PesLg/PesLgProcPreEventCategory` →
  `Event/EventCategoryList` has only `0_pesleague` [data]. That route goes into PES League, which is out of scope here.
  JOIN_ONLINE_MODE never sends "COMPE" (sweep.md).
- **Who sends COMPEV** [static, class = vtable slot 26]:
  - JOIN_COMPEV_MODE: process::OnlineEvCompeInit (0x1413e8c20) and OnlineEvCompePostMain (0x1413c3be0).
  - GET_COMPEV_LIST: OnlineEvCompeGetCompeList (0x1413ec2d0).
  - GET_COMPEV_MAINMENU_INFO: OnlineEvCompePreMain (0x1413c0db0), the Online Co-op room processes
    OnlineCoopCreatejoinRoom / JoinRoom (0x1413680b0), and menu::OnlineCoopEvCompeAfterMatch.
  - The OnlineEvCompe flows (`Online/MyClub/EvCompe/*`) are entered from myClub: MyClub/MenuMain `26_event_compe` /
    `27_vs_com_compe` / `45_event` → ProcessCheckMatchSquadEvCompe → EvCompe/ProcInitEvCompe [data].
- **Verdict:** too large for a minimal fake. It needs myClub (club, squads, card list), which is a separate project (survey.md).
  The co-op event variant (a co-op room tied to a compe_id, CoopMenuEvCompeAfterMatch) belongs to the co-op work.
  If the co-op session wants it: GET_COMPEV_MAINMENU_INFO {pid, compe_mode, compe_id, target_pid} →
  `{status, compe_result, clear_count, progress_game_count, win_point, cpu_level, vscom_team_id}`.

---------------------------------------------------------------------------------------------------
## C. Matchday (online_mode MATCHDAY, game mode 59)

### C1. Command sequence (menu → Matchday main menu → searching)
Flows `Online/MyClub/Matchday/*` are shared with myClub Matchday. The process classes and their commands come from
their update functions [static]:

| # | flow node (process/menu) | command(s) | failure |
|---|---|---|---|
| 1 | login part, `online_mode:"MATCHDAY"` | as above | |
| 2 | MatchdayProcInit (0x141374080) | **JOIN_MATCHDAY_MODE {pid}** → result only | "return" → TopMenu. ERR_COMPE_JOIN_SANCTION → "Your account is currently banned from participating in competitions." |
| 3 | MatchdayProcMainMenu (0x141385250) | none → `00_compe_list` | |
| 4 | MatchdayCompeList (menu, 0x140dc17e0) | **GET_MATCHDAY_LIST {pid, is_myclub:"NO", start_index:0}** (filler 0x1410cee00) | ERR_COMPE_NOTFOUND → "Competition not found." An empty list probably shows "MatchdayMainMenuNoDataDialog" [inferred] |
| 5 | pick event → BgChangeJoin → MatchdayProcGetCompeDetail (0x141376e00) | **GET_MATCHDAY_INFO {pid, compe_id}** (CmdGetMatchdayInfoEntry) | "return" → list. Events: `proceed` → EULA screen, `skip_eula` → GroupSelect |
| 6 | MatchdayCompeEulaAgree → **MatchdayGroupSelect** (pick side A/B) → **MatchdayCompeTeamSelect** (team from that side's `team_list`) | menus | |
| 7 | MatchdayProcCompeJoin (0x14137fa40) | **JOIN_MATCHDAY {pid, compe_id, entry_matchday_group, team_id, sp_gameplan_data, sp_gameplan_data_size, sp_member_list}**, GET_MATCHDAY_INFO (CmdGetMatchdayInfoCurrent), conditionally GET_MATCHDAY_SP_GAMEPLAN; SET_MYCLUB_MAIN_SQUAD only in myClub (0x141018c80) | ERR_COMPE_BEFORE_ENTRYTIME / AFTER_ENTRYTIME / FULL / NOTFOUND each have a text. Then `join_1vs1` (match_type SINGLE_MATCH) or `join_coop` |
| 8 | MainMenu/MenuMainMatchday (TaskMatchdayMainMenuManager) | **GET_MATCHDAY_MAINMENU_INFO {pid, compe_id}** | ERR_COMPE_NOTFOUND text |
| 9 | `00_match` → MatchdayProcToMatch (0x141390d10) `match` → Quick/QuickProcessInit **`4_matchday`** (mode 0x3b/0x2b, 0x141385992) → QuickProcessStartSearching (no team select, no options) | **START_MATCHING {matching_type:"MATCHDAY"}** [static 0x1410feb83] → push WATCH_MATCHING | No 300 s timeout flag for mode 59 [static 0x14111a41f] |

After the match [data]: Match/ProcEnd `11_matchday` → MatchdayProcRtnFromMatch (0x141395270:
**GET_MATCHDAY_ACHIEVEMENT_REWARD {pid, compe_id}**, **GET_MATCHDAY_WATCH_MATCH_REWARD {pid, compe_id}**) →
`00_rank_match` → ProcRatingPre (group 29 shows **MenuRating** when the verdict push has arrived) → RatingEnd `4_matchday` →
Result/MatchdayPointResultOnline → MenuMainMatchday (MAINMENU_INFO again).
The pick-up/final branch (MenuPickupSelect, START_MATCHDAY_PICKUP_MATCHING / WATCH_MATCHDAY_PICKUP_MATCHING,
START/CANCEL_MATCHDAY_ENTRY_PICKUP_MATCH, WATCH_MATCHDAY_PICKUP_USER, `06_observe` → WatchMatch) is used only when
`matchday_progress` has moved past the group stage. TaskQuickMatching picks pick-up matching only when the Matchday manager
flag `[g+0x128]+0xc58` is set (0x141005bc0) [static].

### C2. Enums the replies use [static initializers]
- `match_type` {0 SINGLE_MATCH, 1 COOP_MATCH} (0x14021b000); `squad_type` {0 MATCHDAY_SQUAD_SPECIAL,
  1 MATCHDAY_SQUAD_MYCLUB} (0x14021af90); `group_type` {0 MATCHDAY_GROUP_TYPE_TEAM, 1 _LEAGUE, 2 _NATIONAL, 3 _OTHER}
  (0x14021ac20); `rating_rank` {0 MATCHDAY_RATING_RANK_NONE, 1 _ACADEMY, 2 _PRO, 3 _ELITE} (0x14021aee0);
  `watch_match_reward_state` {0 NOT_FINISH_PICKUP_MATCH, 1 ABNORMAL_END_PICKUP_MATCH, 2 NOT_RECEIPT_WATCH_MATCH_REWARD,
  3 RECEIPT_WATCH_MATCH_REWARD} (0x14021b070); `matchday_progress` {0 MATCHDAY_PROGRESS_GROUP_MATCH, 1 _END_GROUP_MATCH,
  2 _PICKUP_USERS, 3 _PICKUP_MATCH_MATCHING, 4 _PICKUP_MATCH, 5 _END_PICKUP_MATCH, 6 _RESULT} (0x14021add0).
  The index order follows the table addresses (the lowest address is index 0) [inferred].
- Group ids: the strings "MATCHDAY_GROUP_A" / "MATCHDAY_GROUP_B" exist (0x14296cdb0) → likely `entry_matchday_group` [inferred].

### C3. Minimal fake content: one Matchday, group stage only (feasible, medium effort)
Keys come from the full parsers (my key scan also catches the keys that schemas.json misses). Nesting is inferred
from key order. The nested maps are gate **blobs**.
- **GET_MATCHDAY_LIST** (parser 0x141cb2320):
  `{result, list_num:1, list_max:1, is_no_check:"NO", add_limit_rating:0, compe_list:[blob{
   compe_id:1, base_id:1, compe_name:"Friends Matchday", is_disp_compe_name:"YES",
   group_info_a: blob{team_list:[101,…], team_list_num:N, group_name:"Side A", group_desc:"", is_entry_restricted:"NO", pre_pickup_users_pid_list:[]},
   group_info_b: blob{… "Side B" …},
   match_type:"SINGLE_MATCH", squad_type:"MATCHDAY_SQUAD_SPECIAL"[guess for non-myClub], group_type:"MATCHDAY_GROUP_TYPE_TEAM",
   rating_rank:"MATCHDAY_RATING_RANK_NONE", start_date:now-86400, entry_limit_date:now+30d, end_group_match_date:now+30d,
   pickup_member_date:now+31d, end_date:now+32d (unix seconds, like GET_SVRTIME `date` [inferred]),
   entry_user_num:0, entry_user_max:1000, is_end:"NO", banner_file_info: blob{filename:"", hash_str:""},
   list_banner_file_info: blob{filename:"", hash_str:""}, is_use_emblem:"NO", is_use_emblem_list:"NO", is_entry:"NO",
   watch_match_reward_state:"NOT_FINISH_PICKUP_MATCH", compe_db_id:0, is_possible_watch:"NO"}]}`
- **GET_MATCHDAY_INFO** (parser 0x141cc3cd0): `compe_info` blob with the same compe fields, plus `start_match_limit_date`,
  `entry_pickup_match_start_date`, `watch_match_start_date`, `is_check_eula:"NO"` (→ skip_eula [inferred]),
  `eula_url:""`, `ball_id`, `group_match_time` (match minutes, e.g. 10), `pickup_match_time`, `watch_wait_time`,
  `use_stadium_id`, `fixed_timezone`, `fixed_weather`, `fixed_season`, `is_fix_condition`, `is_equalization`,
  `is_pesleague_stadium`, `is_efootball_pro_stadium`, `joined_matchday_group` (empty before joining), `pickup_select`,
  `team_id`, `pickup_cut_off_rank`, `all_reward_info` blob{empty reward lists, `pickup_reward_type:"MATCHDAY_PICKUP_REWARD_NONE"`},
  and `info_overview` blob. Also `squad_rating_info` / `sp_squad_rating` / `evaluation_a/b` / `superiority_border_ratio`.
- **JOIN_MATCHDAY:** NOERR. Store {pid, compe_id, group, team_id}. After that, set `is_entry:"YES"` and `joined_matchday_group`
  in the replies (that leads to the re-entry path `4_reentry_matchday_compe`).
- **GET_MATCHDAY_MAINMENU_INFO** (0x141ceaaa0): `mainmenu_info` blob{`matchday_progress:"MATCHDAY_PROGRESS_GROUP_MATCH"`,
  `group_match_info` blob{matchday_point, matchday_rank, matchday_ranking_user_num, matchday_ratio,
  total_matchday_point_group_a, total_matchday_point_group_b, pickup_match_score_group_a/b:0, is_hide_graph:"NO"},
  pickup_user_info blob{pickup_users_group_a:[], pickup_user_group_a_num:0, pickup_users_group_b:[], …},
  is_failed_pickup_users:"NO", is_final_reward:"NO", final_reward_list:[], group_result, is_get_final_reward:"NO",
  is_watch_match:"NO", pickup_reward_type:"MATCHDAY_PICKUP_REWARD_NONE", pickup_reward_list:[]}`.
- **After the match:** GET_MATCHDAY_ACHIEVEMENT_REWARD `{add_matchday_point:<points for this match>, achievement_reward_list:[]}`.
  GET_MATCHDAY_WATCH_MATCH_REWARD `{is_exist_watch_match_reward:"NO"}`. Push the verdict at END (as in Divisions).
- **Pairing:** `matching_type "MATCHDAY"`, same compe_id. Pairing opposite sides is preferable. Allow the same side as a fallback
  for two friends [guess]. The team is the one chosen at join.
- **Never advance `matchday_progress`.** The pick-up stage and the watched final would need spectating (spectate.md) and the
  pick-up matching commands. Leave them out.
- **Unknowns (settle live):** which date fields the list screen checks against server time; whether empty banner filenames are
  skipped or start an HTTP fetch; whether `squad_type` SPECIAL makes the client want a server squad (`sp_member_list`).

### C4. Live test (one copy for the menus, two copies only for the search)
1. eFootball → Matchday with no server change. Expect `JOIN_ONLINE_MODE {online_mode:"MATCHDAY"}` … `JOIN_MATCHDAY_MODE {pid}`
   → `GET_MATCHDAY_LIST {pid, is_myclub:"NO", start_index:0}`, then an empty-list / no-data dialog. Stop.
2. overrides.json with the C3 GET_MATCHDAY_LIST body. Expect one event in the list. Choose Join → `GET_MATCHDAY_INFO {compe_id:1}`.
   Stop at the first screen after it (EULA or side select). This proves the list and info shapes.
3. Add the GET_MATCHDAY_INFO / MAINMENU_INFO overrides. Side → team → expect `JOIN_MATCHDAY {compe_id:1, entry_matchday_group, team_id}`,
   then `GET_MATCHDAY_MAINMENU_INFO`, then the Matchday main menu with "Match" enabled. Stop.
4. Two copies: Match → expect **`START_MATCHING {matching_type:"MATCHDAY"}`** from both, then "opponent found". Stop.

---------------------------------------------------------------------------------------------------
### C5. A full Matchday match, live 2026-11-07 (two copies, unattended)
- **Entry, per copy:** `pesflow go matchday` → (Live Update dialog) → MatchdayCompeList → Return → "Enter this
  Competition. Proceed?" → `ok 1` (Yes) → MatchdayGroupSelect ("Choose the Side…" dialog → OK) → Home/Away
  cards ("This Side includes top players such as L. MESSI…" / "…C. RONALDO…") → Return (Right first for Away).
- **Team:** MatchdayCompeTeamSelect: a team dialog → OK, then league → team (Return each), Return opens the
  confirm dialog, Right, Return (Yes; as separate presses).
- **Then:**
  - JOIN_MATCHDAY, GET_ONLINE_AD_FILE, GET_MATCHDAY_SP_GAMEPLAN, GET_MATCHDAY_INFO, GET_MATCHDAY_MAINMENU_INFO
    → Online/MyClub/Matchday/MainMenu/MenuMainMatchday: "Friends Matchday", Home 0 – Away 0 bar, Your Stats
    (Event Points, Result Reward Progress, Side Ranking, Representative condition), "Group Match Stage ends in
    29d23h".
  - "Start Match" → START_MATCHING `MATCHDAY` → paired across sides (A Arsenal vs B Manchester United) → the
    Quick match screens → kick-off.
- **At END:**
  - "MATCHDAY 1: DRAW (+1)", "MATCHDAY 2: DRAW (+1)", and the verdict to both;
  - screens: MatchResult → MenuRating → **MatchdayPointResultOnline** "Match Rewards: 1 Points" (Breakdown:
    Gameplay Review 0.0, Squad Rating ×0.0) → MenuMainMatchday with the side bars Home 1 / Away 1 and Event
    Points 1.
  - The breakdown comes from GET_MATCHDAY_ACHIEVEMENT_REWARD `gameplay_quality` and `squad_rating`, whole numbers
    in **tenths** (live: 1 → "0.1", "× 0.1"; 2.5 is stored as 2). The server sends points × 10 and 10 (× 1.0).

- **Side Ranking (live 2026-11-07):** main menu → Side Ranking (Online/MyClub/Matchday/Ranking/MenuMatchdayRanking)
  sends `CMD_GET_MATCHDAY_RANKING {pid, compe_id, start_index, view_num 100}`.
  - Reply: `rank_list` [blob{**pname: blob{pid, pname: blob{name, …}}**, entry_name, team_id, rank,
    matchday_point, recode_date}], list_num, list_max, update_date. The nesting was checked on the parser.
  - Shown: rank, user name, team crest and name, Event Points, "Last Updated".
  - The main menu's "Your Stats → Side Ranking" reads `group_match_info.matchday_rank` ("Rank: 1"; 0 shows "-").
- Points add up per match across the event (live: 1 → 2 after the second draw).

- **Divisions → Information (Y = `w` key) → Ranking** (live 2026-11-08): "Overall Rankings" (HTTP CMD_GET_RANKING)
  and "Season Points Rankings" → a list of **Division 1–12** (always 12, even with `max_division "D10"`) → one
  division → HTTP `CMD_GET_DIVISION_RANKING {start_index, view_num 100, division "D10"}`.
  - Shown: rank, user, Season Points (`point`) and a User Information panel (avatar, division badge, courtesy
    rating, matches, W/D/L, win rate, favourites, comment). Divisions 11–12 just show an empty list.

## Server changes, in order
1. `pesmatch`: queue by `matching_type` (and compe_id for MATCHDAY). Keep the room's mode.
2. Divisions: GET_DIVISION_INFO (blob layout A4), GET_OFTEN_USED_TEAM_LIST, FINISH_SEASON, `max_division:"D10"`.
   Add division bookkeeping at END. Push the verdict to both players at END in OC_RANK (and MATCHDAY) rooms.
3. Until 2 is done, answer `JOIN_ONLINE_MODE` for RANKMATCH/MATCHDAY with `ERR_MODE_BLOCKED`, so players get a clear message
   instead of the season-end loop from bare replies.
4. Matchday (C3) after Divisions works; group stage only.

## Still unknown
> **Status 2026-11-08:** the season-end screen choice is answered live: the game picks promotion / relegation /
> stay itself from the points and thresholds of the reply before FINISH_SEASON (livetests.md 2026-11-07).
- The field that makes MenuSeasonResults choose `0_promotion` or `1_normal` (A5 test 1 shows the screen; then try a FINISH_SEASON with a changed division).
- Whether `match_record.rating` must be a float.
- Whether OLXH171 appears on a Divisions verdict (expected not).
- `win_lose` / `enemy_side` string values in history entries (enemy_side is matched against enum 0x141b99e30 → 0/1/2).
- Matchday: date gating, banner fetch, SPECIAL squad requirements, and the exact nesting of `compe_info` / `mainmenu_info`.
