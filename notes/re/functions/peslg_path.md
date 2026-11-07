# PES League lobby → "Watching list" → WatchMatch: the server reply chain (static)

> **Live (2026-11-06):** `online_mode`, `compe_mode`, `mode` and `lobby_type` are all "PESLEAGUE"; national-team ids in `team_list` work; COMPE_INFO needs a `compe_data` blob (see spectate2.md).

PES2021.exe 1.01.00. Tags: **[static]** read at the address, **[inferred]**, **[guess]**.
Dumps: the working folder (`cd_decide.asm`, `cjoin.asm`, `getmm.asm`, `wlh.asm`, `wl_dlg.asm`, `gres.asm`, `wlparse.asm`, …).

## Key facts

- **The gate is set by the game mode, not by the server.** LobbyProcessModeInit (0x14139b2e0) creates the
  lobby context (0x14103d310 sets `[ctx+0x78]=1` and `ctx+8` = new 0x80-byte object). It then maps the mode to the
  lobby kind with 0x141118590(ctx+8, kind): 0x1a→1 (Free), 0x23→2 (myClub), **0x3a→3 (PES League)**,
  0x2a→4 (myClub PES League) (0x14139b340–0x14139b370). Gate 0x1410419e0 = `ctx+0x78 && kind∈{3,4}`. **[static]**
- **Mode 0x3a comes from PesLgProcCompeJoin.** When the competition's `+0x34 == 3` it posts `join_lobby` and calls
  SetMode(0x3a), or 0x2a when myClub (0x14137157b–0x141371614). **[static]**
- **`+0x34` comes from CMD_GET_PESLEAGUE_COMPE_INFO `match_type`.** It is not taken from COMPE_LIST. The handler
  0x14109a4e0 compares the reply string (result+0x218 = parser field 0x100 `match_type`, parser 0x141bc8450) with
  enum 0x1437f39a0 = {0 PES_LEAGUE_MATCH_1VS1, 1 …_1VS1_RANDOM, 2 …_3VS3, 3 **PES_LEAGUE_MATCH_1VS1_LOBBY**} and
  stores the index at compe+0x34 (0x14109a6f7). An unknown string gives 0 (1VS1). COMPE_LIST parses `match_type`
  into its own list record (0x14109cd10 → 0x141b9a0c0) for display. **Send the same string in both.** **[static]**
- The watch-list entry carries the `match_id`:
  - 0x14109b8b0 copies reply `match_list[i].match_id` to entry+0xd0 (0x14109bb2a).
  - "Watch" in the dialog (0x140be1164) calls 0x141050b30. That stores the index at `[[mgr]+0xa8]+0x2c` and copies
    the match_id string to **`[[mgr]+0xa8]+0x30`**, then posts `proceed` → WatchMatch.
  - The copy from there into the match-info `+0x78` (the URL id) was not seen. It is probably in the protected
    section (misc.md §2a). **[static / inferred]**

## Ordered table (one copy; JOIN_ONLINE_MODE / login is the same as for the other modes)

