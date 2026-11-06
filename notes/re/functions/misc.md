# Misc static RE: co-op, spectating, texts, match datagrams (PES2021.exe 1.01.00)

> **Live checks (2026-11-06):** the netshim capture that §4 decodes is from these tests; co-op (§1), spectating (§2) and the dialog list (§3) were not tested live yet.


Static reading only (no game launched, no network). Four questions from the main session; each section has the
answer, the evidence (addresses) and the live test that settles what is left. Tags: **[static]** (address),
**[log]** (existing capture), **[inferred]**, **[guess]**. Dumps are in the working folder (not published).

## Summary
1. **Co-op.** Any group size 1–3 and any group count is accepted. The cheapest 2-copy test is "1 vs 1 co-op"
   (two solo groups, HOME/AWAY); with `force_alone:"YES"` the client skips group matching, so the server only
   needs START_COOP_MATCHING + one WATCH_COOP_MATCHING push + a room record. Send exactly one `is_owner:"YES"`
   (HOME owner) in that push. match_data = base64 of 480 bytes (float32[70] + float32[50]); an empty GET reply is
   accepted. "1+1 vs COM" works in the client but needs the vs-COM team push; "2 vs 0" will most likely stall.
2. **Spectating.** `data` is base64 of a full 256-byte buffer (344 chars), keep `[:data_len]`. Each record is
   already a viewer item (no re-encoding). The client writes `[3][0]` itself. Both players upload; keep one.
   The URL id is match-info +0x78 (default "0"), not checked against the stream. The only watch menu is the PES
   League lobby; the 2-copy replay test needs the `onlinewatch` top-menu patch.
3. **Texts.** `{re}` = `{rf}` = "PES 2021" (region table always PES). The three checks = GATE/ACCOUNT/MENU
   connection up. 0x1410f8010 = SEND_NOTICE filler; 0x141070880 is not a command. Eight cheap dialogs listed.
4. **Match datagrams.** The clear "u32 seq | flags | 00 ff" header is P2pTurnIoFailover (RTT probe/ack + route
   ages), not Clltcp. The Clltcp header is inside the RC4 dlcp record. UHP types are class|method with no RFC
   interleave. Session type comes from `game_sycom`. A byte-by-byte decoding recipe and decoder are given.


---------------------------------------------------------------------------------------------------

---------------------------------------------------------------------------------------------------

## Q1. Co-op: group sizes, is_owner, match_data, and the reply sequence for two copies

PES2021.exe 1.01.00, static reading only. Dumps are in the working folder (not published).
Tags: **[static]** (address given), **[inferred]**, **[guess]**.
Object names used below:
- **C** = the co-op data object `[0x140ff0a20()+0x30]`. The 1st group slot ("mine") is at C+0x58, the 2nd
  ("opponent") at C+0x90. Each slot is {+0 group_id, +4 side, +8 owner_pid, +0x18 vector of 0x68-byte members}.
  C+0xf8 = "owner pid". C+0x88 = group-matching state. C+0xc0 = coop-matching state.
- **M** = TaskCoopMatchingManager (global 0x1436fe0e8; update 0x141079280; state at +0xb8).

### Short answers

1. **The client accepts any group size from 1 to 3, and any number of groups in the push.** No handler checks
   group count, group size or that the two sides are balanced. Members past the third in a group are dropped.
   [static 0x14110f5f0, 0x14110fe00 (cap at 0x14111072f)]
2. **The cheapest 2-copy co-op match is "1 vs 1 co-op": two groups of one, HOME vs AWAY.** It reuses the whole
   1v1 session path. With `force_alone:"YES"` in GET_COOP_QUICKMATCHOPTION the client even skips group
   matching. The server then needs only START_COOP_MATCHING plus one push. [static, see §3]
3. **"1+1 vs COM" (both humans on one side against the CPU) is supported by the client**, but it needs the
   WATCH_COOP_VSCOM_OPPONENT push with team data whose minimum content is not settled. Test it second.
   [static flow; content inferred]
4. **"2 vs 0" (a second group with no humans) is accepted by the push parser, but nothing marks that side as
   CPU-controlled.** It will most likely stall at team selection or team sync. Don't use it. [inferred]
5. **is_owner:** in the group push, exactly one YES per group (that pid sends START_COOP_MATCHING / START_COOP_VSCOM_OPPONENT).
   In the WATCH_COOP_MATCHING push, **send exactly one YES in the whole push, on the HOME owner.** The last YES
   in the push wins C+0xf8, and only that pid sends SET_GAMEMEMBER and SET_GAMEMEMBERENV.
6. **match_data is 480 bytes, sent base64:** two little-endian float32 tables, 70 + 50 entries. An empty or
   wrong-sized GET reply is accepted; the client just zeroes its local copy. Best: echo the last SET.

---

### 1. Group sizes and the is_owner layout

#### What the two push handlers accept [static]
- **WATCH_COOP_GROUP_MATCHING** (handler 0x14110f5f0):
  - `matching_state` → C+0x88: SEARCHING 1, FOUND 2, FINISH 3, CANCEL 4, TIMEOUT 5, FAILED 6.
  - Only on SEARCHING or FINISH is the member list rebuilt (clear 0x14110f480, add 0x141035790 into C+0x58).
  - There is no size check, and no check that my own pid is in the list.
  - Any member whose `member.is_owner == "YES"` sets C+0xf8 = that pid (0x14110f9e1). The last YES wins.
  - `com_level_min/max` → M+0xd0/+0xd4. An unknown level string leaves index 0.
- **WATCH_COOP_MATCHING** (handler 0x14110fe00):
  - `matching_state` → C+0xc0. CHECKALIVE and SEARCHING both give 1; FOUND 2, FINISH 3, CANCEL/TIMEOUT/FAILED 4/5/6.
    CHECKALIVE also creates CmdSendCoopAlive once (0x141110955).
  - It loops over **every** group in `group_list` (no count check) and looks for my pid (0x140ff0a30) in each
    member list (0x14110ffe9). In the group that has me, it reads my member's `side` (HOME=0, AWAY=1).
  - If my pid is in no group: the first group is taken as mine and HOME.
  - Each group goes to slot "mine" (if it is my group) or slot "opponent" (any other group; a third group would
    overwrite the second). The slot gets group_id, side (mine = my side; opponent = the other side), owner_pid.
  - `use_team_id` ≠ 0x3ffff → that team is loaded for the slot's side (0x14100cca0 at 0x14111025c).
    0x3ffff (262143) means "no team yet": nothing is loaded.
  - Members are copied up to 3 per group (`cmp rdx,3` at 0x14111072f). A group with 0 members leaves its
    slot's member vector empty; nothing complains.
  - Any `member.is_owner == "YES"` sets C+0xf8 (0x1411107b3). The last YES in the push wins.

#### Who sends what, depending on C+0xf8 [static]
| Command | Sent by | Check |
|---|---|---|
| START_COOP_GROUP_MATCHING | every human | none (0x141132970) |
| START_COOP_MATCHING | C+0xf8 only | 0x141133717 |
| START_COOP_VSCOM_OPPONENT | owner only | 0x14113ba40(C+0x48, my pid) at 0x14113a7f7 [inferred: it is the owner test] |
| SET_GAMEMEMBER | C+0xf8 only | 0x141135bb7 |
| SET_GAMEMEMBERENV | C+0xf8 only | 0x141135d76 |

- So after the WATCH_COOP_MATCHING push, **C+0xf8 must be one pid shared by all copies.** Give a single YES
  (the HOME owner). With no YES at all, each copy keeps its old C+0xf8 (its own pid from its solo group), and
  then **every** copy sends SET_GAMEMEMBER / SET_GAMEMEMBERENV. [static + inferred]

#### Which pids make up the P2P session [static 0x141133d00]
- TaskCoopSession builds the TaskMatchSession pid list (task+0x90) like this: for side 0 then side 1, take the
  slot whose side matches, then every member whose guest flag (member byte +0x30) is 0.
- So the list is **HOME humans first, then AWAY humans**, local guests excluded. 1 vs 1 co-op gives the same
  2-pid list as Quick Match.
- After this, the session is the 1v1 TaskMatchSession (0x1411430b0): GET_MATCHID, SEND/WATCH_SYNC_NOTICE,
  [START_SEND_GAME_SEQUENCE_DATA], GET_SERVER_ENV, TURN list, SYSTEM_NOTICE START/END, P2P.

#### The three layouts asked about
| Layout | Client path | Verdict |
|---|---|---|
| **1 vs 1 co-op** (group A = copy 1 HOME, group B = copy 2 AWAY) | coop matching (M states 6→7→9) | **works statically; same session as Quick Match.** Test first. |
| **1+1 vs COM** (one group of 2) | group matching → vs-COM (M states 1→2→4→5→9) | supported; needs WATCH_COOP_VSCOM_OPPONENT team data (see §4). Test second. |
| **2 vs 0** (group B with 0 members) | coop matching | parser accepts it, but only the vs-COM push sets the "opponent is COM" fields (C+0x9c=1, C+0x94, C+0xa0/0xa4; 0x1410d8f89..0x1410d8ff4). Expect a stall at team select / TaskSyncTeamData. [inferred] Don't use. |

### 2. How the menu picks the path [static]

- The quick-match option reply sets the menu fields (copier 0x14110df10, parser 0x141dbed90; key offsets
  checked: is_vscom → Result+0x1d0, force_alone → +0x1f0):
  - `force_alone == "YES"` → cmd+0x120 → menu+0x9c.
  - `is_vscom == "YES"` → cmd+0x121 → menu+0x90 (only the default cursor; [inferred]).
  - `cpu_level_min/max` index → menu+0x94 / +0x98.
