# Hidden room lobbies (Free / Team Play / spectate): how the top menu is built and how to reach them

> **Confirmed live (2026-11-05):** Kick Off → scroll past Cup → **Friendly Match Lobby** is there (8th item). Entering it runs the Lobby flow described here; see livetests.md for what the game sent.


PES2021.exe 1.01.00, static only. Tags: [static] read at an address, [data] game files, [inferred], [guess].
Working files: the working folder (not published) (all 2158 flow JSONs from dt16_all.cpk under `flow/`, `use_all.json` =
US-English string dump from dt80, asm dumps `topmenu_build.asm`, `aaf280.asm`, `ab3590.asm`, `aefc20.asm`,
`pesl_join.asm`, `peslg_list.asm`).

## TL;DR
1. **The top menu is a hard-coded C++ table, not data.** Friendly Match Lobby (`freelobby`) and Team Play Lobby
   (`multilobby`) **are in it, as children of Kick Off**, right after Cup and before Training. No code path
   filters them out: no server flag, no platform/SKU check, no date check. [static]
2. The sub-menu is a **scrolling list**: visible rows = min(item count, AFP slot count), and the game plays
   `scroll_on` when there are more items than slots. Kick Off has **10** items. Live tests saw 7 (Local Match …
   Cup), and Training was missing too. So the three missing items are most likely **just off-screen: press Down
   past "Cup"**. Expected order: 8 Friendly Match Lobby, 9 Team Play Lobby, 10 Training.
   [static 0x140e420fb-0x140e421a9; the 7-slot count is inferred]
3. Choosing Friendly Match Lobby sets game mode 0x1a ONLINE_LOBBY and posts event `freelobby`. The chain is
   TopMenuNew `12_freelobby` → TopMenuModeInit `1_mode_online` → LoginFlow `01_lobby` →
   `Online/Lobby/LobbyProcessModeInit`. Nothing on that chain is gated. [static + data]
4. **Server-driven route:** a PES League competition whose `match_type` is `PES_LEAGUE_MATCH_1VS1_LOBBY` sends
   eFootball → Online Tournament → the competition → `04_join_lobby` → the same Lobby flow (mode
   PES_LEAGUE_LOBBY 0x3a). [static; that this value comes from GET_PESLEAGUE_COMPE_LIST is inferred]
5. **Spectating (`onlinewatch`) has no menu entry.** The string 0x1427103e8 is never referenced, and TopMenuNew's
   `14_onlinewatch` is dead data. The mode itself exists (0x1d ONLINE_WATCHMATCH; LoginFlow sends it to
   `14_test` → `Online/WatchMatch/WatchMatch`). [static + data]

## 1. How the top menu is built [static]
- **Static initializer 0x1401c7fb0** (registered in the CRT init table at 0x1425337c0) builds an
  initializer_list of **48 records of 0x48 bytes** and copies it into the global vector **0x1436f57a8**
  (constructor 0x140c55370; getter 0x140c553a0; atexit destructor 0x142518720). The item-id strings come from the
  pointer table 0x1434b6bf8.. (`""` 0x1434b6bf8, `exhibition` 0x1434b6c00, …, `freelobby` 0x1434b6c58,
  `multilobby` 0x1434b6c60, …).
- Record layout:

  | offset | field |
  |---|---|
  | +0 | id |
  | +4 | level (0 = root) |
  | +8 | parent id (-1 for a root) |
  | +0xc | title string id |
  | +0x10 | second string id or -1 (roots) |
  | +0x14 | kind: 1 = leaf that starts a mode, 2 = new/load/resume leaf, 3 = category, 4 = Exit |
  | +0x18 | game mode (index into the mode table 0x1434fc9c0) |
  | +0x20 | std::string name = flow event suffix |
  | +0x40 | sub-type (0, 1 = new, 2 = load) |

  The getter by id is 0x140c56910 (falls back to a dummy record at 0x1434b6d70 with id -1). "Children of X"
  (0x140c567b0) = every record whose +8 equals X, in table order.
- String ids: 0x3a2xxxx = group `TermpesC` (group 185 of use/string/all.str), entry xxxx. For example
  0x3a20121 = "Friendly Match Lobby", 0x3a20122 = "Team Play Lobby", 0x3a20123 = "Training", 0x3a20135 =
  "eFootball". [data `use_all.json`]
