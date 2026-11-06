# Matchday 2: compe id 0, "Group Match Stage has ended", real compe_info / mainmenu_info layout

> **Confirmed live (2026-11-06):** with the event inside `compe_info.info_overview` the side cards show Home/Away with their own texts, the team browser offers only the leagues of the side's `team_list`, JOIN/INFO/MAINMENU carry compe_id 1 and Start Match works. One more requirement found live: JOIN_MATCHDAY sends the player's squad (`sp_member_list`, the team's player ids) and game plan, and the game reads them back with GET_MATCHDAY_SP_GAMEPLAN before each match; with an empty reply the line-ups are empty and both games crash in GAMELOAD. With the squad returned a full match ran, and both got "Match Rewards: 1 Points" and the side bar Home 1 – Away 1.

Static reading only (PES2021.exe 1.01.00). Dumps: the working folder (scratch, not for publishing).
Tags: [static] = address given, [inferred], [guess].

## Root cause (one line)
`GET_MATCHDAY_INFO.compe_info` is **two levels deep**. All the event fields (compe_id, group_info_a/b, group_type,
the five dates, is_entry …) must sit inside **`compe_info.info_overview`** (a bin blob). We sent them flat in compe_info.
At that level the parser only looks for `info_overview` plus the INFO-only keys, so every flat overview key was
ignored and those fields stayed 0 / "" [static 0x141cc4138: the compe_info loop compares `info_overview` first; if
the key differs it jumps straight to `start_match_limit_date` at 0x141cc5f4a. compe_id…is_possible_watch are read
only inside the info_overview loop 0x141cc41a5–0x141cc5f1e].
`info_overview` is **not** {title, body}. It is the same overview map as one GET_MATCHDAY_LIST `compe_list` entry.

---------------------------------------------------------------------------------------------------
## Q1. Why compe_id becomes 0, and the fix

Flow of the compe id [static]:
1. List pick → the Matchday manager (`[0x140ff0a20()+0x128]`, "mgr") holds the chosen entry at mgr+0x60.
   CmdGetMatchdayInfo**Entry** fills `compe_id` from **mgr+0x60** (filler 0x1410d30f0 @0x1410d3165). Live: 1. ✓
2. The Entry reply handler (0x1410d31d0) **overwrites mgr+0x60** with the parsed reply: copy 0x14105c680(mgr+0x60,
   result+0x1d0). result+0x1d0 is parser-this+0xb8 = `info_overview.compe_id` (parser stores compe_id at +0xb8,
   0x141cc4234). The overview part is copied by 0x14105cca0: dst+0 ← compe_id, +4 ← base_id (0x14105cce0).
   No info_overview → compe_id 0.
3. JOIN_MATCHDAY takes `compe_id` from **mgr+0x60** (filler 0x1410d5550 @0x1410d5638) → 0.
4. After a successful join, MatchdayProcCompeJoin copies mgr+0x60 → **mgr+0x668** (0x14137fd38–0x14137fd4a, copy
   0x140dbe700) and sends CmdGetMatchdayInfo**Current**, whose compe_id is mgr+0x668 (0x1410d30d5). Its reply goes back
   to mgr+0x668 (0x1410d31a7). GET_MATCHDAY_MAINMENU_INFO also uses mgr+0x668 (0x1410da381). So everything after
   the first INFO reply inherits that 0.

Not used for the id: top-level fields, `compe_info.compe_id`, `compe_db_id` (two different fields: overview +0x3d0
and compe_info +0x404), `base_id` (copied to dst+4 only).

**Fix:** send `compe_info = blob{ info_overview: blob{compe_id: 1, …}, … }`. Then JOIN, INFO Current and
MAINMENU all carry 1 [inferred from the chain above].

---------------------------------------------------------------------------------------------------
## Q2. What decides "The Group Match Stage has ended." and the greyed Start Match

### The menu state
- MenuMainMatchday keeps a state at menu+0xb0, set by 0x140db9db0 from `progress = 0x14105b2a0(mgr+0xc58)`
  (= mgr+0xc58 dword, the converted mainmenu_info). Map (jump table 0x140db9ecc): progress 0 → state 0,
  1 → state 1, 2 → state 2, 4/5/6 → state 4/5 [static].
- State → layout (0x140db8dd0, table 0x140db9090): 0 `p_forceMatch`, **1 `p_forceMatchAggregation`** (what we saw),
  2 `p_forceMatchResult`, 3/4 `p_representativeMatch`, 5/6 `p_representativeMatchResult` [static].
- State → Start Match button (0x140db90b0, table 0x140db97d0): state 0 = enabled with handler 0x140db66a0;
  **state 1 = no handler, disabled** (0x140db914b) [static].
- State 1 text: 0x140db76b0 → 0x04680008 "The Group Match Stage has ended. Results are currently being tallied.
  Please wait until entry to the Grand Final opens." plus 0x140db7490 → 0x0469001c "Group Match Stage results in: %ds",
  counted to **mgr+0x7f8** [static 0x140db750c].

### Why progress was 1 although we sent GROUP_MATCH
- `matchday_progress` enum (initializer 0x14021add0): **0 GROUP_MATCH, 1 END_GROUP_MATCH, 2 PICKUP_USERS,
  3 PICKUP_MATCH_MATCHING, 4 PICKUP_MATCH, 5 END_PICKUP_MATCH, 6 RESULT** [static; the table is 0x1437f3698 + 8·i,
  so the "lowest address = index 0" rule in modes2 holds]. "MATCHDAY_PROGRESS_GROUP_MATCH" → 0 (0x14105aea0).
- The MAINMENU converter 0x14105d370: **if progress == 0 and server_now >= mgr+0x7f4, it forces progress = 1**
  (0x14105d392–0x14105d3cf; now = 0x14115fe20) [static].
- mgr+0x7f4 = mgr+0x668 + 0x18c = **`info_overview.end_group_match_date`** (parser +0x298 → src+0x1e0 → dst+0x18c,
  0x14105cead) [static]. It was 0 (no info_overview), so now >= 0 → "ended".
- The popup "The Group Match Stage has ended." (0x0468000c) comes from the Matchday task 0x1410705a0: it reads
  mgr+0x7f4 (0x1410705e3), runs a countdown while now < date, and once now >= date calls 0x141070a70(1), which shows
  0x0468000c (0x141070aee) [static]. Same cause.
- The 2678390 s countdown is mgr+0x7f8 = dst+0x190 = **`compe_info.entry_pickup_match_start_date`** (we sent now+31 d).
  It is not pickup_member_date. If 0 it defaults to end_group_match_date+300 (0x14105c6c7–0x14105c6dc) [static].

Converted compe struct (dst = mgr+0x60 or mgr+0x668) [static 0x14105cca0 / 0x14105c680]:
+0 compe_id, +4 base_id, +0x180 start_date, +0x184 entry_limit_date, +0x188 start_match_limit_date,
+0x18c end_group_match_date, +0x190 entry_pickup_match_start_date (default end_group+300),
+0x194 pickup_member_date (default +0x190 + 90), +0x198 watch_match_start_date (default +0x194 + 600), +0x19c end_date,
+0x174 squad_type index (mgr+0x7dc == 1 means myClub in the Start Match handler 0x140db66c0).

### Values that give "group stage running, Start Match enabled"
- `mainmenu_info.matchday_progress = "MATCHDAY_PROGRESS_GROUP_MATCH"` [static].
- `compe_info.info_overview.end_group_match_date` **> server now** (unix seconds, the same clock as GET_SVRTIME)
  [static compare; clock = inferred]. Keep start_date < now < end_group_match_date < pickup_member_date < end_date.
- `entry_pickup_match_start_date` and `watch_match_start_date` > end_group_match_date (or 0 for the defaults).
- `start_match_limit_date` is copied to mgr+0x7f0 (0x14105c6c1). I found **no reader** in the menu, Start Match or
  matching code. Set it = end_group_match_date [inferred: harmless].
- `is_end:"NO"` (overview; dst+0x1a8 = (is_end == "YES"), 0x14105cf27). `is_final_reward:"NO"`.
- Quick-match gate 0x14112d390 also stops when progress==0 and now >= mgr+0x7f4 (0x14112d462) [static], so the same date
  must stay in the future for the search.

---------------------------------------------------------------------------------------------------
## Q3. Exact layouts

S = str, N = int (the parser accepts positive int or float, `cmp eax,4` / `cmp eax,2`), B = bin blob of a map,
[B] = msgpack **array** of bin blobs, [N] = array of ints.

### GET_MATCHDAY_INFO (parser 0x141cc3cd0; offsets are parser-this, add 0x118 for the result object)
```
{result S, compe_info B{
   info_overview B{                                  # loop 0x141cc41a5..0x141cc5f1e
     compe_id N +0xb8, base_id N +0xbc, compe_name S +0xc0, is_disp_compe_name S +0xe0,
     group_info_a B{team_list [N], team_list_num N, group_name S, group_desc S, is_entry_restricted S,
                    pre_pickup_users_pid_list [N]},
     group_info_b B{same; team_list +0x188, group_name +0x1a0, group_desc +0x1c0, is_entry_restricted +0x1e0, pids +0x200},
     match_type S +0x210, squad_type S +0x230, group_type S +0x250, rating_rank S +0x270,
     start_date N +0x290, entry_limit_date N +0x294, end_group_match_date N +0x298, pickup_member_date N +0x29c,
     end_date N +0x2a0, entry_user_num N +0x2a4, entry_user_max N +0x2a8, is_end S +0x2b0,
     banner_file_info B{filename S, hash_str S}, list_banner_file_info B{filename S, hash_str S},
     is_use_emblem S +0x350, is_use_emblem_list S +0x370, is_entry S +0x390,
     watch_match_reward_state S +0x3b0, compe_db_id N +0x3d0, is_possible_watch S +0x3d8 },
   start_match_limit_date N +0x3f8, entry_pickup_match_start_date N +0x3fc, watch_match_start_date N +0x400,
   compe_db_id N +0x404, is_check_eula S +0x408, eula_url S +0x428, ball_id N +0x448, group_match_time N +0x44c,
   pickup_match_time N +0x450, watch_wait_time N +0x454, use_stadium_id N +0x458,
   fixed_timezone S +0x460, fixed_weather S +0x480, fixed_season S +0x4a0,       # STRINGS, not ints
   is_fix_condition S, is_equalization S, is_pesleague_stadium S, is_efootball_pro_stadium S,
   joined_matchday_group S +0x540, pickup_select S +0x560, team_id N +0x580, pickup_cut_off_rank N +0x584,
   all_reward_info [B{                                # +0x588, ARRAY, read with no type check (0x141cc6a84)
       achievement_reward_list [B{is_repeat S, require_point N, reward_list [B{present…}]}],
       required_point_final_reward N, win_reward_list [B], lose_reward_list [B], watching_reward_list [B],
       pickup_win_reward_list [B], pickup_lose_reward_list [B], pickup_reward_type S }],
   squad_rating_info B{sp_squad_rating N, rating_kind_player_bonus_value_list [N], evaluation_a N, evaluation_b N},
   superiority_border_ratio N +0x5b8 } }
```
Present blob (all reward lists): present_id, name, name_jp, title, body, entity_id, grade, skill B{skill_category,
skill_id}, type, player_level, contract_term, team_id, present_type, item B{item_type, item_variation, item_effect_arg,
count, pes_point_price}, receipt_deadline, count. **`title`/`body` belong to a reward present, not to info_overview.**

- **The crash:** `all_reward_info` is read as an msgpack array: count = value+0x20, pointer = value+0x28, elements
  0x18 bytes (0x141cc6a84–0x141cc6ad1). There is no type check. A bin blob has its byte length and data pointer in
  those same places, so the client walked our raw bytes ("…pickup_reward_type\xbbMAT…") as msgpack objects and
  dereferenced string bytes as a pointer [static; the crash site 0x1421a5e9a is in 0x1421a5be0, which this parser calls
  — inferred]. Either leave it out (safe) or send a real array: `all_reward_info: []`. Never send a bin.
- fixed_timezone/weather/season are strings (enums 0x14021eb50 / 0x14021f750 / 0x14021e2d0). Sending int 0 gives
  string length 0, which is harmless, but a non-zero int would be read as (len, ptr) and crash [inferred from the string
  reader]. Send `"TIME_ZONE_TYPE_NONE"`, `"WEATHER_TYPE_NONE"`, `"SEASON_TYPE_NONE"` [guess: NONE = not fixed;
  is_fix_condition "NO" anyway].

### GET_MATCHDAY_LIST (parser 0x141cb2320)
Flat, as we send it: each `compe_list` element is a bin blob of the **overview map** (the same keys as info_overview).
It works live. list_num +0xc8, list_max +0xcc, is_no_check +0xd0, add_limit_rating +0xf0 is parsed as an int vector
(we send 0, which works live) [static].

### GET_MATCHDAY_MAINMENU_INFO (parser 0x141ceaaa0; offsets are parser-this)
```
{result S, mainmenu_info B{
   matchday_progress S +0xb8,
   group_match_info B{matchday_point N +0xd8, matchday_rank N +0xdc, matchday_ranking_user_num N +0xe0,
       matchday_ratio N +0xe4, total_matchday_point_group_a N(64-bit) +0xe8, total_matchday_point_group_b N +0xf0,
       pickup_match_score_group_a N +0xf8, pickup_match_score_group_b N +0xfc, is_hide_graph S +0x100},
   pickup_user_info B{
       pickup_users_group_a [B{pid N, pname B{name S, xuid_one S, psn_user_id B{account_id N, online_id S,
                                is_valid_cache S}, is_deleted S}}] +0x120,
       pickup_user_group_a_num N +0x130, pickup_users_group_b [B…] +0x138, pickup_user_group_b_num N +0x148,
       is_failed_pickup_users S +0x150 },             # INSIDE pickup_user_info, not at mainmenu level
   is_final_reward S +0x170, final_reward_list [B present] +0x190, group_result S, is_get_final_reward S,
   is_watch_match S, pickup_reward_type S, pickup_reward_list [B present] } }
```
Nesting is from the key-miss jumps: a pickup_user_info miss jumps to is_final_reward (0x141cecb77), and an
is_failed_pickup_users miss stays inside that loop (0x141ceca71) [static]. The reply handler 0x1410da3e0 converts it
to mgr+0xc58 (0x14105d370). If is_final_reward == "YES" it starts the final-reward flow (YES/NO table 0x1437f3868:
0 "NO", 1 "YES") [static]. Our current reply has `is_failed_pickup_users` at the wrong level. That is harmless, because
the key is ignored and defaults to "".

### Why the side cards were blank and the team browser was unrestricted
- group_info_a/b and group_type are overview keys, so they were dropped [static, as above].
- 0x14105bea0 builds each side from group_info: side+0x40 ← group_name, side+0x60 ← group_desc, side+8 ← team keys.
  The team keys are built from team_list according to group_type (0x14105bf65): **TEAM (0)** → key = team_id << 14
  (0x14105c300), LEAGUE (1) → 0x14105c195, NATIONAL (2) → 0x14105bf7f (via 0x1415183f0). group_type enum:
  0 TEAM, 1 LEAGUE, 2 NATIONAL, 3 OTHER (0x14021ac20) [static].
- Empty name/desc → blank card. Empty team list → the full browser [inferred: the empty list is treated as
  "no restriction"]. With info_overview filled, `team_list:[101,108,114,127]` + `group_type:"MATCHDAY_GROUP_TYPE_TEAM"` should
  restrict the browser to those 4 teams [inferred].

---------------------------------------------------------------------------------------------------
## Server change (pesmatchday.get_matchday_info)
```python
ov = _compe(svc, pid)                       # the same overview map as the list entry (is_entry YES after join)
now = int(time.time())
info = {"info_overview": _pack(ov),
        "start_match_limit_date": ov["end_group_match_date"],
        "entry_pickup_match_start_date": ov["pickup_member_date"],    # must be > end_group_match_date
        "watch_match_start_date": ov["end_date"], "compe_db_id": 0,
        "is_check_eula": "NO", "eula_url": "", "ball_id": 0, "group_match_time": 10, "pickup_match_time": 10,
        "watch_wait_time": 0, "use_stadium_id": 0,
        "fixed_timezone": "TIME_ZONE_TYPE_NONE", "fixed_weather": "WEATHER_TYPE_NONE", "fixed_season": "SEASON_TYPE_NONE",
        "is_fix_condition": "NO", "is_equalization": "NO", "is_pesleague_stadium": "NO", "is_efootball_pro_stadium": "NO",
        "joined_matchday_group": e["grp"] if e else "", "pickup_select": "NO",
        "team_id": e["team_id"] if e else 0, "pickup_cut_off_rank": 0}
        # all_reward_info: leave out, or [] (a real list, never _pack(...))
return {"result": NOERR, "compe_info": _pack(info)}
```
MAINMENU: move `is_failed_pickup_users` into the pickup_user_info blob (cosmetic).
Store/compare by COMPE_ID as now. Once the client sends 1, `req["compe_id"]` can be validated.

## Live test (one copy, stop at the first proof)
1. Apply the INFO change and restart the test server. Run eFootball → Matchday → the event → Yes.
   **Proof 1:** "Choose the Side" cards show "Side A"/"Side B". After picking A, the team browser lists only
   101/108/114/127.
2. Pick a team → Yes. **Proof 2:** the log shows `CMD_JOIN_MATCHDAY {compe_id: 1, …}`, then
   `GET_MATCHDAY_INFO {compe_id: 1}` and `GET_MATCHDAY_MAINMENU_INFO {compe_id: 1}`.
3. **Proof 3:** the Matchday main menu has no "has ended" popup, shows "Group Match Stage ends in: 29d23h"-style text
   (0x0469002a, from 0x140db7060), and Start Match is enabled. Stop there (the search needs two copies; see modes2 C4).
If step 1 shows the cards but the browser is still unrestricted, the restriction needs `is_entry_restricted`
or a non-empty pre_pickup list. That is the next thing to check (0x14105bea0 consumers).