- On "start" (0x140d7beb0): vsCOM = (selected menu choice == 1). Then:
  - **If force_alone, or local guest players are signed in** (0x141037100), it calls 0x14107a560:
    it skips group matching and jumps straight to M state 4 (vs COM) or 6 (vs humans). The helper 0x141035810
    makes a solo group: me + my local guests, C+0xf8 = my pid (0x1410359fe).
  - Otherwise 0x14107a510: M state 1 (group matching), vsCOM flag in M+0xcc.
  - The CPU level is the average of min and max on the direct path, or the min/max pair otherwise.
- After group matching (M state 2): vsCOM → state 4 (TaskCoopVSComOpponent), else state 6 (TaskCoopMatching).

### 3. Exact server reply sequence: two copies, Online Co-op → 1 vs 1 co-op match

Recommended variant **A** (`force_alone:"YES"`, no group matching). Variant **B** (group matching) is listed
after it. Everything after step 4 is the existing 1v1 machinery, as long as the server registers a room.

**Menu (each copy)**
1. `CMD_GET_IS_JOIN_COOP_ROOM` → `{result:NOERR, is_join_coop_room:"NO"}`.
2. `CMD_GET_COOP_MAINMENU_INFO` → `{result:NOERR, is_coop_banned:"NO", is_season_end:"NO", division:"", pre_season_rank:0,
   pre_season_rank_all_clan_num:0, division_change_type:"", is_exist_owner_clan:"NO", is_exist_new_request:"NO"}`
   (schema defaults are probably enough; the client compares these strings with "YES").
3. `CMD_GET_COOP_PROFILE` / `CMD_GET_COOP_REJECT_LIST`: defaults as in coop.md §5 (level 1, empty lists).
4. `CMD_GET_COOP_QUICKMATCHOPTION` → `{result:NOERR, is_vscom:"NO", force_alone:"YES", cpu_level_min:"NORMAL", cpu_level_max:"NORMAL"}`.
   `CMD_SET_COOP_QUICKMATCHOPTION` → NOERR.
5. The player picks the "vs players" choice (choice 0) and starts.

**Matching**
6. Each copy sends `CMD_START_COOP_MATCHING {matching_type:"ONLINE_COOP", coop_player_info_list:[{pid,…}], owner_pid, use_team_id, retry_num}`
   → reply `{result:NOERR}` at once. Keep (pid, use_team_id, session).
7. When two are queued, push **`CMD_WATCH_COOP_MATCHING`** (kind 0x60, rqid 0) to **both**, same body:
   ```
   {result:"NOERR", matching_state:"FINISH", group_list_num:2,
    group_list:[ blob{ member_list:[ blob{ member: blob{<1v1 member of copy 1, side:"HOME", is_owner:"YES",
                                                          matching_order:0>},
                                           guest_kind:"GUEST_NONE", is_follow:"NO", coop_level:1, coop_manner:"A"} ],
                       member_list_num:1, use_team_id:<copy 1's use_team_id, usually 262143>,
                       owner_pid:<pid 1>, group_id:1 },
                 blob{ member_list:[ blob{ member: blob{<1v1 member of copy 2, side:"AWAY", is_owner:"NO",
                                                          matching_order:1>}, guest_kind:"GUEST_NONE",
                                           is_follow:"NO", coop_level:1, coop_manner:"A"} ],
                       member_list_num:1, use_team_id:<copy 2's>, owner_pid:<pid 2>, group_id:2 } ]}
   ```
   - Nesting: `group_list` is a list of msgpack blobs; inside, `member_list` is a list of blobs; inside that,
     `member` is a blob. Key order of the parser 0x141dc9a70 confirms it.
   - The `member` blob is exactly what pesmatch `_member()` builds today (pid, pname, is_owner, matching_order,
     side, host/reflexive/relayed_addr, ntl_nat_type), with `side` set per group and the reflexive address
     rewritten per viewer as in 1v1.
   - Then register `rooms[rid] = {owner: pid1, members: [pid1, pid2], t: now}`. GET_MATCHID, the relay check and
     the notice relay all use `_room_of()`, so they then work unchanged.

**Session (existing handlers)**
8. `CMD_GET_GAMERELAY` (HTTP) → as for 1v1 (`game_sycom ""` or "P2P"; "SC" would switch to the CS relay).
9. `CMD_GET_MATCHID` → the room's non-empty match_id (`upload:"NO"`). It is asked twice: in TaskMatchSession
   and again in TaskCoopStartGame state 9. **An empty match_id fails StartGame** (0x1411359e0).
10. `SEND_SYNC_NOTICE` → relay; `GET_SERVER_ENV`; `GET_TURN_SERVER_LIST`; `SEND_SYSTEM_NOTICE` START/END → relay.
11. `CMD_SET_GAMERELAY_CHECK_RES` (both) → push `CMD_WATCH_GAMERELAY_CHECK_RES {error_code:"NOERR"}` once both reported.
    (TaskCoopSession registers that watch first, in its state 0, 0x141134700.)
12. `CMD_SEND_MATCHING_RESULT` (both) → NOERR.

**Start game (TaskCoopStartGame 0x141135600)**
13. `CMD_SET_GAMEMEMBER` (pid 1 only) → NOERR.
14. `CMD_SET_GAMEMEMBERENV {list_num, envlist[pid, side, sideLeader]}` (pid 1 only) → NOERR. Log it: it shows
    how the client mapped the sides.
15. TaskSyncManager → TaskSyncUserData / TaskSyncBLData: peer-to-peer, no server command [inferred from the
    callees of 0x14112c600].
16. `CMD_SET_GAMETEAM {home_team_id, away_team_id}` → NOERR.
17. `CMD_GET_MATCHID` → same id.
18. `CMD_GET_COOP_MATCH_DATA` → `{result:NOERR, match_data_size:0, match_data:""}` (or the stored blob, §5).
19. M state 14–22: multiplay sync and TaskSyncTeamData, peer-to-peer. ONLINE_COOP skips the MyClub/Matchday
    squad commands (they need game mode 0x13 or the Matchday flag; 0x14107976b, 0x14107979e).
20. Then the usual 1v1 match traffic (CHANGE_GAMEPHASE … END, SEND_GAME_RESULT, …). Post-match adds
    `SET_COOP_MATCH_DATA` and `SET_COOP_PLAYER_GRADE` (both NOERR) and `GET_COOP_LEVEL_RESULT` (one entry per
    member with unchanged level/points; the `status` values are still unknown).

**Variant B (force_alone:"NO")**: replace step 6 by:
- each copy: `CMD_START_COOP_GROUP_MATCHING {matching_type:"ONLINE_COOP", …}` → NOERR;
- server: push `CMD_WATCH_COOP_GROUP_MATCHING {result:NOERR, matching_state:"FINISH", member_list:[blob{member:blob{<self>, is_owner:"YES"}, guest_kind:"GUEST_NONE", is_follow:"NO", coop_level:1, coop_manner:"A"}], member_list_num:1, com_level_min:"NORMAL", com_level_max:"NORMAL"}` to each copy, about itself only;
- each copy then sends START_COOP_MATCHING as in step 6.

**Failure signals to watch for**
- No START_COOP_MATCHING after the group push → C+0xf8 is not this pid (is_owner missing).
- A START_COOP_MATCHING error → log line 274 and a return to group matching (M state 8).
- TaskCoopSession errors are logged at lines 600 (session) and 685 (relay check) of OnlineModeTaskCoopSession.cpp.
  A session failure with task code 4 ends co-op; other failures retry group matching (0x14107964c).
- StartGame log line 426 = empty match_id; line 271 (0x10f) = SET_GAMEMEMBERENV found no game-data object
  (0x1414b6a60 returned null, 0x141135df1); not a server problem.

### 4. "1+1 vs COM" with two copies (second test)

Flow [static]: menu choice 1 (vs COM), `force_alone:"NO"` →
1. both: START_COOP_GROUP_MATCHING {matching_type:"ONLINE_VSCOM_COOP", com_level_min/max} → NOERR;
2. server: push WATCH_COOP_GROUP_MATCHING FINISH to both with **both members** in member_list, one
   `is_owner:"YES"` (copy 1), `com_level_min/max` echoed;
3. M+0xcc is set → M state 4: the owner sends START_COOP_VSCOM_OPPONENT {is_myclub_mode:"NO", com_level_min/max,
   user_data[{pid, guest_kind}], owner_pid, my_team_id, vs_team_id} → NOERR. Each copy waits **60 s** for the push
   (0x14113a7d0: deadline = now + 0xea60);
4. server: push WATCH_COOP_VSCOM_OPPONENT to both:
   `{result:NOERR, owner_pid:<pid1>, my_team: blob{team_name, team_name_short, base_team_id:<my_team_id>, team_data: blob{…}, team_name_commentary_id},
     com_team: blob{side:"AWAY", com_level:"NORMAL", pid:0?, pes_pname:…, team_name, team_name_short, base_team_id:<vs_team_id>, team_data: blob{…}}}`.
   - The handler 0x1410d8ef0 sets: my side = the opposite of `com_team.side` (C+0x5c), opponent side (C+0x94),
     C+0xf8 = `owner_pid`, C+0x9c = 1, C+0xc0 = 3 (FINISH), opponent team key from `base_team_id` (C+0xa4).
   - my_team goes through 0x1410d9890 (`base_team_id` ≠ 0x3ffff → team loaded by id, 0x14100cca0).
   - com_team goes through 0x1410d9080. It builds the squad from `team_data` (member list +0xf0, gameplan
     +0x108/+0x10c) via 0x141028c60 and validates it with 0x1410289d0. **Whether `base_team_id` alone (empty
     member_list) is enough is unknown.** [static flow; minimum content unknown]