- Top categories are the 7 roots listed at 0x1427102f8 (read by 0x140c553b0): **0 Kick Off, 0x10 eFootball,
  0x18 Extras, 0x1e Settings, 0x25 myClub, 0x27 Master League, 0x2b Become a Legend.** The AFP has 8 slots,
  `c_topMenu_0..7` (0x140ab1360).
- **Kick Off children** (vector index : id name mode):
  - 1:1 exhibition 0x8
  - 2:0x2f exhibition (EURO) 0x8
  - 3:2 offline_coop 0xc
  - 4:3 randomselection 0xb
  - 5:4 versus 0xa
  - 6:5 League (category)
  - 10:9 Cup (category)
  - **14:0x14 freelobby 0x1a**
  - **15:0x15 multilobby 0x1b**
  - 16:0xd Training (category: tutorial 0x33, freeTraining 0x34)
- **eFootball children:** quick 0x19, rankmatch 0x18, online_coop 0x1c, compeonline 0x3b (Matchday),
  compeonline 0x36 (Online Tournament = PES_LEAGUE). All 5 match the live screen.
- The game mode table 0x1434fc9c0 has 16-byte records {category, 0, name ptr}. 0x1a ONLINE_LOBBY has category
  0xf, 0x1b ONLINE_MOM_LOBBY 0x10, 0x1d ONLINE_WATCHMATCH 0x12. The IsOnline check 0x14149f5e0 counts all three
  as online. (0x14296cbb0 is a separate string list: QUICK, DIVISIONS, LOBBY1VS1, MOMLOBBY, …)

## 2. Why the lobby entries aren't seen
- `menu::TopModeSelect` (vtable 0x14266ddd8). Its constructor 0x140aae010 calls 0x140aaf280, which, for each id
  0..0x2f that is a category, stores **all** of its children in the map at this+0xd0. The only check is
  "id != -1" (0x140c6d210).
- 0x140ab10c0 fills `p_subMenu` with `count = children.size()` and calls the list widget 0x140e41fe0.
- The only per-item check is 0x140ab0240 / 0x140c56950. It only greys out the resume items (ids 6, 0xa, 0x28,
  0x2c) and the EULA item 0x1d. It changes whether an item is enabled, not whether it is shown.
- Nothing else writes to the vector or the map: the only users of this+0xd0 are 0x140aae010/300/820,
  0x140aaf280/590, 0x140ab08f0, 0x140ab10c0 and 0x140ab13bd, and all of them only read.
- Searched for and **ruled out**: a SKU/region/date condition, a server field (none of GET_SERVER_ENV,
  JOIN_ONLINE_MODE, the URL list, livedata or DLC is consulted in these functions), a debug switch, and data
  files (TopMenuNew.json only routes events; the AFP only has the slots).
- **Conclusion:** Friendly Match Lobby, Team Play Lobby and Training are rows 8–10 of a scrolling list that
  shows about 7 rows. [static + inferred] **Live test: Kick Off → press Down past Cup.**

