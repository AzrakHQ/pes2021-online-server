# Group "coop": online co-op (2v2 / 3v3, vs-COM) and spectating — static RE of PES2021.exe 1.01.00

## Corrections from later findings (2026-11-06)
- **Live (coop2.md, livetests.md): co-op never connected in 1.01.00** — the peer list is never handed to the P2P layer, and that layer takes one peer. "The session already handles 4–6 peers" below is wrong for P2P. Fixed for one human per side by mod 0.5.0 + server `pescoop.py`; a co-op Casual Match kicked off live. The server must also echo each player's own SYNC_PROGRESS notice, answer GET_GAMERELAY with `game_sycom "P2P"`, and send real `use_team_id` values (262143 on both sides crashes the match load).
- misc.md §1 (static, 2026-11-06): the client accepts groups of 1–3 and any number of groups; with `force_alone:"YES"` two solo copies can play 1 v 1 co-op; exactly one `is_owner:"YES"` (HOME owner); match_data = base64 of 480 bytes (float32[70] + float32[50]); a GET reply with size 0 is accepted. Not tested live.


Two halves. **Co-op** is below. **Spectating / watching** is in
[spectate.md](spectate.md) (written by a sub-agent from the same brief). Its summary is at the end of this file.
Working files are in the working folder (dumps not published; general tools in `notes/re/functions/tools/`):
- `coop_tasks.asm`, `coop_cmdobj.asm`, `coop_room.asm`, `mgr.asm`: full-function dumps, including chained `.pdata` chunks.
- `trees2.txt`: nested key trees of every coop reply parser.
- `cmds.txt`: `kb.py cmd` output.
- Helper scripts: `ct.py` (dumps a function across all its chained chunks; `kb.py fn` stops at the first chunk), `ptree.py` (reply-parser key tree), `en.py` (resolves enum-string tables), `pmap.py`, `vt.py`.

Tooling notes that matter for everyone:
- **Many request packers and parsers are split into several `.pdata` chunks** (UNW_FLAG_CHAININFO). `kb.py cmd` then shows only `['msgid']` for the request. `ct.py keys <packer>` gives the full key list.
- **`schemas.json` is lossy for nested replies.** It is flat and misses many keys, e.g. `matching_state`, `group_list_num`, `owner_pid`, `group_id`, `side`, `guest_kind`. Use `ptree.py <parser>`.
- **The enum-string getters 0x141b99xxx/0x141b9axxx index tables in BSS** that static initializers fill (e.g. 0x14021b120). `en.py <getter>` resolves them. Only the first N entries of each table are meaningful; the tables overrun into neighbouring enums.

---------------------------------------------------------------------------------------------------
## 1. How co-op works (overall flow)

The tasks are all `onlinemode::Task*` state machines.

```
[menu]  GET_COOP_MAINMENU_INFO, GET_COOP_QUICKMATCHOPTION / SET_..., GET_COOP_PROFILE, GET_IS_JOIN_COOP_ROOM,
        GET/SET_COOP_REJECT(_LIST), squad tasks (MyClub / Matchday member lists)
[room, optional "party"]  CREATEJOIN / JOIN / LEAVE_COOP_ROOM, GET_COOP_ROOMLIST, REQWATCHL_COOP_ROOM_MEMBER (list
        push), GET/SET_COOP_ROOM_OPTION (+push WATCH_COOP_ROOM_OPTION), SET_COOP_MATCH_READY, SET_COOP_ROOM_GUEST,
        SET_COOP_ROOM_TO_MATCHING -> push WATCH_COOP_ROOM_TO_MATCHING_MEMBER, REQWATCH_ERROR_COOP_ROOM_TO_MATCHING,
        push WATCH_BREAKUP_COOP_ROOM, RETURN_COOP_ROOM (after the match)
TaskCoopMatchingManager  fn 0x141079280, state at +0xb8 (27 states, jump table 0x141079d24):
  1  TaskCoopGroupMatching    every human: START_COOP_GROUP_MATCHING, then wait for push WATCH_COOP_GROUP_MATCHING
                              FINISH (= "your side / team of 1-3 humans is formed")
  2  wait: success and is-vsCOM (+0xcc) -> 4; success -> 6; failure -> 3 (retry -> state 1, retry_num++ at +0xc4,
     or give up -> 25)
  4/5  TaskCoopVSComOpponent   START_COOP_VSCOM_OPPONENT, then push WATCH_COOP_VSCOM_OPPONENT (COM team data) -> 9
  6/7  TaskCoopMatching        group OWNER only: START_COOP_MATCHING, then all wait for push WATCH_COOP_MATCHING
                               FINISH (= "opponent side found"); failure -> 8 -> back to 1
  9/10 TaskCoopSession         GET_GAMERELAY (via GetGamerelayForMatching) -> TaskMatchSession (the SAME 1v1 session
                               task: GET_MATCHID, sync notices, [START_SEND_GAME_SEQUENCE_DATA if upload], GET_SERVER_ENV,
                               TURN, CreateMultiplaySession) -> SET_GAMERELAY_CHECK_RES -> push WATCH_GAMERELAY_CHECK_RES
                               -> SEND_MATCHING_RESULT
  11/12 TaskCoopStartGame      SET_GAMEMEMBER -> SET_GAMEMEMBERENV{envlist pid/side/sideLeader} -> TaskSyncManager
                               (peer sync over SEND/WATCH_SYNC_NOTICE) -> SET_GAMETEAM{home_team_id,away_team_id} ->
                               GET_MATCHID (must be non-empty) -> GET_COOP_MATCH_DATA
  13..20  squad tasks          TaskCoopMyClubElectedSquadPlayer / MatchdaySPElectedPlayer (GET/SET_*_COOP_MEMBER_LIST),
                               CmdGetMyclubConfirmCoopSquad
  22/23 TaskSyncTeamData, then 24..27 end -> match (CHANGE_GAMEPHASE etc. as in 1v1)
[post-match]  same as 1v1 (SEND_GAME_RESULT, SEND_STATS_DATA, ...) plus SET_COOP_MATCH_DATA and SET_COOP_PLAYER_GRADE
        (both sent through the game result queue `gamequeue`), GET_COOP_LEVEL_RESULT{match_id}, then
        RETURN_COOP_ROOM / LEAVE_COOP_ROOM
```