5. M state 9: TaskCoopSession with the pid list = the two humans (both on one side). Then StartGame as in §3.

Live test: as §3 but with this push. First try `base_team_id` only (a real team id from the game DB, e.g. the
team the owner picked = `vs_team_id`; `team_data` = blob{member_list:[], member_num:0}). If the match loads,
done. If it stalls after the push, fill team_data from the local DB (`tools/pesdb.py team <id>`).

### 5. match_data in SET / GET_COOP_MATCH_DATA

**SET_COOP_MATCH_DATA request** (filler 0x141112860, packer 0x141dd4980) [static]:
- `pid`.
- `match_stats` = a map with **one key, `grade`** = int from the global coop struct +0x3f2f0 (0x141dd4f73..0x141dd503b).
- `match_data_size` = **480** (0x1e0, set at 0x1411128e4).
- `match_data` = **base64** (encoder 0x14184d8c0) of the 480 bytes copied from `[[0x143705e10]+0x50] + 0x3f110`.

**What the 480 bytes are** [static 0x140a0e940, at 0x140a1014d and 0x140a102bd]:
- `+0x000`: **float32[70]** (little-endian), `+0x118`: **float32[50]**. 280 + 200 = 480.
- During match set-up (0x140a0e940, called from 0x140a0d190) the client pairs each value with its index, sorts
  each table in descending order (qsort 0x141e53540, comparator 0x140a13130 compares floats), then:
  - table 1: the top 3 entries with value ≥ 1.0 write their index into match-setting slots 0x3c2..0x3c4;
    otherwise the slot gets 0x46 (= "none").
  - table 2: the top 9 entries with value ≥ 1.0 write their index into slots 0x3c5..0x3cd, plus a level in
    slots 0x3ce.. of 1 (value < 2.0), 2 (< 3.0) or 3; otherwise 0x32 (= "none").
- So it is a learned per-player co-op profile: two score tables whose top entries steer something at the next
  co-op kick-off. What the 70 and 50 indices name is unknown [guess: preferred positions / play styles].

**GET_COOP_MATCH_DATA reply** (parser 0x141dd3f00, handler 0x1411124e0) [static]:
- `match_data_size` NUM → Result+0x1d0. `match_data` STR is **base64-decoded** (0x14184e640) into Result+0x1d4.
- The handler copies the decoded bytes into the global struct **only if `match_data_size == 480`**
  (0x1411122c0). Otherwise it copies a zeroed 480-byte buffer. Either way it clears grade (+0x3f2f0) and a flag
  (+0x3f2f4). There is no error path.
- **So `{match_data_size:0, match_data:""}` is accepted**: the player's profile is reset to zeros (no top
  entries). Echoing the stored base64 string from the last SET keeps it. The same blob most likely also belongs in
  GET_COOP_PROFILE `coop_profile_data.match_data` [inferred].

### 6. Live test plan (two copies, one PC)

Each step stops at its first proof (minimal-live-tests rule).

1. **Menu only (one copy).** Open eFootball → Online Co-op.
   - Proof: the log shows GET_IS_JOIN_COOP_ROOM / GET_COOP_MAINMENU_INFO / GET_COOP_QUICKMATCHOPTION with
     NOERR and the co-op menu draws. If it errors, the code on screen names the command (messages.md §2).
2. **Matching (both copies), variant A.** Both choose the vs-players option and start.
   - Proof 1: one START_COOP_MATCHING from **each** copy (force_alone worked; no START_COOP_GROUP_MATCHING).
   - Proof 2: after the push, both send GET_GAMERELAY and then GET_MATCHID. That proves the group_list nesting,
     `side` and FINISH.
3. **Session and start game.** Proof: SET_GAMEMEMBER and SET_GAMEMEMBERENV come **only from pid 1** (the
   single is_owner YES), then SET_GAMETEAM, GET_MATCHID and GET_COOP_MATCH_DATA from both. Stop when both reach
   team selection or the match loads; a full match is not needed.
4. **match_data (only if a match is played to END anyway).** Proof: SET_COOP_MATCH_DATA has
   `match_data_size:480`, a 640-character base64 string, and it decodes to 120 plausible floats.
5. **1+1 vs COM** (separate run, §4). Proof: START_COOP_VSCOM_OPPONENT from copy 1 only, then GET_GAMERELAY
   from both after the push.

### Still unknown
- Minimum content of WATCH_COOP_VSCOM_OPPONENT `com_team.team_data` (does base_team_id alone load a squad?).
- What the 70 + 50 match_data indices stand for.
- GET_COOP_LEVEL_RESULT `status` values and whether an empty `coop_level_user_list` is accepted.
- Whether 4–6-peer meshes connect through our relay (needs more than two copies).
- What the room (party) path adds on top: room_phase / countdown values (coop.md §6), not needed for the
  quick-match tests above.

---------------------------------------------------------------------------------------------------

## Q2 Spectating: open points from spectate.md, settled statically

PES2021.exe 1.01.00. Tags: **[static]** read in code at the address given, **[inferred]**, **[guess]**.
Dumps are in the working folder (not published). Flow JSONs were read from `dt16_all.cpk`.

### Headline corrections to spectate.md

1. **`data` is base64, and it always encodes the full 256-byte record buffer.** `data_len` holds the real length.
   The server must base64-decode and keep the first `data_len` bytes. **[static]**
2. **Each record's `data` is already the exact item the viewer reads, for every kind, kind 2 included.** The
   recorder writes kind-2 frames as `[2][from][chan][seq LE32][len LE16=9+n]` followed by a payload of
   `[2][from][chan][seq BE32][len BE16=n][n bytes]`, and the payload is what gets uploaded. **The server does not
   rebuild anything.** It just joins the decoded `data` of one uploader's records in arrival order. **[static]**
3. **The client writes the end item itself.** It always records `[3][0]` when the session closes. **[static]**
4. **Both players upload.** No code path checks for owner or host. **[static + inferred]**
5. **The only menu that leads to watching is in PES League lobbies.** It is not in the Friendly Match Lobby. The
   only code that switches the client into observe mode is the debug or test process `OnlineTestMatchWatch`.
   **[static]**

---

### (a) The viewer's `id` path component

- **Answer.** The id is the match-info string at `[0x1436fced8]+0x58 → +0x78`. The same field is sent back as
  `match_id` in CMD_SEND_WATCHING_RESULT. So **the URL id = the match_id the viewer reports**. If the field is
  empty, the client uses **`"0"`**. **[static]**
- **Evidence:**
  - The match-info object is `*(0x1436fced8)+0x58`. Getter 0x140ff0a20; the object is created in 0x140fee760 →
    0x141091ee0. That constructor jumps into the protected section 0x1446758e0.
  - Field `+0` is the mode. 0x141092610 logs it as MODE_UNSET 0, MODE_P2P 1, MODE_CS 2, MODE_OBSERVE 3.
  - TaskMatchSession SetConfig 0x141144d80: for mode 3 (0x1411454cb) it copies `[mi+0x78]` into
    cfg+0x48 (task+0xf0). If that is empty it writes `"0"` (0x1411454fd, string 0x142877f04).
  - 0x14115c170 at 0x14115c266: if cfg.mode == 3, cfg+0x48 → 0x1411d0500, which sets the stream-watcher id
    (+0x58). That id is the `<id>` in `<WATCHING_GAME>/<id>/<N>.enc`.
  - 0x14114e230 only clears the id (it sets `""`).
  - The SEND_WATCHING_RESULT filler 0x1410e5e80 at 0x1410e5f08 copies `[mi+0x78]` into `match_id`.
- **Who writes `mi+0x78`?** No plain code does. A byte scan of every caller of 0x140ff0a20 found only two
  functions that touch `+0x78`, and both only read it. So the setter is inside the protected or obfuscated
  section, as the object's constructor is. **[static]** For the lobby list path it is almost certainly the `match_id`
  of the chosen GET_WATCHING_MATCH_LIST entry. **[inferred]**
- **The URL id is not checked against the stream.** The header item's id is compared only with earlier header
  items in the same stream (playback +0x38 flag, 0x1412591f9). The first header just stores it. So the server can
  serve any stored stream under any id. **[static]**
- **Live check.** The viewer's first GET shows the id. Its SEND_WATCHING_RESULT `match_id` should be the same
  string.

### (b) `data`: base64 or raw, and what `data_len` holds

- **Answer: base64**, standard alphabet, `=` padding, no line breaks. It always encodes **256 bytes**, so each
  string is 344 characters. `data_len` is the raw length (≤ 256). **[static]**
- **Evidence:**
  - Packer 0x141e50970 copies each 0x118-byte record:
    - +0 type
    - +4 fromId
    - +8 channelId
    - +0xc sequenceNumber
    - +0x10 a 256-byte buffer
    - +0x110 buffer size, the constant 0x100 (set at 0x141233591)
    - +0x114 the real length (set at 0x1412335c1)
  - At 0x141e513e1 it calls encoder 0x14184d8c0 with length = **+0x110 (0x100)**. `data_len` is +0x114
    (0x141e514e0).
  - Encoder 0x14184d8c0 calls 0x141857490 (out length = 4·⌈n/3⌉) and then 0x1418576f0. That function uses the
    table "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" and `=` padding. Its CRLF-every-76
    flag is passed as 0 (0x14184d90e).
  - The record builder 0x141233440 zero-fills the buffer (0x14123358c) and copies in the payload.