| # | flow node | client sends | minimal reply | event posted |
|---|---|---|---|---|
| 1 | Top menu eFootball → Online Tournament (mode 0x36) → LoginFlow `11_pesleague` | login chain + JOIN_ONLINE_MODE (online_mode "PESLEAGUE" [inferred]) | as for the other modes | `11_pesleague` |
| 2 | PesLgProcInit (0x14136a3b0) | **CMD_JOIN_PESLEAGUE_COMPE_MODE** {pid, compe_mode} | `{result:NOERR}` (parser 0x141c41a30 reads only result) | `proceed`; on error `return` [static] |
| 3 | PesLgProcGetMainMenuInfo (0x1413823a0) | **CMD_GET_PESLEAGUE_MAINMENU_INFO** {pid, mode} | `{result:NOERR, division:"…", new_result:"NO"}` | `mainmenu`. It posts `result` only when not myClub and user-info+0x34==1 (new_result YES) (0x141382546–0x141382564) [static/inferred] |
| 4 | PesLgProcMainMenu (0x14137bf10) | — | — | always `compe_list` if the manager exists [static] |
| 5 | PesLgCompeList (menu, 0x140daffb0) | **CMD_GET_PESLEAGUE_COMPE_LIST** {pid, mode, start_index, is_end, is_join_only} | `compe_list:[blob{compe_id N, compe_name S, is_myclub "NO", match_type "PES_LEAGUE_MATCH_1VS1_LOBBY", start_date N, end_date N, entry_limit_date N, match_end_date N, entry_user_limit "NO", entry_user_num 0, entry_user_max 100, entry_limit_division "NO", entry_limit_division_min "", entry_limit_division_max "", is_end "NO", banner_file_info blob{filename "",hash_str ""}, list_banner_file_info blob{…}, is_entry "NO", compe_db_id N}], list_num 1, list_max 1, is_no_check "NO", mode <echo>` (keys and order from parser 0x141bd6040) | selecting the item posts `compe_detail` (0x140dafe63/6c) [static] |
| 6 | PesLgCompeDetail (menu ctor 0x140da7650, init 0x140da8120) | **CMD_GET_PESLEAGUE_COMPE_INFO** {pid, compe_id} (0x140da81b4) | `{result:NOERR, compe_data/compe fields: compe_id, compe_name, is_myclub "NO", **match_type "PES_LEAGUE_MATCH_1VS1_LOBBY"**, start_date, end_date, entry_limit_date, **match_end_date (future)**, entry_user_limit "NO", entry_user_num, entry_user_max, entry_limit_division "NO", …min/max "", banner_file_info, is_exist_overview_info "NO", is_exist_participation_right "NO", (next flag) "NO", compe_db_id, use_ball_id, is_e_sports "NO", …stadium flags "NO", **entry_compe "NO"**, team_id 0, clan_info {…}, compe_regulation {…}, **eula_url ""**}` (parser 0x141bc8450; meaning of each offset from handler 0x14109a4e0) | The join action stores its event at +0x140 (0x140da8120): **`eula_agree` if entry_compe≠YES and eula_url is non-empty; otherwise `compe_join` if myClub or type ∈{2,5}; otherwise `team_select`.** For our 1v1 lobby that is **`team_select`**. It is posted after the join dialog `peslg_compe_detail_join_dialog` (0x140da7e50), or automatically when [this+0x90]==0 or `[[mgr]+0x78]+0x174` is set (0x140da8d3c path) [static] |
| 7 | PesLgCompeTeamSelect (0x140da97c0 / 0x140da9ac0) | **CMD_GET_PESLEAGUE_COMPE_TEAM_LIST** {pid, compe_id} | `{result:NOERR, team_list:[<team id NUM>, …], team_list_num:n}` (parser 0x141bdf3b0: array of NUM) | `proceed` → PesLgProcCompeJoin [data; menu logic not read] |
| 8 | PesLgProcCompeJoin (setup 0x141370f60, update 0x141371030) | (a) **CMD_JOIN_PESLEAGUE_COMPE** {pid, compe_id, team_id}, only if compe+0x160==0; (b) CMD_GET_PESLEAGUE_COMPE_TEAM_LIST again, only if not myClub and the team vector is empty; (c) lottery list only for types 1/5; (d) **CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO** {pid, compe_id}, always (0x1413713b0) | (a) `{result:NOERR}`; (d) `{result:NOERR, matching_first_date, matching_time, matching_interval, compe_end_time, team_id, team_name, point, win_count, lose_count, draw_count, match_num, regulation_match_num, rank_type}` (parser 0x141c36530; values not checked here) | **`join_lobby`**, SetMode(0x3a) (0x141371588/0x141371614). Any command error → state 4 → `return` to the list [static] |
| 9 | Lobby flow (same JSON chain as the Friendly lobby): ModeInit → InitLobbySelect → CmdGetLobbyList → ServerSel → JoinLobby → CmdGetLobbyInfo → FinishLobbySelect → InitBlockSelect → CmdGetBlockList → LobbySel → JoinBlock → InitRoomList | GET_LOBBYLIST {lobby_type **"PESLEAGUE"** [inferred from enum FREE/MULTI/MYCLUB/PESLEAGUE + kind 3]}, JOIN_LOBBY, GET_LOBBYINFO, GET_BLOCKLIST, JOIN_BLOCK, REQWATCHL_ROOMLIST | **the same replies as testserver/lobbytest.py**; an empty room list is fine | → LobbyRoomList. ModeInit sets kind 3, so the gate is true [static] |
| 10 | LobbyRoomList, button handler slot 51 (0x140bdc330) | — | — | gate true → `watching_match` (0x140bdc34f); gate false → default 0x140ced7d0. Which pad button this is was not read [guess: shown in the help bar] |
| 11 | LobbyWatchingMatchList (menu::OnlineWatchMatchListPesLeagueLobby; task 0x141050fa0, request filler 0x14109b7a0) | **CMD_GET_WATCHING_MATCH_LIST** {pid, match_category "PESLEAGUE_LOBBY", compe_id, index} | see "List reply" below | Empty list → `DialogNoMatchData` (0x140be1000). Selecting an entry opens SelectDialogWatchingListPesLeagueLobby (0x140be2390). Option 0 → `proceed` → WatchMatch; option 1 → `userlist` (0x140be1164–0x140be11aa) [static] |
| 12 | WatchMatch (process::OnlineTestMatchWatch, mode 3) | GET_SERVER_ENV, then HTTP `<WATCHING_GAME>/<id>/<N>.enc` (misc.md §2f) | see spectate.md | — |