The evidence, all [static]:
- Manager state machine 0x141079280, with task factories 0x141079d90 / e50 / fa0 / a000 / a060.
- TaskCoopGroupMatching update 0x1411326a0.
- TaskCoopMatching update 0x141132f30, jump table 0x141132ff4.
- TaskCoopSession update 0x141133b40, jump table 0x141133c84, state at +0x74.
- TaskCoopStartGame update 0x141135600, jump table 0x1411359a4.
- Vtables: TaskCoopSession 0x142871810, TaskCoopMatching 0x142871020, TaskCoopStartGame 0x142871ec8, TaskCoopGroupMatching 0x142870a98.

**Terminology.**
- **Group:** one side's humans, 1–3 players. The member vector is capped at 3, see `cmp rdx,3` at 0x14111072f [static].
- **Group matching:** forming your side.
- **Coop matching:** pairing two groups. The VS COM variant pairs a group with a CPU team.
- The client has **no "room" matching_type**. The request filler 0x14110e720 picks `matching_type` from the enum at 0x141b9a150, entries 4–8: ONLINE_COOP, MYCLUB_COOP, ONLINE_VSCOM_COOP, MYCLUB_VSCOM_COOP, MATCHDAY [static].
- So a room party presumably also goes through START_COOP_GROUP_MATCHING, and the **server** must put the room's `to_match_pid_list` into one group [inferred].

**Shared with 1v1.**
- The co-op session literally creates the 1v1 `TaskMatchSession`: 0x141133d5d `"TaskMatchSession"`, task vtable 0x142878280, update 0x1411430b0.
- It feeds that task the members of **both** groups: the loop `cmp r13d,2` over the two group slots at 0x141134043.
- GetGamerelayForMatching is also used by OnlineModeTaskQuickMatching (0x14111b830). SEND_MATCHING_RESULT, SET_GAMEMEMBER and SET_GAMEMEMBERENV use the same commands as quick match.
- The P2P session is N-peer: the spectate half found the recorder hook on `onlinesystem::P2pFullMeshSession`, vtable 0x1428b7698.
- So 2v2 = 4 peers and 3v3 = 6 peers in a **full mesh**, with the same UDP/relay path as 1v1 [static + inferred].

**Push mechanics: same as 1v1.**
- Every `CommandObjectCmdWatch*` / `ReqWatch*` vtable has 28 slots. **Slot 27 is the push handler (OnReceive).**
- 1v1 WATCH_MATCHING slot 27 = 0x141100d00, vtable 0x142858ac0.
- WATCH_COOP_GROUP_MATCHING = 0x14110f5f0 and WATCH_COOP_MATCHING = 0x14110fe00.
- Room pushes: WATCH_COOP_ROOM_OPTION 0x14113edf0, WATCH_COOP_ROOM_TO_MATCHING_MEMBER 0x14113f3e0, REQWATCHL_COOP_ROOM_MEMBER 0x14113f6e0, REQWATCH_ERROR 0x14113f940, WATCH_BREAKUP 0x14113f250.
- WATCH_COOP_VSCOM_OPPONENT = 0x1410d8ef0, WATCH_COOP_MATCHING_CONDITION = 0x141111000.
- So: push unsolicited, kind 0x60, rqid 0, exactly like our WATCH_MATCHING push [static + log analogy].