- **Caveat.** The copy at 0x1412335d0 has no 256-byte bound. A payload over 256 bytes would overflow the client's
  stack, so in practice the game should never record one. **[inferred]** Server: if `data_len` > 256, log it and
  keep 256 bytes.
- **Wire size.** A batch can hold up to about 0x4000 bytes of `(len+0x20)` per record (0x1411ced9b). With the
  344-character strings, one SEND_GAME_SEQUENCE_DATA can be tens of KB of msgpack. **The server's NCL
  0x70/0x71/0x72 fragment reassembly must work** (wire.md §1). **[inferred]**

### (c) Which players upload

- **Answer: every peer whose GET_MATCHID reply has `upload:"YES"`.** That is both players in 1v1, and all
  members in co-op. **[static for each step; "all" inferred from there being no role check]**
- **Evidence (none of these steps checks owner or host):**
  - GET_MATCHID handler 0x140ff76e0 stores `match_id` in the manager's match object. Through 0x140ff7cd0 it also
    sets `+0x70 = 1` ("record") at 0x140ff77bc.
  - TaskMatchSession state 5 (0x141143733) sends START_SEND_GAME_SEQUENCE_DATA when `[task+0x118]` exists and
    upload == YES.
  - SetConfig stores the upload flag at cfg+0x40 (0x141145528). 0x1411444e0 then creates the SendGameSeqTask
    (0x14114e4d0 → 0x1411daed0).
- **What each upload holds.** The recorder hooks both send and receive:
  - Sends, 0x1412563a0: fromId = own node id, from 0x14123ca10.
  - Receives, 0x141240aa0: fromId = the sender's id.
  - So **each stream is one peer's complete two-way view** of the session. **[static]**
- **Server.** Keep exactly **one** uploader per match: the first START_SEND, or the HOME player. Never merge two
  streams. Each stream has its own header and its own order. **[inferred]**

### (d) Header bytes, and kind-3 reasons

**The kind-0 header** is written by 0x141256720. All fields are big-endian.

```
00 ver=00 rev0 rev1 rev2 n build[n] m id[m] b1 b2 b3 k members[k]
```

- `rev0`–`rev2` come from 0x141989390(0..2). `build` comes from 0x14149a470+0xe8.
- `id` = the GET_MATCHID match_id (manager+0x40 → +0x30, 0x1412568b0). If it is empty:
  - In CS mode it is `"MATCH_ID_DIRECTONLINE"`.
  - Otherwise **the header fails and recording stops** (0x141256863 → recorder error flag +0x18).
  - **So GET_MATCHID `match_id` must not be empty.** **[static]**
- `b1` = low byte of session field +0x758, read through 0x14115d450 (thunk chain 0x1411b3480 → 0x1411ae520 →
  0x1411ac040). **[static fetch; meaning unknown, guess: session role or kind]**
- `b2` = the recorder's own node id: 0x14123ca10 → 0x1411f7c00, the same getter that supplies fromId for sends.
  **[static]**
- `b3` = the first entry of a node-id list (0x1411b3470 → 0x1411ae450 → 0x1411f8020, msp vtable +0x48). It is
  0xff if the list is empty. **[static fetch; guess: host or master node id]**
- `k`, `members` = the sorted member node-id list (0x1411b3460, msp vtable +0x40; sorted by 0x141255df0).
- **What the viewer checks** (0x141258f90):
  - The item is at least 20 bytes long.
  - ver = 0.
  - The three revisions equal its own.
  - The build string equals its own.
  - Every later header in the same stream must match the first on `id`, `b1`, `b3` and `members`; `b2` is only
    stored.
  - **Server: forward the header verbatim and never edit it.** **[static]**
- **The kind-1 item** `[1][count][ids…]` is the member list again (0x141256cf0). It is written whenever the list
  changes. **[static]**

**Kind 3.** The recorder writes `03 00 02 03 00`, which is a frame whose item is `[3][0]`. It does this at
0x141255fa0, called on session close from 0x141240750 (P2P) and 0x141245f50 (CS). **The reason byte is always 0.**
**[static]**

**How the viewer maps the reason** (0x141258ae0 → ObserveSession::Update 0x141246ca0; lines are PWTV source lines):

| Playback end reason | Meaning | Result |
|---|---|---|
| 0 | `[3][0]` read | normal end, no error |
| 0xfd | END_OF_DATA reached with no kind-3 item (0x141259d3c) | error PWTV340 (code 0) |
| 0xfe | not set in the code read | error PWTV343 (code 1) |
| 0xff or any other value | parse error or bad item | error PWTV347 (code 2) |

- **Server:** if the uploader vanished without a kind-3 record, append `[3][0]` yourself before END_OF_DATA.

### (e) Padding and zlib

- **Padding.** Unpad 0x141852a20 is called from the chunk decrypter 0x1411df7c0 at 0x1411df8f3. It reads the last
  byte n, fails if n > 16, and strips n bytes. It does not check the other pad bytes. PKCS#7 works. **[static]**
- **zlib.** 0x1403a2900 → 0x1403a2920 is zlib 1.2.11 `uncompress2`. It calls inflateInit_ with "1.2.11"
  (0x1403a295d) and then inflate 0x1403b1020, which owns "incorrect header check". So **flag 1 means a zlib-wrapped
  stream (RFC 1950), not raw deflate.** Python `zlib.compress()` output is correct. **[static]**

### (f) Where a viewer starts, and serving an old recording

**Menu routes** (flow JSONs plus code):

1. **PES League lobby.** Room list `OnlineLobbyRoomSelect` (slot 51 = 0x140bdc330) posts `04_watching_match`
   → `Online/Lobby/LobbyWatchingMatchList` → `proceed` → `Online/WatchMatch/WatchMatch` → `WatchMatchMenu`
   → `Match/MatchSetup` → `WatchMatchEnd` (`0_pesleague` goes back to the list).
   - The entry is offered only if 0x1410419e0 is true: lobby flag `[mgr+0x20]+0x78` is set **and** lobby kind
     `[[mgr+0x20]+8]+0x78` ∈ {3, 4}. **[static]**
   - 3 and 4 are the PES League and myClub PES League lobbies. Evidence: 0x141051210 picks
     TaskGetWatchingMatchList**PesLeagueLobby** for 3 and **MyClubPesLeagueLobby** for 4. **[static; names]**
   - **The Friendly Match Lobby (FREE) never shows this entry.** **[inferred]**
   - The list request (0x14109b7f0) is `match_category` = enum 0x141b99ef0:
     - index 19 = **"PESLEAGUE_LOBBY"** (task 0x141050f00).
     - index 20 = "MYCLUB_PESLEAGUE_LOBBY" (task 0x141050ea0).
     - Initializer 0x14021a740: 0 RANK_MATCH … 13 ONLINE_COOP, 14 MYCLUB_COOP, 15 ONLINE_COOP_VSCOM,
       16 MYCLUB_COOP_VSCOM, 17 MYCLUB_EVENT_COMPE_COOP, 18 MYCLUB_VSCOM_COMPE_COOP, 19 PESLEAGUE_LOBBY,
       20 MYCLUB_PESLEAGUE_LOBBY, 21 MATCHDAY, 22 MATCHDAY_COOP.
   - Then `compe_id` = the current PES League competition, and `index` = page.
2. **Matchday.** `MenuMainMatchday` posts `06_observe` → `MatchdayProcToWatchMatch` → `0_match` / `7_match_coop`
   → WatchMatch. The id source on this path was not traced. It needs the Matchday flows. **[data]**
3. **Debug or test.** LoginFlow `14_test` (game mode 0x1d) → WatchMatch. **There is no menu entry** (lobby.md).
   - The WatchMatch process is `process::OnlineTestMatchWatch` (vtable 0x1428eff78; update 0x141369410; setup
     0x141368db0).
   - **It is the only code that sets mode 3:** `mov dword [mi], 3` at 0x141368e04. A byte scan found no other
     writer. It also copies the debug vars `target_ip_address` / `target_port_num` and starts TaskMatchSession.
   - It never sets the id, so a viewer arriving here asks for **`/<WATCHING_GAME>/0/1.enc`** unless `mi+0x78`
     was filled before. **[static]**

**What the viewer sends before the first `.enc` GET** [static]:
- Route 1 only: CMD_GET_WATCHING_MATCH_LIST {match_category:"PESLEAGUE_LOBBY", compe_id, index}.
- Then TaskMatchSession in observe mode (task+0x8c = 1, set at 0x141142ee8):
  - It skips GET_MATCHID, the sync notices and START_SEND (0x14114326b → state 2 → 5 → 7).
  - It sends only **CMD_GET_SERVER_ENV**. There is no GET_TURN_SERVER_LIST, because state 9 is taken only when
    mode == 1 (0x141143abd).
  - SetConfig and CreateMultiplaySession(observe) follow, then HTTP `.enc` polling.
- When TaskMatchSession reaches state 0x1a, WatchMatch posts `proceed` (0x1413694c3).
- After the match, `OnlineObserverEnd` sends **CMD_SEND_WATCHING_RESULT** {match_id = mi+0x78, is_connect,
  phase, error_code (last reporter code), reason}.

