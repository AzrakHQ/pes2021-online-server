# Sweep group: settling the earlier groups' "Still unknown" items (static)

Scope: the priority items from results / match / rooms / coop / menus / live / db. Each item has an
answer, a tag ([static] read in code, [log] seen in our traffic, [inferred], [guess]) and addresses, or
**needs live test: <test>**. Work files: the working folder (not published) (asm dumps, `scanoff.py`, `dt20/all.dec`).

Tooling notes for later groups:
- the working folder (not published) <disp>` finds every instruction with memory displacement `disp`. It decodes linearly from the
  .pdata chunk start, so it does not mis-align. The older "decode a few bytes back" trick returns wrong
  instructions (e.g. `test [rdi+0x880]` instead of `mov [rdi+rcx*4+0x880]`).
- Server-env fields are at `[[0x14114a910()]+0x10] + off`. 0x14114a910 returns OnlineSystem (global
  0x1436fe468), not the env object.
- The English UI text is `Data/dt20_eng_x64.cpk` → `eng/string/all.str` (WESYS+zlib, 1.34 MB decoded, ASCII/UTF-8).
  It has every edit-mode, skill and tactics label. Decoded copy: the working folder (not published).

---------------------------------------------------------------------------------------------------
## RESULTS

### `division` string values — SETTLED
- Enum 0x141b99db0 → table 0x1437f3700, initializer 0x140218760..0x14021892c [static]:
  `0 "NONE", 1 "D12", 2 "D11", 3 "D10", 4 "D9", 5 "D8", 6 "D7", 7 "D6", 8 "D5", 9 "D4", 10 "D3", 11 "D2", 12 "D1"`.
  (Index 0 is "NONE": the D12 string load at 0x140218874 is stored at 0x1437f3708, and "NONE" goes to +0.)
- So the strings are `"D1"` (top) to `"D12"` (bottom) and `"NONE"`. The icon picker maps index 1→division_12 …
  12→division_1, which is consistent.
- GET_SERVER_ENV `max_division` (default "D12") is stored as an enum index at env+0x380. It is read by 0x141029610,
  called from the divisions menu (0x140da43e0 / 0x140da4630 / 0x140da62e0) [static]. Meaning [inferred]: the
  lowest/number of divisions shown. Send "D12".
- **Server:** division fields = `"D1".."D12"`; new players start in "D12" [inferred].

### Ranking `sort` / `order` values — SETTLED
- The request builders for all four ranking command objects are 0x1410ef220 (CmdGetRanking), 0x1410efc40 (ById),
  0x141105f20 and 0x1411069a0 (the MyClub variants, same code). Option fields: +0x1c8 start_index,
  +0x1cc view_num = **100** (hard-coded), +0x1d0 sort, +0x1f0 order [static 0x1410ef47d..0x1410ef4cd].
- `sort` = enum 0x141b9a090 (table 0x1437f23e8): `RATING, WIN_NUM, MATCH_NUM, WIN_STRAIGHT, WIN_RATE`. It is chosen
  from the UI tab number: tab 0→MATCH_NUM, 1→WIN_NUM, 4→WIN_STRAIGHT, 5→RATING, 6→WIN_RATE. For tabs 2 and 3 no
  `sort` key is sent (the form serializer 0x141d42150 skips empty strings) [static 0x1410ef360..0x1410ef484].
- `order` = enum 0x141b9a0a0 (table 0x1437f2788, init 0x14021ca80): `0 "DESC", 1 "ASC"`. The game **always sends
  index 1 = "ASC"** [static 0x1410ef49c]. "ASC" therefore probably means "rank 1 first", not "lowest value first".
  **Server:** ignore `order`. Sort by the `sort` column, best first.

### GET_GAMERESULTS request keys — SETTLED
- Packer 0x141c771f0 (two chunks) [static]: `pid` NUM (Option+0xd0), `game_res_type` STR (+0xd8),
  `pesleague_compe_id` NUM (+0xf8).
- `game_res_type` = enum 0x141b99df0 (table 0x1437f2668). It is set per command-object variant in vtable slot 7
  [static 0x1410c19f0 / 0x1410c1cb0 / 0x1410c1b30 / 0x1410c1a90 / 0x1410c1bd0]:

  | Variant | game_res_type |
  |---|---|
  | plain | "ALL" (also the ctor default) |
  | RankMatch ("CmdGetDivisionGameresults") | "RANK" |
  | MyClub | "MYCLUB" |
  | Coop | "COOP_ALL" |
  | PesLg | "COOP_PESLEAGUE" if 0x14101f850() (co-op PES League), else "PESLEAGUE" |

  The PesLg variant also sets `pesleague_compe_id` from the PES League manager (`[...+0x120]` → +8).
  Other enum values (COOP_MYCLUB) exist but are not sent by these variants.
- **Server:** filter the history by `game_res_type`; return everything for "ALL".

### Rating screen: which modes show it; `rating_compe` — SETTLED (mode test) / PARTIAL (rating_compe)
- 0x14149f4f0 is **not runtime-filled**. It returns `table[mode].group`, where `mode = [x+0xf0]` (< 0x3d) and the
  table 0x1434fc9c0 is static in .data: 61 × {u32 group, u32 0, char* name} [static]. Relevant rows:

  | mode | name | group |
  |---|---|---|
  | 24 | ONLINE_RANK | 13 |
  | 25 | ONLINE_QUICK | 14 |
  | 26 | ONLINE_LOBBY | 15 |
  | 27 | ONLINE_MOM_LOBBY | 16 |
  | 28 | ONLINE_COOP_MATCH | 17 |
  | 29 | ONLINE_WATCHMATCH | 18 |
  | 30..44 | MYCLUB* | 19 |
  | 54..58 | PES_LEAGUE* | 28 |
  | 59..60 | MATCHDAY* | 29 |

  Full table: the working folder (not published) (index, group, name).
  The mode index is the same number that `play_data.gamemode` carries (25 in our Quick Match logs, 24 = Divisions).
- RatingPre 0x141379220 tests `bt 0x30082000, group` = groups {13, 19, 28, 29}: ONLINE_RANK, myClub, PES League and
  Matchday [static 0x141379262..0x141379278]. **Quick Match (group 14) never shows MenuRating; RatingPre returns
  "skip"** [static]. So in Quick Match the WATCH_GAMEADDPOINT content only matters for the M+0x18 bookkeeping, not for
  any screen.
- Gain formula (0x1413795d1..0x1413795dd, own and opponent) [static]: the screen struct gets `+0x3c = oldrating`,
  `+0x38 = (rating > rating_compe, unsigned) ? rating − rating_compe : rating`, +0x40 boost_type, +0x44
  boost_rate, +0x30 side, and +0x34 = W/L/D from 0x1413790e0 (winner_side vs own side; 1 win, 0 loss, 2 draw /
  unknown). menu::RatingChange (0x140bd9910) animates **oldrating → (rating − rating_compe)**. `rating_compe` is
  never shown by itself in that menu.
  - Meaning [inferred]: it is a competition/event bonus portion that is excluded from the displayed result.
  - **Server:** send `rating_compe: 0`. Boost-rate units are not settled; they are irrelevant for Quick Match.

### Units of `gtimer` — SETTLED from logs (unit) / not traced to the engine
- `gtimer` = `[match+0x1198]` (a u32 tick counter reset at match init, 0x140a3bac6 / 0x140a3c59d). It is copied
  into each queued CHANGE_GAMEPHASE / ADD_* entry (0x14216860c, entry +0x10 = gtimer, +0x14 = phase) [static].
  The incrementer was not located.
- [log] Values from five matches in logs/*.log:
  - 1ST is always 950.
  - The 15-game-minute mark arrives after 7 000–13 000 ticks, so the counter is **not game time**.
  - Against wall clock it runs at 46–51 ticks/s while the match is played. Example: 18:20:52 → 18:52:00 = 1868 s,
    93 157 ticks. During the half-time menu it runs at about 13/s.
  - Conclusion: one tick per engine update, about 20 ms (50 Hz). A 90-minute match ≈ 94 000.
  - **Server:** treat it as an opaque, monotonic per-match counter. Use it for de-duplication only (both clients
    send the same value).

---------------------------------------------------------------------------------------------------
## MATCH

### `[0x140ff0a20()+0x58]` values and TaskMatchSession `+0xa8` — SETTLED
- `[g+0x58]` is a "network mode" struct (ctor 0x141091ee0, reset 0x1410921d0 → type 0). Its type dword at +0 is set
  in three places [static]:
  1. **GET_GAMERELAY reply handler** 0x1410bb4c0 → 0x141092240. It compares `game_sycom` with enum 0x141b99e00
     {0 "P2P", 1 "SC"}: "P2P" → 1, "SC" → 2 (and keeps ip/port/index/grid), anything else → 0.
  2. **TaskQuickMatching** 0x14111a690: `if type != 2 → type = 1` (0x14111a6ea / 0x14111a7bb). It then fills the
     peer list at +0x60 from the matched members, skipping its own pid, and continues with 0x141092720.
     **So `game_sycom:""` (what we send) still ends as P2P (1).** This matches the logs, which show SEND_SYNC_NOTICE
     and TURN list requests.
  3. **Debug direct-IP connect** 0x141368db0 (only caller 0x141369410, a menu functor) sets type = **3** and reads the
     debug properties `"target_ip_address"` / `"target_port_num"` (0x141b34930 / 0x141b34850). It is a developer
     direct-connect path with no gate matching.
- SetConfig 0x141144d80 converts this to task+0xa8 [static 0x141145121..0x141145512]:

  | `[g+0x58]` type | task+0xa8 | What happens |
  |---|---|---|
  | 1 = P2P | 1 | uses the +0x60 peer list; ping check in state 22; no retry |
  | 2 = SC (game relay server/client) | 0 | parses "host:port" from struct +0x10 (split at ':'); NAT-prep states 12/13; retry ≤2 with a 15 s wait; CsClient strategy |
  | 3 = direct | 3 | uses string +0x78 (default "0"); GET_MATCHID skipped (task+0x8c); sets multiplay+0x20b |

  SYNC_NOTICE and the TURN list run only for type 1.
- **Server:** nothing to change. `game_sycom` "" or "P2P" both give P2P; never send "SC".

### Who sets multiplay+0x5c4 = 3 ("Revision error") — SETTLED: nobody
- Every write to +0x5c4 in the multiplay code is listed (scanoff, region 0x140ff0000–0x141200000) [static]:
  - ctor 0x140fff5c3 = 0;
  - 0x141003342 = 0 (check start);
  - 0x141000ec5 = verdict of 0x141001190;
  - 0x141000ee0 = 2 (timeout: elapsed > [mp+0x748]).
- The verdict function 0x141001190 returns only 0 (still waiting / peer data missing), 1 (ok) or **4 (DATAPACK
  mismatch)**.
- **Status 3 ("Revision error [..]") and 5 ("Other user disconnected") are dead branches in 1.01.** A revision
  mismatch shows up as 4 = MXNV533 "DATAPACK error".
- Also confirmed: `mp+0x5c0` (the DATAPACK compare switch) = 1, cleared when `env+0x38c` (GET_SERVER_ENV
  `is_revision_check == "YES"`) is 0 [static 0x140fff59e..0x140fff5bc]. This confirms the menus group's inference.
  **`is_revision_check:"NO"` disables the per-peer string compare.**

### Consumer of SYSTEM_NOTICE `time=` — SETTLED
- P2P connect step 0x1411d81d0 builds a key string `"<pid>-<pid>-…-<min(own_time, peer_time)>"`:
  - The member pids come from the session list (%d each, joined with '-').
  - The time is `min([obj+0x89c] own START time, [obj+0x8a0] peer time)` (0x1411d8586..0x1411d8592).
  - The string (≤ 64 bytes) is copied to a 64-byte buffer and passed to `[obj+0x8e8]->vtbl+0xc0(peer_id, …)` once
    per peer (0x1411d8858..0x1411d8889). That is the NTL connect call, so the string is the shared P2P session /
    connection tag.
  - Both fields are cleared afterwards (0x1411d86cb) [static].
- Consequence: both peers must compute the same string. **The server must relay each START notice's `time=`
  unchanged.** Rewriting or synthesising different times per side gives different tags → P2P connect failure
  [inferred from the code]. Relaying the peer's own text (what pesmatch does) is correct.

### Consumer of `trigger_keep_alive` (env+0x398) — SETTLED as "no consumer found"
- The copy is at 0x14121110b (Result+0x23c → env+0x398).
- No reader of `[env+0x398]` was found [static]. The search covered all 320 callers of 0x14114a910 (OnlineSystem →
  +0x10 → +0x398, within 14 instructions) and every dword read of +0x398 in the binary; the readers found
  (0x14105c79a, 0x141253b23, 0x1421b6387, …) belong to other objects.
- It is most likely unused in 1.01 (medium confidence: a far-away use through a saved env pointer cannot be ruled
  out). Keep sending 1.

---------------------------------------------------------------------------------------------------
## ROOMS

### `online_mode` (JOIN_ONLINE_MODE) — SETTLED
- Enum 0x141b9a080 (table 0x1437f2a80, init 0x14021c790): `0 QUICK, 1 RANKMATCH, 2 FREE, 3 MULTI, 4 COMPE, 5 MYPES,
  6 MYCLUB, 7 PESLEAGUE, 8 COOP, 9 EDIT_DATA_LIVE_UPDATE, 10 MATCHDAY` [static].
- JOIN_ONLINE_MODE's mapper 0x1410f2600 looks up {group, mode or −1, string} with group = 0x14149f4f0 (table above)
  [static]:

  | group (modes) | online_mode |
  |---|---|
  | 14 (ONLINE_QUICK, also TEST_ONLINE) | "QUICK" |
  | 13 (ONLINE_RANK) | "RANKMATCH" |
  | 15 (ONLINE_LOBBY) | "FREE" |
  | 16 (ONLINE_MOM_LOBBY) | "MULTI" |
  | 19 (myClub) | "MYCLUB" |
  | 28 (PES League) | "PESLEAGUE" |
  | 29 (Matchday) | "MATCHDAY" |
  | 17 (ONLINE_COOP_MATCH) | "COOP" |
  | 24 with mode 14 (EDIT_LIVEUPDATE) | "EDIT_DATA_LIVE_UPDATE" |
  | anything else | "" |

  COMPE and MYPES are never produced by this mapper.

### `lobby_type` (GET_LOBBYLIST) — SETTLED
- Enum 0x141b99eb0 (table 0x1437f28b0): `0 FREE, 1 MULTI, 2 MYCLUB, 3 PESLEAGUE`. The table continues with the
  REGION_* and info-type enums.
- Request filler 0x1410ed780 calls virtual `+0xd8` [static]:
  - CommandObjectCmdGetLobbyListForLobbyMulti 0x1410ed700 → "MULTI";
  - CommandObjectCmdGetLobbyListFree 0x1410ed720 → "PESLEAGUE" if the lobby sub-type ∈ {3, 4} (0x1410419e0),
    "MYCLUB" if it is 2 (0x141041970), else "FREE". The sub-type is `[[g+0x20]+8]+0x78`.
- `lang` = 0x141052850(). Keys and values are written into Option +0x1e8 (lobby_type) and +0x208 (lang).

### SET_GAMEENV `game_env` request shape — SETTLED
- Packer 0x141caf3c0 [static]:
  - The top-level map is **fixmap 5** (0x85): msgid, rqid, svrtype, client, `game_env`.
  - `game_env` is a **msgpack str/bin blob** (0x141151580 writes a str header + bytes) holding a packed **fixmap 9**
    (0x141156c10(9)): `cpuLevel` STR, `gametime` NUM, `injury` STR, `ball_type` NUM, `exGame` STR, `pkOnOff` STR,
    `substitution` NUM, `exSubstitution` STR, `limitTime` STR.
- It is the same blob convention as the DECIDE push, so the server can store the blob and echo it in
  WATCH_DECIDE_GAMEENV unchanged.

### `room_entry_no` / `game_entry_no` / `entryNum` — PARTIAL
- In REQWATCHL_ROOMPLAYERLIST_NTL (parser 0x141bb80f0), each player record (local rbp+0x5d0, pushed with
  0x141bb8090 into Result+0x218) holds: pid +0x4c, room_player_id +0x50, room_entry_no +0x54,
  game_entry_no +0x58 (all NUM) [static 0x141bb92d6..0x141bb941e].
- The consumers of +0x54/+0x58 were not traced.
- **Needs live test:** two clients in a Free lobby room. Send room_entry_no 1/2 and game_entry_no 0 → toggle Entry →
  game_entry_no 1/2, and check that the room list orders and labels players by these numbers.

---------------------------------------------------------------------------------------------------
## COOP

### WATCH/GET_COOP_ROOM_OPTION `room_phase` and countdown — room_phase SETTLED, countdown PARTIAL
- `room_phase` (STR at Result+0x470) is matched against the **game-phase enum** 0x141b99e40 (the CHANGE_GAMEPHASE
  table 0x1437f2420): `DATASYNC(0) TEAMSELECT MATCHMENU GAMELOAD ENTRY 1ST 1ST_15MIN 1ST_30MIN BREAK1 2ND 2ND_15MIN
  2ND_30MIN BREAK2 EX1ST BREAK3 EX2ND BREAK4 PK END(18)`.
  - The matching index goes to room data +0x22c; unknown → 0 [static 0x14113e56b..0x14113e66d; WATCH handler
    0x14113f… same code at r13+0x120].
  - So room_phase is the phase of the room's running co-op match. Send "DATASYNC" (or omit it) while the room is
    in the lobby.
- `is_game` YES → room+0x229.
- `is_start_count_down`=="YES" → room+0x210 = 1 and room+0x218 = `countdown_end_time` (u32 → qword).
- `is_stop_countdown`=="YES" → both cleared [static 0x14113e76d..0x14113e7fa].
- The countdown display consumer was not found → **needs live test:** push `is_start_count_down:"YES",
  countdown_end_time: now_unix+10`. Expect a 10 s countdown; if it shows a huge number, try milliseconds.

### GET_COOP_LEVEL_RESULT `status` — NOT SETTLED
- CommandObjectCmdGetCoopLevelResult has no OnResult (slot 9 = nop). The menu that reads the result was not
  identified. No enum getter compares `status` in the co-op result-menu functions (0x140d7e000–0x140d84000).
- **Needs live test:** after a co-op match, reply `status:"NONE"`, then `"UP"` / `"LEVEL_UP"`, and watch the result
  screen. Unchanged points are safe either way.

### Which `is_owner` the session host needs — NOT SETTLED (unchanged from the coop report)
- **Needs live test** (coop report §Still unknown 2).

---------------------------------------------------------------------------------------------------
## MENUS

### online_mode index 2 / announcement_type index 3 — SETTLED
- online_mode[2] = **"FREE"** (table above).
- announcement_type enum (table 0x1437f2af0, init 0x14021ed40): `0 PRE_MAIN, 1 USE_SCOUT, 2 USE_TOP_AGENT,
  3 USE_SPECIAL_AGENT, 4 CONTRACT, 5 RECOVERY, 6 STORE, 7 SUBSCRIPTION` [static].

### Information list: NO_BANNER_INFO listed? `date` unit; new title limit — PARTIAL
- Converter 0x1410c0980 [static]:
  - It maps announce_type via 0x141b99c10 {0 NO_BANNER_INFO, 1 BANNER_INFO, 2 WEEKLY_INFO, 3 COMPE_INFO,
    4 PERSONAL_INFO} into item+8.
  - It copies `date` → item+0, announce_id → +4, priority → +0x10.
  - **title > 180 bytes (strnlen 0xb4) is replaced by a single space.** New: keep titles ≤ 180 bytes UTF-8.
- Adder 0x141027130 [static]: type 4 (PERSONAL_INFO) → list at mgr+0x20; **every other type, including
  NO_BANNER_INFO → the general list at mgr+0x08**. Nothing filters NO_BANNER_INFO out, so it is stored like the
  other items.
- Rendering without an image and the `date` unit still **need a live test**: one NO_BANNER_INFO item with
  `date: now_unix`, then check the date shown in the Information list.

---------------------------------------------------------------------------------------------------
## LIVE

### Source of `[netmgr+0x68]` (server DLC version) — SETTLED
- netmgr = `[OnlineSystem+8]`. Its ctor/reset 0x1411c7570 sets defaults: +0x48 "1.00.00", +0x68 "0.00",
  +0x88 "0.00", +0xa8 "0.00".
- CommandObjectCmdGetSvrVersion (vtable 0x1428514c8, created by name "CmdGetSvrVersion" at 0x140fddfe2) has
  OnResult 0x1410f08c0, which copies [static]:

  | GET_SVRVERSION field | Result offset | netmgr offset |
  |---|---|---|
  | `version` | 0x1d0 | +0x28 |
  | `patch_version` | 0x1f0 | +0x48 |
  | **`dlc_version`** | 0x210 | **+0x68** |
  | `livedata_version` | 0x270 | +0xa8 |
  | eula versions | 0x230 / 0x250 / 0x290 / 0x2b0 / 0x2d0 | setters 0x1410f0f50 / 0x1411c7340 / 0x1410f0e00 / 0x1410f0ee0 / 0x1410f0e70 |

  Parser offsets come from 0x141d4af80 (`this` = Result+0x118).
- So `[netmgr+0x68]` **is GET_SVRVERSION `dlc_version`**, and the state-4 gate (SGJC392 when version_dp < it) is
  driven by our reply. Send "1.00" or omit it ("0.00").

### GET_SVRVERSION `livedata_version` consumer — SETTLED as "none"
- It lands in netmgr+0xa8.
- netmgr has only one version getter, 0x1411c79c0 (+0x68). Only two methods are reached through `[OnlineSystem+8]`
  (0x1411c7820, 0x1411c79c0), and no other code reads netmgr+0xa8 [static].
- Not used for any decision. Live-data freshness is decided only by the NOT_DELIVERED / version-table logic.

### version_wu.bin field +0x14 — SETTLED: stored, never read
- Reader 0x1414a0d00 stores, for entry e (stored at index e+1):
  - field +0x00 → string +0x60 / int +0x10;
  - field +0x0a → string +0x330 / int +0x2e0;
  - field +0x14 → string +0x8d0 / int +0x880 (for e = 18 = xu: +0x14 → +0x5b0, +0x1e → +0x880).
- The table has only three int getters: 0x1414a0b9a / 0x1414a0c00 (+0x10 content version), 0x1414a0bf0 (+0x2e0) and
  0x1414a0be0 (+0x5b0). The last two are called only with index 0x13 (= xu) at 0x1420ae019/26 and
  0x14040443c/49. There is no getter or other reader of +0x880 / +0x8d0 (byte-pattern and displacement scans)
  [static].
- So for version_wu.bin only the first field (content version) matters. Fields +0x0a and +0x14 are free text.

### ERR_DIFFERENT_LIVEDATA_VERSION handling — SETTLED
- The documented-error lists (data at 0x14289efc0.., 0x14289f848..) give it, together with
  ERR_DIFFERENT_EXTRADATA_VERSION and ERR_DIFFERENT_PATCHVERSION, to **CMD_JOIN_ONLINE_MODE and CMD_LOGIN**.
- Result enum (table 0x1437f3ee0, 189 entries, getter 0x141b9a2b0): ERR_DIFFERENT_LIVEDATA_VERSION = 138,
  PATCHVERSION = 38, EXTRADATA = 186.
- No code compares against these. The only code reference to the string is the enum initializer, and none of the 6
  getter call sites passes 138/38/186 [static].
- JOIN_ONLINE_MODE's handler 0x140fe5680 special-cases only "ERR_MODE_BLOCKED" (CCWR211). **Any ERR_DIFFERENT_*
  reply → the generic error path (CCWR216) and the online entry is aborted** [static].
- **Server:** for a version gate use ERR_MODE_BLOCKED, which has a dedicated message. ERR_DIFFERENT_* only gives a
  generic error.

---------------------------------------------------------------------------------------------------
## DB (labels from the game's own English string table `eng/string/all.str`)

### Long Range Curler vs Long Range Drive — SETTLED (and renamed)
- The UI-order table 0x142979100 is `[67,104,68,69,70,105,71,72,99,73,74,98,106,75,100,101,76,77,78,79,107,80,81,82,83,
  95,84,85,102,86,87,96,97,88,89,90,103,91,92,93,94]`. all.str contains the 41-entry skill help list in the same UI
  order (`<Scissors Feint>` … `<Fighting Spirit>`, around decoded offset 0x1c230). Zipping the two gives [static]:
  - **74 = "Long Range Drive"**, help text "Enables players to hit curling shots with great accuracy from a distance".
    This is PES 2020's *Long Range Curler*, renamed in 2021.
  - **106 = "Long Range Shooting"**, help text "A measure of how accurately the player can shoot at long range". This
    is the old *Long Range Drive*.
  - Also fixes the db report's names: **99 = "Step On Ball Control"** (was "Sole Control"), **105 = "Cross Over Turn"**
    (was "Chop Turn"), **101 = "Rising Shots"**. All other 38 names match the db report.
- The "Player Skills: X" list (offset 0x11447c) gives the original 28 skills in internal order 67..94. It agrees.

### Motion order — consistent, still [inferred]
- The Edit-player labels are present (Motion → Dribbling / Running, each with "Hunching" and "Arm Movement"; Corner
  Kick; Free Kick; Penalty Kick; Goal Celebration with "Full Celebration 1/2").
- The UI order 51,52,53,54,57,55,56,58 with ranges 0..4, 0..9, 0..4, 0..9, 0..9, 0..19, 0..6, 0..162 fits Dribble
  Hunching, Dribble Arm, Running Hunching, Running Arm, Corner Kick (10), Free Kick (20), Penalty (7), Celebration.
  The string table holds no per-field ordering, so this stays [inferred], but it is consistent.

### Set-piece role names — [inferred, strong]
- In-game UI order (all.str help, offset ~0x18882): Captain, Long FK Taker, Short FK Taker, FK Taker 2, Left CK
  Taker, Right CK Taker, PK Taker.
- With the db finding that team slots 0..5 (+0x5c8..0x5cd) test PA bits 19,17,17,18,16,20 (0x1414f06a0) and captain
  = +0x5d1, slots in UI order give:
  - bit 19 = Long FK;
  - bit 17 = Short FK and FK Taker 2 (shared);
  - bit 18 = Left CK;
  - bit 16 = Right CK;
  - bit 20 = PK.
- This assumes the slot order matches the UI order (not proven). It replaces the db report's guess "corners share
  bit 17".

### p43, p48, p49, p50 — NOT SETTLED
- They form UI group 0x142979048 = `[48, 49, 43, 50, 46(height), 47(weight)]`, so they sit on the page with Height
  and Weight.
- all.str has no "Stronger Hand", "Star" (as an edit label) or "Attitude" label. The labels are probably not shown in
  edit mode (hidden values).
- **Needs live test:** screenshot Edit → Player for a player whose p48 is 7 (Messi) and one with 0, or diff the
  edit-save after changing each field.

---------------------------------------------------------------------------------------------------
## What changes for our server (from this sweep)
1. Divisions use `"D1".."D12"` / `"NONE"`. Rankings: `sort` ∈ {RATING, WIN_NUM, MATCH_NUM, WIN_STRAIGHT, WIN_RATE}
   or absent; `order` is always "ASC" (ignore it); view_num is 100. GET_GAMERESULTS filters on
   `game_res_type` (ALL/RANK/MYCLUB/COOP_ALL/PESLEAGUE/COOP_PESLEAGUE).
2. Quick Match never shows the rating screen, so WATCH_GAMEADDPOINT is not needed for any visible screen in QM.
3. Relay SYSTEM_NOTICE text unchanged: `time=` feeds the shared P2P session tag (min of both times).
4. `game_sycom ""` = P2P (fine). A DATAPACK mismatch is MXNV533; `is_revision_check:"NO"` disables that check.
5. Version gate: use ERR_MODE_BLOCKED on JOIN_ONLINE_MODE. ERR_DIFFERENT_* only gives a generic CCWR216.
   GET_SVRVERSION `dlc_version` drives the SGJC392 gate; its `livedata_version` is ignored.
6. News titles must be ≤ 180 bytes.

## Still needs a live test (exact tests)
- room_entry_no / game_entry_no meaning (two clients, Free lobby, toggle Entry, watch the list).
- Coop countdown unit (push countdown_end_time = now+10 s).
- GET_COOP_LEVEL_RESULT `status` values; which `is_owner` the co-op host needs.
- NO_BANNER_INFO rendering and the `date` unit (one item, date = now).
- p43/p48/p49/p50 names (edit-mode screenshot).
- The gtimer incrementer (engine) was not located; the unit is from logs only.