**matching_state enum (getter 0x141b99fa0, table 0x1437f3098, init 0x14021b120):**
0 CHECKALIVE, 1 SEARCHING, 2 FOUND, 3 FINISH, 4 CANCEL, 5 TIMEOUT, 6 FAILED [static].
- Both coop push handlers map that string to a manager state. Group: `mgr+0x88`. Coop: `mgr+0xc0`.
- Only **FINISH** (3) advances the waiting task: group 0x141132880, coop 0x141133150.
- CANCEL, TIMEOUT and FAILED (4–6) make the task fail, with log lines 206 and 300; the manager then retries or ends.
- SEARCHING and FOUND only update the UI.

---------------------------------------------------------------------------------------------------
## 2. Matching commands (needed for any co-op)

### CMD_START_COOP_GROUP_MATCHING — "put me in the co-op queue / form my side"
- **Transport:** gate, client→server.
- **When:** TaskCoopGroupMatching state 0 (0x141132970). It is sent by **every** human, with no owner check. The retry path re-sends it with `retry_num`+1.
- **Request** (packer 0x141dc1bc0) [static]:
  - `matching_type`: one of ONLINE_COOP, MYCLUB_COOP, ONLINE_VSCOM_COOP, MYCLUB_VSCOM_COOP, MATCHDAY.
  - `is_good_reputation`.
  - `com_level_min`, `com_level_max`: VERYEASY..LEGEND, enum 0x141b99d80.
  - `retry_num`.
  - Plus the usual `pid`, `svrtype`, `client`.
- **Reply:** `{result}` only (parser 0x141dc2540).
  - Not NOERR → result object byte `+0x98`==1 → task error at log line 180 (0x141132c60) → manager state 3 (retry or end) [static].
- **Server should:** reply NOERR and enqueue (pid, matching_type, com levels). When a group is formed, push WATCH_COOP_GROUP_MATCHING FINISH to each member.
- **Confidence:** high for fields and flow; how the real server built groups is unknown.

### CMD_WATCH_COOP_GROUP_MATCHING (push) — "your side is formed"
- **Transport:** gate push. Handler 0x14110f5f0, parser 0x141dc5730.
- **Body** (key tree from `ptree.py`) [static]:
  ```
  matching_state STR   (required: FINISH; see the enum above)
  member_list  LIST of blobs {
      member  BLOB { matching_order NUM, is_owner STR YES/NO, pid NUM, pname BLOB{name,xuid_one,psn_user_id{..},is_deleted},
                     host_addr / reflexive_addr / relayed_addr BLOB{address_family NUM, sa_data STR}, ntl_nat_type NUM,
                     has_guestplayer, rating, manner, team_power_lv, division, guest_info{pname{..},is_platform_guest},
                     side, retry_num, base_team_id, team_name, hometown_info{nation_id,label,name},
                     game_result_wdl{team_id,game_result}, is_pes_league_mode, skill_rank, is_entry_weekly_myclub,
                     weekly_point, join_compe_rating }        # same blob shape as the 1v1 WATCH_MATCHING member
      guest_kind STR (GUEST_NONE / GUEST / PLATFORM_GUEST, enum 0x141b99e70)
      is_follow STR YES/NO, coop_level NUM, coop_manner STR (C/B/A/AA/AAA, enum 0x141b99ed0) }
  member_list_num NUM
  com_level_min STR, com_level_max STR   (VERYEASY..LEGEND)
  ```
- **What the handler does** [static, offsets from the Result base]:
  - `matching_state` (+0x1e8) sets `mgr+0x88` (1 SEARCHING, 2 FOUND, 3 FINISH, 4/5/6 errors).
  - Only on **SEARCHING or FINISH** does it rebuild the member vector `mgr+0x58` (0x68-byte entries; pid at +0x28):
    - `pid` and `pname` go into the entry.
    - `guest_kind` → word: GUEST_NONE=0, GUEST=1, else 0x101.
    - `is_follow`=="YES" → byte; `coop_level` → int; `coop_manner` → 0..5.
    - **the member whose `is_owner`=="YES" becomes the group owner `mgr+0xf8`** (0x14110f9e1).
    - `com_level_min/max` → global +0xd0/+0xd4.
- **Matters:**
  - **FINISH is required.**
  - **Exactly one `is_owner:"YES"` per group.** That owner sends START_COOP_MATCHING (see below).
  - The pids must include the receiver.
  - Address blobs: send them as in 1v1 (they feed the later P2P set-up).
- **Server should:** push `{result:NOERR, matching_state:"FINISH", member_list:[blob{member:blob{...1v1 member...}, guest_kind:"GUEST_NONE", is_follow:"NO", coop_level:1, coop_manner:"A"}...], member_list_num:n, com_level_min:"NORMAL", com_level_max:"NORMAL"}` to every group member.
- **Confidence:** high.