**Replaying a finished match from storage: should work.** **[inferred]**
- The stream has no timestamp or "live" check:
  - The header checks only revision, build, id/b1/b3/members consistency (0x141258f90).
  - The downloader just fetches N = 1, 2, … with about 1 s between successful chunks, and pauses while more than
    0xC000 bytes are queued.
- No code read compares `game_phase` from the list. The list item renderer 0x140be2aa0 only fills date,
  informationText and matchName. **[static for what was read; not every list path was traced]**
- OnlineModeTaskPesLgWatching (0x1421a71c0, singleton 0x1421a7450) is a **PES League entry-deadline timer**. It
  reads match-info +0x38 and a deadline, and has nothing to do with observe streams. It does not reject old
  matches. **[static + inferred]**

**What the server must answer for "copy 2 watches what copy 1 recorded":**
1. While copy 1 plays an online match (the Friendly Match Lobby match works live):
   - GET_MATCHID → `{match_id:"<non-empty>", upload:"YES"}`.
   - START_SEND_GAME_SEQUENCE_DATA → NOERR.
   - SEND_GAME_SEQUENCE_DATA → NOERR. Store, for **one** uploader, the base64-decoded `data[:data_len]` in order.
   - A vs-COM match cannot be used: it is offline and has no session to record. **[inferred]**
2. Build the stream: the concatenated items, plus `03 00` if missing. Store it as `.enc` chunks:
   - Chunk body: `IV(16) || AES-256-CBC(key 0x142897bd0, PKCS7([flag] || body))`.
   - flag 0, or flag 1 with zlib.
   - The last chunk's body ends with `END_OF_DATA`.
   - Serve `GET <WATCHING_GAME>/<anything>/<N>.enc`, with 404 beyond the last chunk. For the test, map id `"0"`
     and any unknown id to the latest stored stream.
3. Viewer side:
   - GET_SERVER_ENV: the normal reply.
   - SEND_WATCHING_RESULT → NOERR.
   - Route 1 additionally needs GET_WATCHING_MATCH_LIST →
     `{match_list:[{match_id, game_phase, game_result{date, player[...], ...}}], match_list_num:1, total_match_num:1}`,
     **plus the whole PES League lobby path**: GET_PESLEAGUE_COMPE_LIST with `match_type:"PES_LEAGUE_MATCH_1VS1_LOBBY"`,
     JOIN_PESLEAGUE_COMPE, and the lobby commands for lobby_type PESLEAGUE. That is the expensive part.

### Live test (two copies, one PC)

The goal: prove the upload format, then a replay, with the fewest steps. Stop each step at its first proving signal.

**Step 1: recording (both copies, about 5 minutes).**
- Server flag: GET_MATCHID `upload:"YES"`. Log every SEND_GAME_SEQUENCE_DATA:
  `len(data)`, `data_len`, `type`, and the first 16 decoded bytes.
- Play a Friendly Match Lobby match, copy 1 vs copy 2.
- **Proves (b)/(c):**
  - Every `data` string is 344 characters, and decodes to 256 bytes with zeros after `data_len`.
  - Both pids send START_SEND.
  - The first record is type 0 with data `00 00 <rev BE…>`.
  - Type-2 records' data start with `02 <fromId> <channelId>`, then BE seq = `sequenceNumber`, then BE len =
    `data_len - 9`.
- Also watch for NCL 0x70–0x72 fragments on SEND_GAME_SEQUENCE_DATA. If the server mis-reassembles them,
  that is the first thing to fix.
- Kick-off is enough for this signal. Let one short match reach END only if the `03 00` check (d) is wanted: the
  last record of each uploader should be type 3, data `03 00`.

**Step 2: replay (one copy).**
- This needs a way into mode 0x1d, because the lobby route needs a PES League lobby that we don't serve.
- Cheapest: a dinput8 patch (lobby.md §5). Rename top-menu record 15 to `onlinewatch` with mode 0x1d. Then
  Kick Off → that item → login → LoginFlow `14_test` → WatchMatch.
- Server: serve the stored stream under id `0`.
- **Proves (a) and (f):**
  - The access log shows `GET <WATCHING_GAME>/0/1.enc` (or the real id).
  - Only CMD_GET_SERVER_ENV comes before it.
  - The match plays.
  - It ends with CMD_SEND_WATCHING_RESULT {match_id:"" or "0", error_code empty}.
- A PWTV340 code at the end means the `[3][0]` item is missing. PWTV347 means an item is malformed.
- JOIN_ONLINE_MODE for mode 0x1d may ask for an `online_mode` value we have not seen. Log it and reply NOERR.

### Still unknown

- The writer of match-info +0x78 on the lobby and Matchday paths. It is in the protected section; the live
  step 1 or 2 log settles it indirectly.
- What `b1` and `b3` mean. They need no server action.
- Reason 0xfe's setter.
- Whether `game_phase` in the list filters anything.
- The `online_mode` that JOIN_ONLINE_MODE sends for game mode 0x1d.

---------------------------------------------------------------------------------------------------

## Q3. Texts: `\x11` macros, the generic-dialog checks, two +0x74 setters, and cheap dialogs to photograph

PES2021.exe 1.01.00, static reading only. Dumps are in the working folder (not published).
String tables were read from `download/dt80_100E_x64.cpk` (`use/string/all.str`, `common/string/sys_use.str`).
Both files are "WESYS" containers: a 16-byte header, then zlib.

### (a) How `\x11` macros expand

#### The parser
- The text tokenizer is at 0x140e2ad20 [static]. It handles NUL, `\n`, `\t`, and `\x11`. Any other byte is a plain character.
- After `\x11` it reads one letter. A jump table at 0x140e2aea8 (0x140e2add0) maps letters `c`..`w` to these handlers [static]:

| Letter | What it does | Handler |
|---|---|---|
| `c` | text colour; 8 hex digits RRGGBBAA follow, e.g. `\x11c848284ff` | 0x140e2af00 |
| `d` | token type 4 (layout) | 0x140e2ae05 |
| `i` | block marker, then one digit; `{i0}` / `{i1}` open and close the heading of a help item | 0x140e2ad90 |
| `j` | menu button glyph, `ja`..`jo` | 0x140e264b0 (callback slot 0) |
| `k` | menu button glyph, `ka`..`ki` | 0x140e26960 (slot 1) |
| `l` | in-match action button, `la`..`ln` | 0x140e26530 (slot 2) |
| `o` | in-match action button | 0x140e267b0 (slot 3) |
| `t` | in-match action button | 0x140e26690 (slot 4) |
| `n` | competition name, `na`..`nd` | 0x140e25e80 (slot 5) |
| `r` | **game-title strings, `ra`..`rn`** | 0x140e25ee0 (slot 6) |
| `m`, `w`, `s` | skip or flag tokens (layout) | 0x140e2ae88 / 0x140e2ae95 / 0x140e2ae7a |

- The callback slots are registered by 0x140e268a0. Registration is 0x140e2b090 into the table at 0x1436f9590; dispatch is 0x140e2b050 [static].

#### `{r?}`: game title (the answer for `{rf}` / `{re}`)
- The static initializer 0x1401d6b00 builds a map at 0x1436f9578. The map has three keys, and each key holds 14 std::strings [static]:
  - key 0 = Winning Eleven, English;
  - key 1 = Winning Eleven, Japanese;
  - key 2 = PES.
- The lookup 0x140e25ee0 works like this [static]:
  - Index = letter − 'a', which must be < 14.
  - Key = 2 when 0x1414a18e0() == 0. Otherwise key = (language var 0x1434fce64 == 5).
- 0x1414a18e0 reads 0x1434fce54. The region setter 0x1414a1900 **always writes 0** there (0x1414a1912). So this build uses key 2, the PES list. [static; the only writers in `.trace` are these two. A write from the protected section cannot be ruled out, so this is confirmed by the live test below.]
- PES list, in order [static, 0x1401d70d7..0x1401d73a2]:

| Tag | Text | Tag | Text |
|---|---|---|---|
| `ra` | PES 2021 | `rh` | PES 2020 |
| `rb` | PES 2021 | `ri` | PES 2021 LITE |
| `rc` | PES | `rj` | PES 2021 LITE |
| `rd` | PES | `rk` | PES 2021 DEMO |
| **`re`** | **PES 2021** | `rl` | PES 2021 DEMO |
| **`rf`** | **PES 2021** | `rm` | myPES |
| `rg` | PES 2020 | `rn` | eFootball PES 2021 SEASON UPDATE |

- The texts fit every use in the string table [static data]:
  - "Log in to {rf} Server"
  - "Use {rh} edit data in {rf}" = PES 2020 data in PES 2021
  - "{rm} is a mobile phone application" = myPES
  - `Documents\KONAMI\{rn}\WEPES`
  - "In {rj}, you can only play a portion" = LITE
- So the player sees **"An unexpected problem has occurred while communicating with the PES 2021 server."** and **"Your version of PES 2021 is out of date."** [static]
- On a Winning Eleven SKU the same tags give "Winning Eleven 2021", "WE 2021", and so on (0x1401d6b51..).