## 3. Every way into the Free lobby flow
1. **Top menu (main route).** Decide handler 0x140ab03f0, kind-1 branch at 0x140ab0610: sets
   [0x1436fb9c4] = 1, calls `SetMode(entry+0x18)` (0x14149a570 writes the mode manager's +0xf0), then posts the
   event `entry.name` via 0x140ab19c0. The flow engine matches the event by name suffix: `freelobby` →
   `12_freelobby`.
   - TopMenuModeInit's process 0x140aefc20: IsOnline → `mode_online` (0x140aefce2) → `Online/Login/LoginFlow`.
   - After login, 0x140febef0 switches on the mode category (jump table 0x140fec3dc): 0xf → `lobby`, 0x10 →
     `mom`, 0x12 → `test`, 0x1c → `pesleague` … → `01_lobby` → `Online/Lobby/LobbyProcessModeInit` →
     LobbyProcessInitLobbySelect → … (protocol in rooms.md). [static + data]
2. **PES League competition (server-driven).** eFootball → Online Tournament (mode 0x36) → LoginFlow
   `11_pesleague` → PesLg flows → `PesLgProcCompeJoin`. Its process 0x141371030 switches on the selected
   competition's +0x34 (0x141371524):
   - 0 → `join_1vs1`
   - 1 → `join_1vs1_random`
   - 2 → `join_coop`
   - **3 → `join_lobby`, mode 0x3a PES_LEAGUE_LOBBY, or 0x2a MYCLUB_PES_LEAGUE_LOBBY when 0x141018ca0 is true**
   - 4 → `join_coop_random`

   It also sends CMD_JOIN_PESLEAGUE_COMPE first (0x1413710ea).
   - The enum at 0x1437f39a0 (built by 0x14021cd50) is {0 `PES_LEAGUE_MATCH_1VS1`, 1 `…_1VS1_RANDOM`,
     2 `…_3VS3`, 3 `PES_LEAGUE_MATCH_1VS1_LOBBY`}.
   - The GET_PESLEAGUE_COMPE_LIST handler 0x14109a4e0 compares a reply string against these names
     (0x14109a666). That string is the per-competition `match_type` STR (parser 0x141bd6040) [inferred, strong].
   - So **our server can send a competition with `match_type:"PES_LEAGUE_MATCH_1VS1_LOBBY"`** and the game opens
     the room lobby. The lobby flows then run with lobby_type PESLEAGUE, so GET_LOBBYLIST etc. must answer for
     that type. [inferred]
3. **myClub friend match.** `Online/MyClub/ProcessCheckMatchSquadFriendMatch` `proceed` →
   LobbyProcessModeInit (myClub lobby, mode 0x23 MYCLUB_LOBBY). This needs the myClub flows to work. [data]
4. **Invitations do not lead into the lobby.** `InformationProcessInvitation` only goes to error menus or back to
   InformationSubMenu (a `TaskWatchAcceptInvitation` class exists). No flow edge from an invite reaches
   Online/Lobby. [data; static names only]
5. The other Lobby edges (UserList, ProcEnd `03_free_match`, GuestProcessExit, MatchMenuSubReturn) are returns
   into a lobby you are already in. [data]

## 4. Minimal change, if the scroll test fails or we want it more visible
- **No data edit can add a menu item:** the list is compiled in, and the AFP only supplies the slots.
- **No server field controls the top-menu list.**
- **Server option (needs no mod):** route 2 above, a PES League competition with match_type
  `PES_LEAGUE_MATCH_1VS1_LOBBY`. The exact list/info field set still has to be implemented (schemas:
  GET_PESLEAGUE_COMPE_LIST / _INFO / JOIN_PESLEAGUE_COMPE).
- **dinput8 runtime patch (tiny):** wait until `begin = *(u64*)0x1436f57a8` is non-null. The CRT static init runs
  after dinput8's DllMain, so use a delayed thread or a hook, and finish before the first TopModeSelect is
  built. The map is rebuilt in 0x140aaf280 each time that screen is constructed. Then:
  - Move Friendly Match Lobby to the top of eFootball: `*(u32*)(begin + 14*0x48 + 8) = 0x10` (parent 0 → 0x10).
    Children keep table order, so it appears before Quick Match. Optionally do the same for index 15
    (Team Play Lobby).
  - Check first that `*(u32*)(begin+14*0x48) == 0x14` and that the SSO string at +0x20 is "freelobby".

## 5. Team Play (MOM) lobby and spectating
- **Team Play Lobby:** it is in the same Kick Off list, as row 9 (vector index 15, mode 0x1b). Event
  `multilobby` → `13_multilobby` → login → `02_mom` → `Online/MOMLobby/MOMLobbyInit`. Same scroll explanation,
  same patch with index 15. [static + data]
- **Spectating (`14_onlinewatch`):** no table entry, so no menu route. Possible patch: rename record 15 to
  `onlinewatch` (11 chars, fits the SSO buffer: write text at +0x20 and size 11 at +0x30) and set +0x18 = 0x1d.
  Deciding it then gives mode 0x1d → `14_onlinewatch` → TopMenuModeInit (online) → LoginFlow `14_test` →
  `Online/WatchMatch/WatchMatch`. WatchMatch probably expects a match chosen beforehand (it is normally entered
  from `LobbyWatchingMatchList` or Matchday `MatchdayProcToWatchMatch`), so a standalone entry likely lands in
  WatchMatchEnd. [guess] The supported spectate entry is inside the lobby: `LobbyWatchingMatchList` →
  WatchMatch (see spectate.md).

## Still unknown
- The AFP slot count of `p_subMenu`, i.e. that exactly 7 rows show. A 10-second live test settles it: scroll Kick
  Off past Cup.
- The exact reply field that fills the PES League competition's +0x34. It is probably `match_type` in
  GET_PESLEAGUE_COMPE_LIST, possibly also in GET_PESLEAGUE_COMPE_INFO. Settle it by reading 0x14109a4e0 /
  0x14109cd10 to where the enum is stored, or by a live test with a fake competition.
- Whether WatchMatch works without a match chosen beforehand.