### CMD_CANCEL_COOP_GROUP_MATCHING
- **Request:** `{pid}` (packer 0x141dcd490), sent from 0x141132730 when the user backs out.
- **Reply:** `{result}`.
- **Server:** dequeue; NOERR.

### CMD_START_COOP_MATCHING — "find an opposing side" (group owner only)
- **When:** TaskCoopMatching state 2 (0x1411336d0). It is sent **only if my pid == `mgr+0xf8`** (the group owner) [static 0x141133717]. Non-owners skip straight to waiting for the push.
- **Request** (packer 0x141dc2ef0, filler 0x14110ee40) [static]:
  - `matching_type`.
  - `coop_player_info_list[ {pid, is_good_reputation, guest_kind, user_id{pid, pname{...}}} ]`.
  - `owner_pid`.
  - `use_team_id`: team chosen for the side.
  - `retry_num`.
- **Reply:** `{result}`. An error → log line 274 → manager state 8 → back to group matching.
- **Server should:** NOERR. Queue the group (group_id, members, use_team_id). When two groups exist (same matching_type, sizes compatible), push WATCH_COOP_MATCHING to **all** members of both groups.

### CMD_WATCH_COOP_MATCHING (push) — "opponent side found"
- **Transport:** gate push. Handler 0x14110fe00, parser 0x141dc9a70.
- **Body** [static: key list from `ct.py keys`; nesting from the handler's 0x20-byte group stride]:
  ```
  matching_state STR            (FINISH; CHECKALIVE triggers SEND_COOP_ALIVE, see below)
  group_list LIST of blobs {    (exactly 2 groups)
      member_list LIST of blobs { member BLOB{...same member blob as above...}, guest_kind, is_follow, coop_level, coop_manner }
      member_list_num NUM
      use_team_id NUM           (Result group +0x14; != 0x3ffff → that team id is loaded for the side, 0x14100cca0)
      owner_pid NUM             (group +0x18)
      group_id NUM              (group +0x1c) }
  group_list_num NUM
  ```
- **What the handler does** [static]:
  1. `matching_state` → `mgr+0xc0`: CHECKALIVE→1, SEARCHING→1, FOUND→2, FINISH→3, CANCEL/TIMEOUT/FAILED→4/5/6.
  2. It finds **the group containing my pid** and reads that member's **`side`** (enum 0x141b99e30: HOME=0, AWAY=1). My group is stored as "mine" (slot `mgr+0x58`), the other as "opponent" (`mgr+0x90`), with side index, owner_pid and group_id. `use_team_id` is loaded as that side's team.
  3. For every member it builds 0x68-byte peer records (pid, guest_kind, is_follow, coop_level, coop_manner); at most 3 per group.
  4. `is_owner`=="YES" → `mgr+0xf8` = that pid (0x1411107b3). The last YES seen wins.
- **Matters:**
  - Every member blob needs **`side`**: all of one group "HOME", the other "AWAY". By analogy with 1v1 MMGC_0002, a missing side very likely breaks team-data sync.
  - **FINISH** is required.
  - Give **one** `is_owner:"YES"` in the whole push: the HOME group owner, which plays the 1v1 "owner/host" role in TaskMatchSession [inferred].
  - Real `host/reflexive/relayed_addr` for every peer: full-mesh P2P needs every pair to connect.
- **Server should:** push to all 4–6 members:
  `{result:NOERR, matching_state:"FINISH", group_list:[blob{member_list:[...], member_list_num, use_team_id, owner_pid, group_id}, blob{...}], group_list_num:2}`.
  Then create one "room" record (as `pes_mm.rooms`) holding **all** member pids, so that GET_MATCHID, the gamerelay check and notice relaying cover everyone.
- **Confidence:** high for fields and state; medium for the exact is_owner rule.

### CMD_SEND_COOP_ALIVE — keep-alive answer during coop matching
- **When:** the WATCH_COOP_MATCHING handler, when `matching_state`=="CHECKALIVE", creates CmdSendCoopAlive (0x141110955), once (pointer at +0x120).
- **Request:** `{pid, group_id}` (packer 0x141dc0b70).
- **Reply:** `{result}`.
- **Server:** optional. Our server can skip CHECKALIVE probes and answer NOERR if one arrives. Use it only to detect dead groups.

### CMD_CANCEL_COOP_MATCHING
- **Request:** `{pid}` (0x141dce2d0), from 0x141133010.
- **Server:** dequeue the group; NOERR. Ideally also push WATCH_COOP_MATCHING CANCEL to the other group members [inferred].

### CMD_WATCH_COOP_MATCHING_CONDITION (push) — matching-condition widening (UI)
- **Body:** `manner_min/max` STR, `coop_play_level_min/max` NUM, `team_power_level_min/max` STR (parser 0x141dcf900).
- Used by the netMatchingCoop menu (0x140d88b30).
- **Server:** optional; never needed.

### CMD_START_COOP_VSCOM_OPPONENT / push CMD_WATCH_COOP_VSCOM_OPPONENT — co-op vs CPU
- **Request** (0x141dd24a0): `is_myclub_mode`, `com_level_min/max`, `user_data[{pid, guest_kind}]`, `owner_pid`, `my_team_id`, `vs_team_id`.
- **Sent by:** TaskCoopVSComOpponent (0x14113a790). It is reached when the group push completes and the session is vs-COM (`mgr+0xcc`).
- **Push** (parser 0x141ce3780):
  ```
  my_team  { team_name, team_name_short, base_team_id,
             team_data{squad_id, member_list[{gameplayer_unique_id, gameplayer_id, uniform_number, player_level, parameters, default_rarity}],
                       member_num, is_entry_compe, coach_id_list, coach_unique_id_list, gameplan_data(_size),
                       is_club_edition, club_edition_type},
             team_name_commentary_id }
  com_team { side, com_level, the same team fields, pes_pname{...}, pid, team_name_commentary_id }
  owner_pid
  ```
  This is a full squad description, the same format as GET_MYCLUB_CONFIRM_COOP_SQUAD.
- **Server:** substantial work. It must synthesize squads (or find out that for `is_myclub_mode:NO` a `base_team_id` alone suffices — unknown).
- **Confidence:** fields high, minimum content unknown.

---------------------------------------------------------------------------------------------------
## 3. Session and start-game commands (mostly already handled for 1v1)

Our 1v1 handlers already relay pid lists and gate the relay check on "all room members". They work for N peers as long as the room holds all 4–6 pids.

| Command | Co-op use | Server should |
|---|---|---|
| GET_GAMERELAY (HTTP, via CommandObject GetGamerelayForMatching 0x141134310; errors understood: ERR_NOTFOUND / ERR_GAMERELAY_NOTENOUGH / ERR_DATABASE / ERR_RETRY_LIMIT) | TaskCoopSession state 1 | same reply as 1v1 |
| GET_MATCHID | inside TaskMatchSession **and** TaskCoopStartGame state 9 (0x14113580e). StartGame **errors if match_id is empty** (0x1411359e0, log line 426) | same non-empty id for all members of the coop room (`upload`: see spectating) |
| SEND/WATCH_SYNC_NOTICE, SEND/WATCH_SYSTEM_NOTICE | TaskMatchSession and TaskSyncManager, now with up to 5 pids in `pid_list` | existing relay |
| SET_GAMERELAY_CHECK_RES → push WATCH_GAMERELAY_CHECK_RES | TaskCoopSession 8/9 | push only once **all** coop members have reported (existing logic, room = all pids) |
| SEND_MATCHING_RESULT {member, vs_member, game_sycom, …} | TaskCoopSession 10 | NOERR |
| SET_GAMEMEMBER, SET_GAMEMEMBERENV {list_num, envlist[pid, side, sideLeader]} | TaskCoopStartGame 1, 3 | NOERR (could log side/sideLeader for checking) |
| SET_GAMETEAM {home_team_id, away_team_id} (packer 0x141d707f0) | TaskCoopStartGame 7 | NOERR; store them for the result |
| CMD_GET_COOP_MATCH_DATA {pid} → {match_data_size NUM, match_data STR} (parser 0x141dd3f00) | TaskCoopStartGame 11 | return the blob last stored by SET_COOP_MATCH_DATA for that pid, else size 0 / "" [guess: empty accepted] |

**Errors in TaskCoopSession** (logger 0x141154560, not the on-screen reporter) [static]:
- Line 600: the match session failed (0x141135150).
- Line 685: gamerelay check (0x141135000).
- If the session fails with task state 4, the manager sets `+0xbc`=1 and ends. Otherwise it retries matching from group matching (0x14107964c).

---------------------------------------------------------------------------------------------------
## 4. Post-match co-op commands

### CMD_SET_COOP_MATCH_DATA — per-player co-op performance blob
- **Request** (0x141dd4980): `match_stats`, `grade`, `match_data_size`, `match_data`.
- **Sent via:** the game result queue: `QueueProcessCommandFunctor<gamequeue::SendCoopMatchData, CommandObjectCmdSetCoopMatchData>` (RTTI 0x143653bf0).
- **Reply:** `{result}`.
- **Server:** store `match_data` per pid (it comes back in GET_COOP_MATCH_DATA and GET_COOP_PROFILE `coop_profile_data.match_data`); NOERR.

### CMD_SET_COOP_PLAYER_GRADE — players rate each other
- **Request** (0x141dd5bf0): `grade_list[{pid, grade}]`, `oppo_grade_list[{pid, grade}]`.
- **Sent via:** the queue (RTTI 0x143653cd0).
- **Server:** NOERR (could feed `coop_manner` / `coop_level`).

### CMD_GET_COOP_LEVEL_RESULT — level/point change screen
- **Request:** `{pid, match_id}` (0x141ba8800).
- **Reply** (0x141ba8ec0): `coop_level_user_list[ blob{status STR, user_id{pid, pname{...}}, is_clan_owner, is_follow, evalution NUM, reference NUM, point, before_point, after_point, before_level, after_level} ]`.
- **Server:** one entry per member, with unchanged points/levels and `status` (values unknown) [guess].

### CMD_GET_GAMERESULTS_COOP — co-op match history (menu)
- **Used in:** 0x140d78000.
- **Server:** an empty list is the safe default (schema not extracted: no Result class found under that name).

---------------------------------------------------------------------------------------------------
## 5. Co-op menu commands (needed so the co-op menu opens)

| Command | Request | Reply (tree) | Safe server reply |
|---|---|---|---|
| GET_COOP_MAINMENU_INFO | pid, online_mode, is_check_season_end | is_coop_banned, is_season_end, division STR, pre_season_rank, pre_season_rank_all_clan_num, division_change_type, is_exist_owner_clan, is_exist_new_request, is_exist_new_pending | `is_coop_banned:"NO", is_season_end:"NO"`, other flags "NO", ranks 0 [inferred: the factory compares "YES"] |
| GET_COOP_QUICKMATCHOPTION / SET_… | set: is_vscom, cpu_level_min, cpu_level_max | is_vscom, force_alone, cpu_level_min/max (VERYEASY..LEGEND) | `is_vscom:"NO", force_alone:"NO", cpu_level_min:"NORMAL", cpu_level_max:"NORMAL"`; store SET |
| GET_COOP_PROFILE | pid | user_info{pid, pname} and, beside it at the top level, coop_profile_data{is_follow, coop_level, coop_level_point, follower, coop_manner, online_match, vscom_match, match_data_size, match_data} [live, parsebench 2026-11-07] | level 1, points 0, manner "A", counts 0, empty match_data |
| GET_IS_JOIN_COOP_ROOM | pid | is_join_coop_room | `"NO"` (asked on the main menu; "YES" presumably offers a rejoin) |
| GET_COOP_REJECT_LIST / SET_COOP_REJECT{set_reject_pid_list, unset_reject_pid_list} | – | reject_list[{pid, pname}], reject_list_num | empty list; store the block list and honour it in grouping |
| GET/SET_MYCLUB_*COOP_MEMBER_LIST, GET_MATCHDAY_SP_*COOP*, GET_MYCLUB_CONFIRM_COOP_SQUAD | coop_player_list | squad trees (see `trees2.txt`) | MyClub / Matchday co-op only; out of scope for a first version |
| GET_IS_KONAMIID_COOPERATION (HTTP, KONAMIID_API) | – | is_cooperation, is_reagree, reagree_url | not co-op ("cooperation" = account linking); belongs to the account group |

---------------------------------------------------------------------------------------------------
## 6. Co-op room ("party lobby") commands

All of these are gate commands. Request packers come from `ct.py keys`, replies from `ptree.py` [static]. Unless stated, nobody has traced how the menu reacts to each field (the room UI tasks TaskCoopRoomRoot 0x14113ff10, TaskCoopRoomMain 0x1413680b0 and the `process::OnlineCoop*` processes were not read in depth).

- **CREATEJOIN_COOP_ROOM**
  - Request: `basic_settings{room_id, room_name, clan_name, clan_owner_info{pid, pname}, is_follow, is_com_match, is_use_chat, is_same_level, team_level, coop_level, is_same_manner, coop_manner, room_entry_restriction, password, lang}`, `match_settings{user_team_id, enemy_team_id, cpu_level}`, `kind` (factory default "NORMAL"; also "COOP").
  - Reply: `room_id` NUM.
  - Server: allocate a room, owner = sender.
- **JOIN_COOP_ROOM** {room_id, password, is_invite} → `{result}`. **LEAVE_COOP_ROOM** {room_id} and **RETURN_COOP_ROOM** {room_id} (back to the room after a match) → `{result}`.
- **GET_COOP_ROOMLIST**
  - Request: `search_options{room_id, use_password, vs_setting, team_level, coop_level, coop_manner, cpu_level_min/max, can_entry}`, `kind`, `compe_id`, `start_index`.
  - Reply: `room_list[ blob{basic_settings{...}, match_settings{...}, user_num, kind, compe_id, is_game} ]`, `room_list_num`, `all_room_num`.
- **GET_COOP_ROOM_OPTION** {room_id}, and push **WATCH_COOP_ROOM_OPTION** (registered by TaskCoopRoomWatch 0x141132330, along with WATCH_BREAKUP and WATCH_ROOM_TO_MATCHING_MEMBER).
  - Body: `basic_settings{...}`, `match_settings{...}`, `is_game`, `room_phase`, `is_start_count_down`, `countdown_end_time`, `is_stop_countdown`, `is_start_matching`.
  - The room_phase values were not resolved.
- **SET_COOP_ROOM_OPTION** {room_id, basic_settings, match_settings}. Server: store it and push WATCH_COOP_ROOM_OPTION to all members.
- **GET_COOP_ROOM_MEMBER** {room_id} and **REQWATCHL_COOP_ROOM_MEMBER** {room_id}
  - Reply: `list_type`, `list_num`, `list_key_name`, `list_data[ blob{player_index NUM, user_info{pid, pname}, is_owner, coop_level, is_clan_member, is_follow, is_guest, guest_info{pname, is_platform_guest}, is_ready} ]`.
  - `REQWATCHL_*` uses the generic list protocol (0x1411c0bc0 / 0x1411c1340) [static]: `list_type` ∈ **RELOAD / ADD / DEL / UPDATE**, and `list_key_name` names the key field inside each element. Here that is **"player_index"**: the REQWATCHL_COOP_ROOM_MEMBER factory 0x141e28470 references that string.
  - So: answer the request with RELOAD (full list). Afterwards push the same msgid with ADD (join), DEL (leave) or UPDATE (ready / option change) carrying only the changed elements [static for the type names; inferred for the semantics].
- **SET_COOP_MATCH_READY** {room_id, is_ready YES/NO}. Server: UPDATE push of that member's is_ready.
- **SET_COOP_ROOM_GUEST** {room_id, guest_info_list[{pname{...}, is_platform_guest}]}: local guest players.
- **SET_COOP_ROOM_TO_MATCHING** {room_id, to_match_pid_list}: the owner starts matching.
  - Server: push **WATCH_COOP_ROOM_TO_MATCHING_MEMBER** `{player_index_list: [NUM...]}` to the room. Its handler 0x14113f3e0 collects the indices into a vector. The members then start TaskCoopMatchingManager: their START_COOP_GROUP_MATCHING must be grouped together.
- **REQWATCH_ERROR_COOP_ROOM_TO_MATCHING** {room_id} → `{is_error YES/NO, error_kind}`.
  - `error_kind` enum 0x141b99d50: NOERR, NOT_ENOUGH_PLAYER, INCLUDES_RENTAL, RED_CARD, CONTRACT. The handler maps it to `+0x504` = 0..4 (0x14113fadb).
  - Safe: `{is_error:"NO", error_kind:"NOERR"}`.
- **WATCH_BREAKUP_COOP_ROOM** (push, no body fields): the room was dissolved (owner left). Server: push it to the remaining members when the owner leaves.

---------------------------------------------------------------------------------------------------
## 7. Feasibility and implementation outline: CO-OP

**Verdict: feasible.** The hard part, an N-peer P2P session with TURN/relay, sync notices and team-data sync, is the client's own 1v1 machinery, and that already works with our server. The server-side additions are matchmaking state and four push types. Main risks:
- Whether 4–6 peers can all connect to each other on our relay / NAT set-up. Full mesh means 6–15 links.
- Testing needs ≥3–4 game clients.

**Phase A: "quick co-op", solo players, no rooms, no vs-COM.** About 1.5–2 days of server work.
1. `pescoop.py`:
   - Queue for START_COOP_GROUP_MATCHING.
   - Grouping rule, configurable: e.g. env `PESSERVER_COOP_GROUP=1` puts each player in their own group, which allows a 2-client "1v1 co-op" smoke test. Default: groups of up to `ceil(n/2)`, max 3.
   - Push WATCH_COOP_GROUP_MATCHING FINISH: one `is_owner:YES` per group, addresses as in 1v1, `side` already assigned.
2. START_COOP_MATCHING from the group owner:
   - Pair two groups.
   - Push WATCH_COOP_MATCHING FINISH to all members, with `group_list` ×2, `side` HOME/AWAY per group, `use_team_id`, `owner_pid`, `group_id`, and a single global `is_owner:YES` (HOME owner).
   - Register `pes_mm.rooms[id] = {owner, members:[all pids]}`.
3. CANCEL_* dequeues. SEND_COOP_ALIVE → NOERR.
4. Defaults for the menu commands (§5), GET_COOP_MATCH_DATA (stored blob or empty), the post-match commands (§4), and GET_COOP_LEVEL_RESULT (unchanged values).
5. Existing GET_MATCHID, gamerelay check and notice relay: verify they key on the room containing **all** members (they do via `_room_of`).

**Phase B: party rooms (§6).** About 2–3 days. Room registry, REQWATCHL list pushes (RELOAD/ADD/DEL/UPDATE), option, ready, room-to-matching, breakup, return. The group-matching step then groups `to_match_pid_list`.

**Phase C: vs-COM co-op and MyClub/Matchday co-op.** Unknown effort (≥3 days). It needs squad and team_data synthesis for WATCH_COOP_VSCOM_OPPONENT and the *_COOP_SQUAD / MEMBER_LIST commands.

**Test plan** (needs the user's permission and multiple machines or accounts; nothing was launched here):
1. Two clients, both in Online Co-op quick match, with `COOP_GROUP=1`: does a 1-vs-1 co-op match load?
2. Three clients (2v1).
3. Four clients (2v2).
4. Watch netshim for mesh links.

---------------------------------------------------------------------------------------------------
## 8. Spectating / watching: summary (details in `[spectate.md](spectate.md)`)

- **The players upload and the server stores and forwards. The viewer pulls over plain HTTP and never talks to the players.**
- **Recording is turned on by the server:** GET_MATCHID `upload:"YES"` (check at 0x140ff7cd0). Only then does TaskMatchSession send **CMD_START_SEND_GAME_SEQUENCE_DATA** {pid}. An error reply aborts the match (MXNV264). We send "NO" today, so nothing is recorded.
- **CMD_SEND_GAME_SEQUENCE_DATA** {match_id, game_seq_data_num, game_seq_data[blob{type, fromId, channelId, sequenceNumber, data, data_len}]}:
  - The `data` is the session-layer frames of the P2pFullMeshSession / CsClientSession (hook 0x1412561c0).
  - Batches are sent at least every 5 s or 16 KB by a worker thread.
  - The first record is a header: revision triple, build string and match id.
- **Viewer stream:**
  - GET `<WATCHING_GAME url>/<id>/<N>.enc`, N=1,2,3… (0x1411dff40). WATCHING_GAME is URL enum index 0x2e, which we already serve.
  - Each chunk = 16-byte IV + **AES-256-CBC**. The key is hard-coded at 0x142897bd0: `35a110675829e36845dc775162cca1bb69b786ed1e73d82ea9fe7e6176743881`.
  - Plaintext = `[flag][body]`; flag 1 = zlib.
  - The last chunk ends with `END_OF_DATA`.
  - An unready chunk → 404. The viewer retries for up to 30 s on chunk 1 and 330 s on later chunks (DZWO960).
- **Stream items:**
  - 0 = header: its revision must equal the viewer's.
  - 1 = member list.
  - 2 = `[2][fromId][channelId][seq BE32][len BE16][data]`. The server must re-encode these from the uploaded fields.
  - 3 = end of stream.
- **List and report commands:**
  - CMD_GET_WATCHING_MATCH_LIST {match_category, compe_id, index} → `match_list[blob{match_id, game_phase, game_result{...}}]`.
  - CMD_SEND_WATCHING_RESULT {match_id, is_connect, phase, error_code, reason} → NOERR.
  - Matchday watch commands → NOERR, `is_exist_watch_match_reward:"NO"`.
  - CMD_SEND_DOWNLOAD_STATS_DATA is unrelated telemetry.
- **Feasibility:** medium, about 1.5–2 days plus a 3-client test (2 players + 1 viewer). Steps:
  1. Put `upload:"YES"` behind a flag.
  2. Store the uploaded records.
  3. Write AES chunks.
  4. Add an HTTP route that returns 404 until a chunk is ready.
  5. Fill the watching list.
- **Unknowns:**
  - The exact id in the URL path.
  - base64 vs raw `data`.
  - The padding and the zlib reading.
  - Which player's upload to keep.
  - The PES League lobby list path.

---------------------------------------------------------------------------------------------------
## Still unknown (co-op), and what would settle it
1. **How the real server formed groups** (solo grouping size; whether 1 human per group is accepted).
   - The client caps groups at 3. Nothing static forbids a group of 1 (see `cmp rdx,3` at 0x14111072f).
   - Settle: live test with `COOP_GROUP=1` and 2 clients.
2. **Which `is_owner` the session host needs** when several groups each have an owner. The last YES wins `mgr+0xf8`. Settle: live test (send a single YES on the HOME owner; observe SEND_MATCHING_RESULT member/vs_member).
3. **Whether 4–6-peer full mesh works** through our relay / NAT shim: game_sycom P2P vs RELAY for each pair. Settle: 4-client test plus netshim log.
4. **Contents of `match_data` (SET/GET_COOP_MATCH_DATA)** and whether an empty GET reply is accepted. Settle: capture one SET_COOP_MATCH_DATA after a co-op match; try size 0 first.
5. **room_phase and countdown values in WATCH_COOP_ROOM_OPTION;** what each `list_type` push changes in the room UI (TaskCoopRoomMain 0x1413680b0 not read in depth).
6. **Minimal WATCH_COOP_VSCOM_OPPONENT content** for non-MyClub vs-COM, and the values of GET_COOP_LEVEL_RESULT `status`.
7. **Whether the client sends CMD_WATCH_COOP_* requests** (the WATCH command objects are created locally; for 1v1 the push alone was enough). Settle: watch the server log during the first co-op test.