#### Buttons (`j`, `k`, `l`, `o`, `t`)
- These expand to **private-use characters U+E000–U+E296**, which the button-icon font draws. The table at 0x1434c0660 holds 16-byte rows {len, utf8 ptr}, e.g. row 0 = U+E009 [static].
- `j` uses the index table 0x1427ae3b8: ja..jl → rows 0..11, jm/jn/jo → rows 29..31.
- `k` uses 0x1427ae3f8. `ka` and `kb` resolve to the **confirm and cancel** glyphs: the row indices are held in 0x1434c08c0/0x1434c08c4, set to 4 and 3 at 0x140e268a4 [static].
- `l`, `o` and `t` are in-match actions. They are looked up through the current controller's button assignment (0x1414b6a60, 0x980 bytes per controller), so they follow the player's key setup [static].
- None of these matter to the server.

#### Competition names (`na`..`nd`)
- 0x140e25e80 calls a function pointer at 0x1436f9570 with index 0..3 [static].
- Nothing in `.trace` writes that pointer, so it is set at runtime or from the protected section.
- These are the licensed competition names. `na` reads like the UEFA Champions League ("Europe's elite") [guess]. `nb`, `nc` and `nd` are other licensed cups [guess]. None of them is used in server-error texts.

### (b) The three 0x14114e7e0 checks that gate the generic dialog
- 0x14114e7e0(mgr, k, 0) is called with mgr = the global at 0x1436fe490 (getter 0x14114e770). It returns true when [static]:
  - the online Client `mgr+0x80` exists; and
  - **either** the special case holds: k == 2, `mgr+0x120` != 0 and `mgr+0xe0` == 0;
  - **or** 0x1411df1a0(client, k) is true. That means Client state `+0x70 == 1` (idle, logged in) **and** the connected flag `+0x160` of server slot k is set. The slots are 0x98-byte entries under `[client+0x88]`.
- The slot names come from the table at 0x1434d6460 [static]: **k = 0 → "GATE", 1 → "ACCOUNT", 2 → "MENU"**.
- The flag means "connected" [inferred]. The disconnect path 0x1411df070 / 0x1411dee60 sets state 4 for slot k and returns to state 1 once the flag clears.
- **Meaning:** a msgid-0 error shows the generic "unexpected problem … CODE:" dialog only while at least one of the GATE / ACCOUNT / MENU connections is up (0x1411ca6f7..0x1411ca744) [static].
  - If all three are down, nothing is shown. The "Connection to server lost" path then handles it [inferred].
  - The same three checks run while a CommandErrDialog is open (0x1411ca63e..0x1411ca693). When all are down, the open dialog is closed through its vtable +0x20 [static].
- Specific msgids, i.e. anything other than 0 or −1, skip these checks and are always shown (0x1411ca74b) [static].

### (c) 0x1410f8010 and 0x141070880
- **0x1410f8010 = slot 7 (request filler) of `command::CommandObjectCmdSendNotice`**, vtable 0x1428553c0 [static]. That is CMD_SEND_NOTICE {pid_list, data}. Live, the lobby owner sends it with `data.notice_key "ROOM_SHARE_INFO"` (OnlineLobbyFreeSendRoomShareInfo).
  - It copies the pending notice from the online-mode manager 0x140ff0a20 → `+0x50`: the pid vector at +8, the data string at +0x20, and a **kind int at +0x40**.
  - By kind [static 0x1410f8187..0x1410f81ab]:
    - kind 1 → `+0x120 = 1`, **auto dialog off**;
    - kind 2 → `+0x120 = 0`, **auto dialog off**;
    - any other kind → `+0x120 = 0`, **auto dialog on**.
  - It also stores "NETCLIENT" and "MENU" as the client / svrtype strings (0x1410f8202).
  - So an error reply to SEND_NOTICE shows a table dialog only for the "other" notice kinds. SEND_NOTICE has no specific rows; its errors would show generic text [inferred].
- **0x141070880 is not a command object.** It belongs to `onlinemode::TaskMatchdayCompeManager` (vtable 0x142825c08; caller 0x1410705a0 ← 0x141070570) [static].
  - Its `+0x74` is the task's own "dialog already shown" flag, next to its state at `+0x78`. It is not the CommandObject auto-dialog flag.
  - messages.md listed it by offset alone. That was a false match, so remove it from the auto-dialog list.

### (d) Cheap server replies whose dialog we have not photographed (best first)
Excluded because they were already seen: 0x2080005 "online service is currently unavailable" (VMEO_0006), "Connection to server lost", "Unable to send or receive data." (bare GET_BLOCKLIST), "No new Live Update available." / Live Update texts, "This match will be…".

How each row was checked:
- The auto dialog was checked per class with `misc/q3/ad.py`. It scans every function that stores the class vtable for a `byte [reg+0x74]` write. "on" = no such write, so the base default 1 applies (0x1411661a9). CHECK_STRING and JOIN_ROOM_NTL came out "off", which matches messages.md and so validates the script [static].
- Specific msgids are always shown (see (b)).

| # | Command → ERR | msgid → text the player should see | When the client sends it | Auto dialog | What it proves |
|---|---|---|---|---|---|
| 1 | **JOIN_ONLINE_MODE → ERR_DIFFERENT_PATCHVERSION** | 0x02080068 "Your version of **PES 2021** is out of date.\nPlease update to the latest version and reconnect." | every online-mode entry (one copy, repeatable) | on (CommandObjectCmdJoinOnlineMode 0x142852578) | the `{re}` expansion; one dialog only: the task then sets login kind 3 (0x140fe584f), which has no TaskError text |
| 2 | **GET_BLOCKLIST → ERR_DATABASE** | 0x020f007c "An error occurred while accessing the **PES 2021** database." | Friendly Match Lobby, after JOIN_LOBBY / GET_LOBBYINFO | on (…GetBlockListFree 0x14284e268) | the `{rf}` expansion; the most common text (202 table rows) |
| 3 | **CREATEJOIN_ROOM → ERR_ROOMCREATELIMIT** | 0x020f001a "You cannot create any more Match Rooms." | lobby → Set up Match Room → Next (one copy) | on (…CreatejoinRoomFree 0x14284d4d0) | a room-error text; whether the screen returns to the Room List |
| 4 | **JOIN_LOBBY → ERR_LOBBYUSERMAX** | 0x0200000e "Cannot participate due to full quota." | entering Friendly Match Lobby | on (…JoinLobbyFree 0x142851e68) | a "server full" text for lobbies |
| 5 | **GET_LOBBYLIST → ERR_MODE_BLOCKED** | 0x020f00b1 "Some of our online services are currently unavailable. Please refer to Online Information for further details." | entering Friendly Match Lobby (first lobby command) | on (…GetLobbyListFree 0x14284ff80) | a soft "this mode is closed" switch (an alternative to JOIN_ONLINE_MODE ERR_MODE_BLOCKED, whose own row is silent) |
| 6 | **CHECK_CHEAT → ERR_PARAMCHECK_FAILED** | 0x020f0015 "Invalid player data found." then the kind-0x16 TaskError "Unable to load content data…\nCODE:CCWR_0014" | every mode entry, right after GET_SERVER_ENV | on (CommandObjectCmdCheckCheat 0x14284d020) | whether both dialogs appear, and in which order (open in menus.md) |
| 7 | **START_MATCHING → ERR_COMMAND_BLOCKED** | 0x022600f6 "This feature is currently unavailable. \nPlease refer to Online Information for further details." | Quick Match start (one copy is enough; no opponent needed) | on (CommandObjectCmdStartMatching 0x142857d50) | a per-feature block for matchmaking |
| 8 | **LOGIN → ERR_INVALID_ACCOUNT** (or ERR_FREE_TO_PLAY_FULL for `{re}`) | 0x020f00a2 "…we have suspended your account…" / 0x01f5003f "Due to excessive traffic on the PES 2021 server, login is currently restricted…" | first online entry after the game starts | on (CommandObjectCmdLogin 0x142853a10) | whether the table dialog and the ConnectAccount kind dialog both show, and the order (open in messages.md) |

- Bonus, not an ERR reply: **GET_MANNER_WARN `warn_kind:"A_TO_B"`** (or DOWN_B / DOWN_C / STILL_C) gives the courtesy warnings 0x20100c0..c3. It is sent on every FREE mode entry, right after CHECK_CHEAT (livetests.md), so it is cheap too.

**Exact live test (one copy, no match needed).**
1. Add one server switch, e.g. `PESSERVER_FAIL_ONCE="CMD_X=ERR_Y"`. On the first CMD_X, reply `{msgid, rqid, result:"ERR_Y"}` with no other fields, then behave normally. [The flag name is a suggestion.]
2. Run rows 1, 6 and 8 from the eFootball menu.
3. Run rows 2–5 from Kick Off → Friendly Match Lobby.
4. Run row 7 from Quick Match.

Proof for each row:
- The server log shows `TX CMD_X result ERR_Y`.
- A screenshot shows the exact text above.
- For rows 1 and 2 the text must read "PES 2021". That settles `{re}` / `{rf}`, and also confirms the region key 2 statically derived above.
- If a row shows a generic "…CODE:XXXX_nnnn" text instead, then a screen-level handler overrode the table. Note the code. `data/server_errors.tsv` `code_if_generic` decodes it.
- Stop each test at the first dialog (minimal-live-tests rule).

---------------------------------------------------------------------------------------------------

## Q4. The match datagram on the wire: kps::lltcp / Clltcp, dlcp, UHP, session type

Static reading of PES2021.exe 1.01.00, checked against the existing 2026-11-05 capture
(`logs/netshim.log`, 772 peer datagrams, first 64 bytes of each). Dumps are in the working folder.
Tags: **[static]** (address given), **[log]** (seen in the capture), **[inferred]**, **[guess]**.

### Short answer