## List reply (GET_WATCHING_MATCH_LIST): what the client needs per entry

Parser 0x141bd11e0, key order. The player loop closes at 0x141bd2b86, so **`team` and `score` are at the game_result
level, not per player**:

```
match_list: [ blob{
  match_id   STR   -> entry+0xd0, later the selected id
  game_phase STR   enum 0x1437f2420 {DATASYNC,TEAMSELECT,MATCHMENU,GAMELOAD,ENTRY,1ST,…,PK}
  game_result blob{
     date N, ex_type S, pk_flag S, no_match S,
     player: [ blob{pid, pname blob{name, xuid_one, psn_user_id blob{account_id, online_id, is_valid_cache, is_deleted}},
                    side "HOME"|"AWAY" (enum 0x1437f3210), is_com, cpu_level, is_follow, is_guest} , … ],
     team:  [home_team_id N, away_team_id N],
     score: [ blob{score_1st,score_2nd,score_ex1st,score_ex2nd,score_pk}  (HOME),  blob{…} (AWAY) ],
     match_category "PESLEAGUE_LOBBY",  gameend_reason "NORMAL_END" } } ],
match_list_num: n, total_match_num: n
```

- The entry builder 0x141058a30 drops the entry (returns 0) if a summed score or the PK score is negative
  (0x1410595a3). It also drops it if gameend_reason's index is above 0x12 (unless a flag is set). **[static]**
- The builder reads `team[0..1]` and `score[0..1]` through raw pointers, with no size check (0x14105944f–0x14105949d).
  **Always send two of each.** Missing ones probably crash the game. **[inferred]**
- Players are sorted into HOME and AWAY lists by `side`. An empty side is skipped, not fatal (0x1410593c1). **[static]**
- When the gate holds, the handler (0x14109c156) also copies the reply's paging total (result+0x1e4) into the
  watch data. **[static]**

## Values that must be right (everything else can be a placeholder)

1. COMPE_INFO `match_type` = `PES_LEAGUE_MATCH_1VS1_LOBBY` (gives join_lobby and mode 0x3a).
2. COMPE_INFO `eula_url` = "" and `entry_compe` = "NO". Then the action is team_select, followed by
   ProcCompeJoin, which sends JOIN_PESLEAGUE_COMPE.
3. `entry_user_limit` "NO" and `entry_limit_division` "NO". Otherwise the "full" flag (+0x170: num ≥ max) and the
   division-range flag (+0x171) may block the join. Where those two flags are read was not found. **[inferred]**
4. **`match_end_date` in the future, in unix seconds.** ProcCompeJoin hands compe+0x44 (= match_end_date) to the
   TaskPesLgWatching deadline (0x14137162f → 0x140a74000). TaskPesLgWatching (0x1421a71c0) compares it with the
   server clock 0x14115fe20 and fires error OBMB107 once that is past. **[static; unit inferred]**
5. Every PES League command returns `result:"NOERR"`. A command error makes ProcCompeJoin go to state 4 → `return`.

## Not settled statically → live checks

- **Which button opens the join dialog in CompeDetail, and which opens the watching list in LobbyRoomList (slot 51).**
  Live: read the help bar on each screen.
- **The JOIN_ONLINE_MODE `online_mode` and JOIN_PESLEAGUE_COMPE_MODE `compe_mode` values.** Live: log the requests.
- **The GET_LOBBYLIST `lobby_type` string for kind 3.** Expected "PESLEAGUE". Live: log it.
- **Whether TeamSelect shows the team_list ids as selectable teams.** Live: send a few real national-team ids
  (pesdb.py). The screen should list them, and choosing one should give JOIN_PESLEAGUE_COMPE {team_id}.
- **The step from `[[mgr]+0xa8]+0x30` (the selected match_id) to the HTTP id.** Live: the first `.enc` GET path
  should carry the `match_id` sent in the list.