The "u32 seq | u16 flags | 0xff" header in wire.md is really **two outer layers added by
`onlinesystem::P2pTurnIoFailover`**. It is **not** the kps Clltcp header. The kps Clltcp header exists, but it
sits **inside** the dlcp encryption, so a capture cannot see it.

A UDP datagram between two players during a match is built like this (outermost first):

```
+-- P2pTurnIoFailover reliability header (clear) ----------------------------------------------+
| u32 seq (BE) | u8 flags | [if flags&0x40: u32 ack (BE) | u16 hold_ms (BE)]                      |  5 or 11 bytes
+-- P2pTurnIoFailover route bytes (clear) -----------------------------------------------------+
| u8 age_route0/100ms | u8 age_route1/100ms      (0xff = never / >= 25.5 s)                       |  2 bytes
+-- kps::dlcp record (RC4 with ciphertext feedback, restarted for every record) ---------------+
| hdr u8 (bit7 link flag, bit6 zlib, bits0-5 random) | csum u16 | payload                         |
|   payload = kps Clltcp packet: u32 seq | u8 flags | [u32 ack | u8 delay] | msp data             |
|   (if bit6: everything after hdr is zlib, so it starts 78 DA)                                    |
+----------------------------------------------------------------------------------------------+
```

The capture fits this exactly **[log]**:
- All 772 datagrams have the route bytes `00 ff`.
- The sequence number goes up by exactly 1 per datagram in each direction, from 0 to 385.
- 764 of 772 records pass the "zlib header under the RC4 feedback" test (next section).
- The only flag bytes seen are 0x80, 0x40 and 0xc0, and the one ack echoes the peer's 0x80 probe seq.

### (a) Layers, with addresses

#### Layer 1: the P2pTurnIoFailover reliability header (outermost, in clear)
- **Encoder:** 0x14125efb0 (thunk 0x14125efa0). **Decoder:** 0x14125ee20 (via 0x14125ed90). **Builder:** 0x14125c0b0.
  Sequence state is `[conn+0]`, incremented after each send at 0x14125c1ca **[static]**.
- **Layout [static 0x14125f03b–0x14125f0ba]:**
  - `+0` u32 seq, big-endian (htonl).
  - `+4` u8 flags:
    - **0x80** is set by 0x142035060 when the timer 0x14125da10 fires. It asks the peer to echo this seq.
      Our reading: an RTT probe **[inferred]**.
    - **0x40** means an ack follows (0x14125f140 sets it).
  - If 0x40 is set:
    - `+5` u32 ack, big-endian: a seq the peer sent with 0x80.
    - `+9` u16 hold time, big-endian: milliseconds between receiving the probe and sending this ack. It is
      computed in 0x14125de10 and capped at 0xffff.
  - Payload starts at offset 5, or 11 when 0x40 is set (constant 0xb at 0x1434d6628).
- There is no window field and no ack list. This layer gives RTT only, not retransmission **[inferred: only
  probes are acked; capture: 1 probe per direction in about 7 s]**.
- **Sequence counters are per route.** The direct send (0x141251b90) and the TURN send (0x1412531f0) both use
  the map at `+0x480` but with different keys (globals 0x1436fecf0 and 0x1436fecd0). Each route therefore has
  its own seq that starts at 0 **[static]**.
- **Capture proof [log]:**
  - Datagram `000000e7 80 00ff…` from one side.
  - The other side's next datagram is `000000ed c0 000000e7 0011 00ff…`: seq 0xed, flags 0xc0, ack 0xe7,
    hold 17 ms.
  - wire.md's "u16 flags 0x8000/0x4000/0xc000" was this u8 flags byte followed by the first route byte (0x00).
- **Which class is used:** this header exists only in P2pTurnIoFailover. That class is the vtable at
  0x1428b9a90; slot 7 = send 0x141252740. The factory is 0x14124ac50 with type 3; net.md shows the
  WithTurn strategy creates type 3.
  - With an empty TURN list the strategy is plain P2pFullMesh (NtlNetworkIo only). Then **there is no
    Failover header at all**, and the datagram starts directly with the dlcp record **[static 0x1411aa730 via
    net.md; inferred]**.

#### Layer 2: the two route bytes
- **Encoder:** 0x14125a5e0. **Inputs:** 0x141251820, called from 0x141252740 **[static]**.
- `byte0 = min(t_B / 100, 255)` and `byte1 = min(t_A / 100, 255)`:
  - t_A is the milliseconds since the last receive on the route tracked at Failover `+0x430/+0x438`.
  - t_B is the same for the route at `+0x440/+0x448`.
  - "Never received" uses 25500 ms (0x14125a5b0 returns 0x639c), which encodes as **0xff**.
- So `00 ff` means: route B was heard from less than 100 ms ago, and route A never.
  - On the LAN test that is "direct P2P alive, TURN never used". So B = direct and A = TURN
    **[inferred from the capture]**.
  - The peer uses these bytes for its failover decision. nettune.md gives the 7.5 s threshold
    = byte value 0x4b **[inferred]**.

#### Layer 3: the kps::dlcp record (encrypted)
- **Record builder:** 0x142052f20. The datagram is `hdr | csum16 | payload`:
  - `hdr = (link_flag<<7) | (xorshift32(clock) & 0x3f)`.
  - `csum16` is RFC 1071 over the whole record with the csum field zeroed (0x142052e90).
  - If `compress2(level 9)` is shorter: `hdr|0x40` followed by zlib(csum|payload).
- **Cipher:** `kps::dlcp::detail::ArcfourEncryptor`, vtable 0x142bca508. Encrypt is 0x14204c530 → 0x14204c5d0
  **[static]**:
  - `c[i] = RC4KS[i] XOR p[i] XOR c[i-1]`, with `c[-1] = 0xEA` (the constructor writes 0xeaea at +9).
  - **Before every record**, vtable slot 1 (0x14204c8d0 → 0x14204c840) restores the RC4 state saved after the
    key schedule and resets i = j = 0. So **every record reuses the same keystream**.
  - Both directions share the key, so their keystream is identical too **[log: same constants in both
    directions]**.
- **Capture test [log]:** take `d[n] = c[n] XOR c[n-1]`, which equals `p[n] XOR KS[n]`.
  - d[1] = 0x01 and d[2] = 0x8a in 764/772 records, in **both** directions. That is `78 DA` (zlib level 9)
    XOR a fixed keystream: KS1 = 0x79, KS2 = 0x50.
  - The top two bits of d[0] are `10` for all zlib records and `11` for the uncompressed ones. Only bit 6
    (the zlib flag) differs, exactly as predicted.
- **Send order inside kps [static]:** `LlDomain::Send` 0x142001480 does three steps:
  1. Gets the msp data (0x142027060).
  2. Wraps it in a Clltcp packet (0x14201e100 → 0x14201cde0 → encoder 0x142034ea0).
  3. Runs dlcp on that (0x142029940 → LinkController 0x14203c550 → state slot 3; Secure = 0x14204c050).

  Receive is the reverse: 0x1420012a0 calls dlcp decode 0x142029780, then the Clltcp parse 0x14201e060, then
  0x1420268a0. Send needs dlcp state "Secure", code 1 (0x142029ca0 / 0x14203c630; Secure's id = 3).

#### Layer 4: the kps Clltcp header (inside the encryption)
- **Encoder:** 0x142034ea0. **Decoder:** 0x142034d60 **[static]**. This is the same shape as layer 1, but the
  hold time is **u8**:
  - u32 seq, big-endian. Per connection, incremented per packet at 0x14201cefa.
  - u8 flags: 0x80 = probe (0x14125da10), 0x40 = ack present.
  - If 0x40 is set: u32 ack and u8 delay. The delay comes from 0x142033f50 and is capped at 0xff.
  - Then the msp payload, from offset 5 or 10 (constants 0x14352af38 = 5 and 0x14352af40 = 0xa).
- The "LatestSentAck / LatestReceivedSequenceNumber" counters (ClltcpInfo RTTI) belong to this layer. There is
  no window field on the wire. The window/pacing in kps::lltcp (FastTcp, ScalableTcp, Bps/PpsPacing) is
  sender-side only **[static: no field in 0x142034ea0]**.
- Bundle encoders near dlcp may be the msp or key-exchange bodies: 0x14204f0a0 and 0x14204dfd0 / 0x14204eb00.
  Their roles were not settled **[guess]**:
  - 0x14204f0a0: `[u8 0x80|0x40][u8 n-1][u16 len-1, data]…`.
  - 0x14204dfd0 / 0x14204eb00: `[u8 n-2][u32 (+u16)]…[data]`.

#### The 7-byte / 259-byte first exchange
- The first datagram on a link is 14 bytes = 5 (Failover header, seq 0) + 2 (route) + **7-byte dlcp record**.
  The reply is 266 = 5 + 2 + **259** **[log]**.
- The 7-byte record = hdr + csum + a **4-byte token**. That fits KeyExchangeActive's encode 0x14204a330:
  - It builds its own message (0x14204a830) and refuses any caller payload (the input length must be 0).
  - It encrypts with an ArcfourEncryptor made from **the link's configured key** (LinkController `+0x20..+0x28`,
    cipher type `+0x18` = 0; 0x14203c360) **[static]**.
- The 259-byte reply = hdr + csum + 256 bytes from KeyExchangePassive (0x14204b2d0). Its inner format (a
  bundle?) was not decoded **[inferred]**.
- **The static key `a977d392…` (0x142bcaf10) does not decrypt the 7-byte record.** I tried it with and without
  feedback, and reversed; the checksum failed every time **[static + log test]**.
  - That key is only used inside Passive state 0 (0x142053880) on the token itself.
  - So the outer KX record uses the link's configured key, whose source I did not trace. To decrypt match
    traffic from a capture you would need it (and then the session key K) **[inferred]**.

### (b) UHP_* hole-punching messages

- **Type encoding [static 0x1418dd15e]:** the STUN builder 0x1418dd0a0 writes `type = class | method`, a plain
  OR in big-endian. There is **no RFC 5389 bit interleaving**:
  - Class values: 0x000 request, 0x100 success response, 0x010 indication, 0x110 error.
  - For responses it copies the request's transaction id (`bt edx,8` at 0x1418dd17c).
  - So UHP_CONNECT is **0x080b** (request) / **0x090b** (success), KEEPALIVE 0x080c/0x090c, FINALIZE
    0x080a/0x090a. RFC interleaving would have given 0x200b.
  - Standard methods (≤ 0xF) come out the same either way.
  - The name lookup 0x1418e07a0 takes the class-stripped method **[log matches]**.
- **Order [log]:** CONNECT → KEEPALIVE (256-byte padded probe, then 64-byte keepalives) → FINALIZE. Each request
  is answered by its 0x09xx response.
- **Attributes [log; field meanings inferred]:**

  | Attr | Len | Bytes seen | Meaning |
  |---|---|---|---|
  | 0x9090 RP_P2P_HEADER | 24 | `02 00 00 01 · 00000000 · 'id' 00 00 00 · 00…` | byte0 = context: 0x02 peer UHP, 0x10 in server keepalives (wire.md); `00 00 01` constant; +8 = the sender's own player-id char ('1' / '2') then zero padding [guess: a 16-byte NUL-padded id]. The very first CONNECT success carried 0 there [log]. |
  | 0x9091 RP_RELIABILITY | 12 | `00 01 01 04 · 00000000 · u32 serial` | serial +1 per keepalive per sender (0x0e, 0x0f, 0x10… and 0x49, 0x4a… in parallel) |
  | 0x909c RP_RELIABILITY_ECHO | 12 | byte-identical copy of the request's 0x9091 | the responder echoes it (RTT / loss accounting) |
  | 0x909d UHP_FINALIZE_RET | 4 | `00 04 00 00` | finalize result [guess: u16 code 4 + u16 0] |
  | 0x0026 PADDING | 188 | zeros | pads the first KEEPALIVE to exactly 256 bytes (path-MTU / size probe) [inferred] |

- The ntl request builders all pass 0x9091 / 0x9090 / 0x9999 TERM through one tagged-varargs call into
  0x1418dd0a0. Examples: 0x1418d2040, 0x1418d3510, 0x1418d7ad0, and the attribute parser 0x1418db100. A full
  field map would need these varargs decoded (not done).

### (c) How the session type (cfg+0x18) is chosen

- `CreateMultiplaySession` 0x1411aa730 reads `cfg+0x18`. This is word 0 of the setup config: 0x1411fca90
  copies `[cfg]` to `+0x18` **[static]**.
- For a match, that config is `TaskMatchSession+0xa8`. It is written in **SetConfig 0x141144d80** from
  `matchinfo+0x58` (0x140ff0a20). That is the mode word that CMD_GET_GAMERELAY's `game_sycom` sets
  (0x141092240, net.md) **[static 0x141145121–0x1411454d0]**:

  | matchinfo mode | from | session type written | other data |
  |---|---|---|---|
  | 1 | `game_sycom "P2P"` | **1 = P2P full mesh** | peer list (vector at +0x60) |
  | 2 | `game_sycom "SC"` | **0 = CsClient** (game relay) | relay address string at +0x10 |
  | 3 | set on the viewer path (not by GET_GAMERELAY) | **3 = Observe** | watching id string at `+0x78` (default `"0"`, 0x142877f04) — this is the `<id>` in the spectate URL |
  | 0 / other (`""`) | — | `+0xa8` left unchanged (P2P worked in logs with "") [log via net.md] | |

- After that:
  - The task state branch tests `[task+0xa8]==0`: CS goes to state 0xc, otherwise 0xe (0x141143b94).
  - `upload` (0x140ff7cd0) is checked only when the type is not 3 (0x141145512).
- The P2P-vs-TURN strategy within type 1 depends only on the TURN list and the debug var `turn_mode`
  (net.md). So **a server picks CS vs P2P only through `game_sycom`**. Keep "P2P".

### (d) How to decode a NETSHIM_HEX capture byte by byte

Classify every UDP payload first:
1. **STUN / ntl:** bytes 4..7 = `21 12 A4 42`.
   - Type = u16 at 0. Low 12 bits without 0x110 = method; 0x100 = success, 0x110 = error.
   - Toward port 3478 (STUN server): BINDING 0x0001 keepalives with 0x9090 / 0x9091.
   - Toward a peer: UHP 0x080a–0x080c / 0x090a–0x090c, attributes per the table above.
2. **DTLS** (if the TURN leg uses it): byte0 ∈ {0x14, 0x15, 0x16, 0x17} and bytes 1..2 = `FE FD`.
3. **TURN ChannelData** (plain UDP TURN): byte0 in 0x40..0x7F, then u16 length. Not seen so far. The client's
   TURN is TLS/DTLS per net.md and livetests.md.
4. **Anything else sent to the peer's ntl port is a match datagram.** Decode it:
   - `b[0:4]` = Failover seq, big-endian. It must be +1 per datagram per destination per route, starting at 0
     on a new link.
   - `b[4]` = flags, only 0x00 / 0x40 / 0x80 / 0xc0.
     - With 0x80, expect the peer's next datagram to carry 0x40 (or 0xc0) with `b[5:9]` = that seq and
       `b[9:11]` = a small hold time in ms.
   - Then 2 route bytes. `00 ff` on a direct-only link. A small byte1 means the TURN route is alive. byte0
     rising toward 0x4b means the direct path is going stale (failover at 7.5 s).
   - The rest is the dlcp record. Compute `d0 = c0 ^ 0xEA`, `d1 = c1 ^ c0`, `d2 = c2 ^ c1`:
     - `d1 == 0x01 && d2 == 0x8A` means a zlib-compressed record (plaintext 78 DA).
     - `d0 >> 6` is constant per record type: `10` for zlib, `11` for raw on this session's key.
   - Length check: the first datagram on a new link is exactly 14 bytes (7-byte KX record); the reply is
     5 + 2 + 259.

Tiny decoder (for the main session's log, one hex string per line):
```python
def match_dgram(b):
    seq=int.from_bytes(b[:4],'big'); fl=b[4]; o=5; ack=hold=None
    if fl & 0x40: ack=int.from_bytes(b[5:9],'big'); hold=int.from_bytes(b[9:11],'big'); o=11
    r0,r1=b[o],b[o+1]; c=b[o+2:]
    z = len(c)>2 and (c[1]^c[0],c[2]^c[1])==(1,0x8a)
    return dict(seq=seq,probe=bool(fl&0x80),ack=ack,hold_ms=hold,route=(r0,r1),dlcp_len=len(c),zlib=z)
```

**What would refute this layout:**
- A datagram with `b[4]` outside {00, 40, 80, c0}.
- Route bytes that are not 2 bytes before the record. Test: d1/d2 = 01/8A must appear at offset 7, or 13 with
  0x40.
- A sequence that does not step by 1.
- Data with no Failover header while a TURN list was served. Without a TURN list the datagram should start
  directly with the dlcp record: no clear seq, and d1/d2 = 01/8A at offsets 1/2.

### Live tests that would settle what is left (each stops at the first proof)

1. **No-TURN layout check.** Serve an empty TURN list (GET_TURN_SERVER_LIST) for one match start with
   NETSHIM_HEX=64.
   - Expected: peer datagrams start with the encrypted dlcp byte; `c1^c0 = 01` and `c2^c1 = 8A` at offsets 1/2.
     No incrementing seq.
   - That proves layers 1 and 2 belong to P2pTurnIoFailover. Stop once ~20 datagrams are logged.
2. **Route byte meaning.** With TURN served, block direct UDP between the copies for ~10 s after kickoff.
   - Expected: byte0 climbs 01, 02 … to about 0x4b. Then datagrams appear on the TURN path with their own seq
     starting at 0.
3. **Probe/ack.** In any capture, check that every `b[4]&0x80` seq X is echoed by the peer as `ack == X` within
   the next few datagrams. Already seen once **[log]**.
4. **Session type.** Reply `game_sycom "SC"` with a dummy relay address (one attempt).
   - Expected: the client goes to the CsClient path (task state 0xc) and sends to the relay ip:port instead
     of the peer.
   - Only worth doing if the CS relay is ever wanted. net.md advises against it.

### Still unknown
- The source of the dlcp link's configured key (LinkController `+0x20`). Without it the KX record and the
  session key K cannot be recovered from a capture, so the inner Clltcp/msp bytes stay opaque.
- The 256-byte Passive KX body format, and the token direction (wire.md's question).
- Exact 0x9090 / 0x9091 / 0x909d field names. The values are known from the capture; the meanings are guesses.
- What the 0x14204f0a0 and 0x14204dfd0 bundle encoders carry (msp acks vs KX).
- Note for Q2 (spectate): the viewer's URL id is `matchinfo+0x58` object `+0x78` (string, default "0"), used
  when the mode is 3 (0x1411454ea).
