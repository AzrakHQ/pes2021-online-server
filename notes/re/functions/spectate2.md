# Spectating with two game copies on one PC: live test plan (static)

> **Confirmed live (2026-11-06, livetests.md):** recording, the PES League path and replay from storage all work as planned, with one correction: GET_PESLEAGUE_COMPE_INFO must also carry the competition fields inside a `compe_data` blob, otherwise the game opens the PES League 1v1 menu instead of the lobby. The watching-list button is X (Match List).

PES2021.exe 1.01.00. Static reading only, nothing was run. Tags: **[static]** read at the address given,
**[inferred]**, **[guess]**. This builds on spectate.md and misc.md §2. Corrections to those files are marked.
The PES League part was traced by a sub-agent; its full table is in `peslg_path.md`.
Dumps stay in the working folder (not published). The flow JSONs were extracted from `game/files/Data/dt16_all.cpk` into `flow/`.

## Answers in short

1. **Upload.** In a Friendly Match Lobby match or a Quick Match, reply to GET_MATCHID with a non-empty
   `match_id` and `upload:"YES"`. Reply NOERR to START_SEND_GAME_SEQUENCE_DATA and to SEND_GAME_SEQUENCE_DATA.
   **Both copies upload.** Keep the stream of one of them (the first to send START_SEND) and join its records.
   The server never rebuilds the records.
2. **Viewer entry: the PES League lobby, with server replies only. No game-file change is needed.** The watch
   button appears because of the game mode (0x3a). No server flag controls it, and the server can make the
   game reach that mode. The `onlinewatch` top-menu item cannot be unlocked by any server reply. Only a code
   patch can unlock it; the mod could apply one in memory at run time. Treat it as the fallback.
3. **A finished match can be watched from storage.** Nothing in the stream or the downloader checks for a live
   match. Two copies are enough for that: record a match first, then let one copy watch the recording.
   **Watching live needs a third copy**, because both copies are playing.
4. **Viewer traffic.** About 10 PES League commands, the usual lobby commands, then GET_WATCHING_MATCH_LIST and
   GET_SERVER_ENV. After that come plain HTTP GETs of `<WATCHING_GAME url>/<id>/1.enc`, `2.enc` and so on.
   At the end the game sends CMD_SEND_WATCHING_RESULT.

---

## 1. Upload side (players)

**When the match starts** (both copies, in the order the live Friendly-lobby test saw: GET_MATCHID, SYNC_NOTICE, GET_SERVER_ENV):

| client sends | server replies | notes |
|---|---|---|
| CMD_GET_MATCHID | `{result:NOERR, match_id:"<non-empty>", upload:"YES"}` | Both members get the **same** id. Today pesmatch.py:400 builds it from the room (`f"{t}{rid:06d}"`) and sends `upload:"NO"`. A Friendly-lobby room is put into `st["rooms"]` by lobbytest's SET_GAMEMEMBER, so the id is not empty. **An empty id stops the recording** (header writer 0x141256863). [static] |
| CMD_START_SEND_GAME_SEQUENCE_DATA {pid} | `{result:NOERR}` | No match_id in the request. Tie it to the room of the sender's pid. A command error aborts the match with **MXNV264**. [static, spectate.md] |
| CMD_SEND_GAME_SEQUENCE_DATA {pid, match_id, game_seq_data[blob…], game_seq_data_num} | `{result:NOERR}` | Sent from a worker thread about every 5 s, or when 16 KB of records have built up (0x1411cf120, 0x1411ced30). The reply is ignored. One message can be tens of KB, so it arrives as NCL 0x70/0x71/0x72 fragments; ncl.py `_read_fragments` already joins them. [static + repo] |

**Which copies upload.** Every copy whose GET_MATCHID reply says YES, so both. No code checks for the owner or
the host (0x140ff76e0, 0x141143733, 0x141145528). Each stream is that peer's own two-way view: what it sent plus
what it received. **Never merge two streams.** [static, misc.md §2c]

**How the server should store the stream** [static for the format, inferred for the design]:
- Per match_id, keep `{uploader_pid, buf, chunks, done}`. The uploader is the first pid that sent START_SEND
  for that room. SEND messages from the other pid are counted and then dropped.
- For each blob in `game_seq_data`:
  - Unpack the blob with msgpack.
  - Compute `raw = base64.b64decode(data)[:data_len]`. The `data` string is always 344 characters, because it
    encodes 256 bytes.
  - Append `raw` to `buf` in arrival order. `raw` is already a viewer item, for every kind:
    - kind 0 = the header;
    - kind 1 = the member list;
    - kind 2 = `[2][from][chan][seq BE32][len BE16][payload]`;
    - kind 3 = `03 00`.
  - Format details: misc.md §2b and §2d.
- **Cut a chunk** about every 5 s, or once 256 KB of `buf` has built up:
  - Plaintext = `b"\x00" + buf_since_last_cut`.
  - File `N.enc` = `IV(16) + AES-256-CBC(key, PKCS7(plaintext))`.
  - The key is `35a110675829e36845dc775162cca1bb69b786ed1e73d82ea9fe7e6176743881` (0x142897bd0).
  - The flag byte 0 means raw. Flag 1 means zlib, i.e. `zlib.compress`.
- **Size limits.**
  - The client reads each response into a **1 MiB** buffer (0x100000), so keep each `.enc` under 1 MiB.
  - The playback queue is a growing vector: 0x1411d0200 grows it by 0x10000 each time it is short. So a large
    chunk cannot overflow it. **[static; this corrects the "queue full" worry]** Downloading only pauses while
    more than 0xC000 bytes are waiting.
- **Close the stream** when one of these happens: a type-3 record arrives, CHANGE_GAMEPHASE END, LEAVE_GAME, or
  the uploader goes offline. The last chunk's plaintext then ends with:
  - `03 00`, but only if no type-3 record arrived; without it the viewer shows **PWTV340**;
  - then the 11 ASCII bytes `END_OF_DATA`.
- **Save it to disk** as `recordings/<match_id>/N.enc`, with a `meta.json`. The metadata holds the pids, names,
  sides and teams from the room, and the score from SEND_GAME_RESULT. GET_WATCHING_MATCH_LIST is built from it.
- **Logging.** The gate's `RX` line logs the whole request. Each SEND would put tens of KB into server.log every
  5 s, so the add-on should log only a summary:
  - `n` (the record count), the `type` values, `data_len`, `len(data)`;
  - the first 16 decoded bytes.

---

## 2. Viewer side: how a copy reaches the watch screen

There are only three flow edges into `Online/WatchMatch/WatchMatch` [data, flow/*.json]:
- `Lobby/LobbyWatchingMatchList` `proceed`;
- `MyClub/Matchday/MatchdayProcToWatchMatch` `0_match` / `7_match_coop`;
- `Login/LoginFlow` `14_test`.

### (a) PES League lobby — recommended, server replies only

This works because the gate depends only on the game mode:
- The gate is 0x1410419e0: lobby context `+0x78 != 0` **and** lobby kind ∈ {3, 4}.
- LobbyProcessModeInit (0x14139b2e0) sets that context. It writes `+0x78 = 1` at 0x14103d359 and maps the mode
  to the kind: 0x1a→1, 0x23→2, **0x3a→3**, 0x2a→4 (0x14139b340–0x14139b370). **[static]**
- **PesLgProcCompeJoin (0x141371030) sets mode 0x3a** and posts `join_lobby` when the chosen competition's
  `+0x34 == 3` (0x14137157b–0x141371614). **[static]**
- **`+0x34` comes from CMD_GET_PESLEAGUE_COMPE_INFO `match_type`, not from COMPE_LIST.** The handler 0x14109a4e0
  stores the enum index at 0x14109a6f7. This corrects lobby.md §3, which guessed COMPE_LIST. **[static]**

The minimal sequence for the viewer copy. Every reply carries `result:"NOERR"`, and every PES League command must
answer NOERR, because any command error goes back to the list. The full field lists are in `peslg_path.md`.

| # | screen / flow node | client sends | minimal reply | event |
|---|---|---|---|---|
| 1 | Top menu eFootball → **Online Tournament** (mode 0x36) → LoginFlow `11_pesleague` | the usual login + JOIN_ONLINE_MODE | as for the other modes (log `online_mode`) | |
| 2 | PesLgProcInit 0x14136a3b0 | CMD_JOIN_PESLEAGUE_COMPE_MODE {compe_mode} | NOERR | `proceed` |
| 3 | PesLgProcGetMainMenuInfo 0x1413823a0 | CMD_GET_PESLEAGUE_MAINMENU_INFO {mode} | `{division:"…", new_result:"NO"}` | `mainmenu` (YES would take the results branch) |
| 4 | PesLgProcMainMenu 0x14137bf10 | none | none | always `compe_list` |
| 5 | PesLgCompeList 0x140daffb0 | CMD_GET_PESLEAGUE_COMPE_LIST | one competition: `compe_id`, `compe_name`, `is_myclub:"NO"`, **`match_type:"PES_LEAGUE_MATCH_1VS1_LOBBY"`**, dates (unix seconds), `entry_user_limit:"NO"`, `entry_limit_division:"NO"`, `is_end:"NO"`, empty banner blobs, `is_entry:"NO"`; `list_num:1, list_max:1` | selecting it → `compe_detail` |
| 6 | PesLgCompeDetail 0x140da8120 | CMD_GET_PESLEAGUE_COMPE_INFO {compe_id} | the same `match_type`; **`eula_url:""`, `entry_compe:"NO"`**, `entry_user_limit:"NO"`, `entry_limit_division:"NO"`; **`match_end_date` in the future** | the join action becomes `team_select` (a non-empty eula_url would give `eula_agree`) |
| 7 | PesLgCompeTeamSelect 0x140da97c0 | CMD_GET_PESLEAGUE_COMPE_TEAM_LIST | `{team_list:[<team id>, …], team_list_num}`: real national-team ids from pesdb.py | `proceed` |
| 8 | PesLgProcCompeJoin 0x141371030 | CMD_JOIN_PESLEAGUE_COMPE {compe_id, team_id}; then CMD_GET_PESLEAGUE_1VS1_MAINMENU_INFO (always sent) | NOERR; the 1VS1 info fields with placeholder values (parser 0x141c36530) | **`join_lobby`, SetMode(0x3a)** |
| 9 | Lobby chain (same JSONs as the Friendly lobby) | GET_LOBBYLIST (lobby_type probably "PESLEAGUE"), JOIN_LOBBY, GET_LOBBYINFO, GET_BLOCKLIST, JOIN_BLOCK, REQWATCHL_ROOMLIST | **the existing lobbytest.py replies**; an empty room list is fine | → LobbyRoomList, with the gate true |
| 10 | LobbyRoomList, button slot 51 (0x140bdc330) | none | none | `watching_match` (0x140bdc34f) |
| 11 | LobbyWatchingMatchList (menu 0x140be0f80; task 0x1410511a0 for kind 3) | **CMD_GET_WATCHING_MATCH_LIST** {match_category:"PESLEAGUE_LOBBY", compe_id, index} | one entry, see below | pick → dialog option 0 → `proceed` |
| 12 | WatchMatch | see §4 | | |

**The GET_WATCHING_MATCH_LIST entry** (parser 0x141bd11e0, builder 0x141058a30):

```
match_list:[blob{ match_id:"<id>", game_phase:"2ND",
  game_result: blob{ date:<unix>, ex_type:"", pk_flag:"NO", no_match:"NO",
     player:[blob{pid, pname blob{name, xuid_one:"", psn_user_id blob{account_id, online_id, is_valid_cache, is_deleted}},
                  side:"HOME", is_com:"NO", cpu_level, is_follow:"NO", is_guest:"NO"}, blob{… side:"AWAY" …}],
     team:[home_team_id, away_team_id],              <- game_result level, ALWAYS 2
     score:[blob{score_1st,score_2nd,score_ex1st,score_ex2nd,score_pk}, blob{…}],  <- ALWAYS 2
     match_category:"PESLEAGUE_LOBBY", gameend_reason:"NORMAL_END" }}],
match_list_num:1, total_match_num:1
```

- The builder reads `team[0..1]` and `score[0..1]` with no size check (0x14105944f–0x14105949d). So **send two
  of each**. Fewer probably crashes the game. **[static; the crash is inferred]**
- An entry is dropped if a score is negative, or if the gameend_reason index is above 0x12 (0x1410595a3). **[static]**
- **This corrects spectate.md**, which placed `team`/`score` inside each player.
- `game_phase` is the 19-value enum 0x1437f2420: DATASYNC…PK, END (initializer 0x140219600). **[static]**
  - No filter on it was found.
  - Using a mid-match value such as "2ND" is the safe choice. **[guess]**

**Two values that have to be right:**
- **match_end_date.** It becomes the deadline of TaskPesLgWatching (0x14137162f → 0x1421a71c0). Once the server
  clock passes it, the game shows **OBMB107**. Send now + 30 days, in unix seconds (the unit is inferred).
- **match_type.** It must be the lobby string in COMPE_INFO. Any other string falls back to 1VS1, and the game
  then opens the PES League 1v1 menu instead of the lobby.

### (b) The `onlinewatch` top-menu item: no server reply can unlock it

- The top menu is the compiled table built by 0x1401c7fb0 (lobby.md §1–2). It has no `onlinewatch` record, and
  nothing filters or adds records.
- **Searched for other ways to set mode 0x1d.** All 35 calls of SetMode 0x14149a570 were checked:
  - None passes a constant 0x1d.
  - The calls that pass a register get the mode from the top-menu record (0x140ab03f0) or from these sources:
    - the resume/top-menu helper 0x140ab1a90, which uses 0x1a/0x1b/0x1c/0x1e/0x36/0x3b/saved mode;
    - the Information shortcut table 0x142777c80, whose 5 entries are all mode 0x2d;
    - PES League 0x141371030, which uses 0x3a/0x2a.
  **[static]**
- **The news, announcement and notification flows never lead to WatchMatch or LoginFlow.**
  - Information flows go only to PlayerInfo, UserList, TopMenu and the PES League result pages [data].
  - `link_url` / WEB_LINK open a browser (menus.md).
- So GET_SERVER_ENV, GET_URLLIST, news and urgent announcements cannot unlock it.

**A data-only edit does not work either** [static + data]:
- Pointing the Friendly-lobby `LobbyRoomList` `02_other_match` edge at `LobbyWatchingMatchList` would open the list
  in kind 1.
- But the list-task selector 0x141051210 only creates a task for kinds 3 and 4. For any other kind it returns 0,
  so no list request is ever made.
- `LobbyOtherMatchList` (button `02_other_match`, CMD_REQWATCHL_BLOCK_MATCH_LIST) is in the Friendly lobby. It only
  leads to UserList and has no watch edge.

**The fallback, if (a) fails live.** This is a memory patch from the mod (dinput8) at run time; no file on disk
changes. It is as in lobby.md §5:
- Wait until `begin = *(u64*)0x1436f57a8` is non-null. Then check that `*(u32*)(begin+15*0x48) == 0x15` and that
  the string at +0x20 is `"multilobby"`.
- Write the mode at `+0x18 = 0x1d` (u32).
- Write the name at +0x20: the MSVC short-string buffer gets `"onlinewatch\0"`, and +0x30 (size, u64) = 11. The
  capacity at +0x38 stays 15.
- Result: Kick Off → the old "Team Play Lobby" row now runs `14_onlinewatch` → TopMenuModeInit → LoginFlow
  `14_test` → WatchMatch.
- No match is chosen first on this path, so the id is `"0"` (0x1411454fd) and the URL is `<WATCHING_GAME>/0/1.enc`.
  The server should serve the latest recording for id `0` and for any unknown id. **[static, misc.md §2f]**
- Patching the game's exe or CPK files is never needed.

### (c) Other entries

- **Matchday** (`MenuMainMatchday` `06_observe` → `MatchdayProcToWatchMatch` → WatchMatch) exists. It needs the
  whole Matchday mode, and its flows sit under `Online/MyClub/`:
  - JOIN_MATCHDAY_MODE, GET_MATCHDAY_LIST/INFO/MAINMENU_INFO;
  - entry, squad and SP game-plan commands;
  - SET_MATCHDAY_START_WATCH_MATCH and GET_MATCHDAY_WATCH_MATCH_REWARD.
  That is more work than PES League, and where its id comes from was not traced. **Not recommended.** [data + inferred]
- **The room itself.** No edge from the Friendly or Team Play room flows leads to WatchMatch. [data]

---

## 3. A finished match, or a live one

- **Watching from storage works.** [inferred, strong]
  - The header check (0x141258f90) compares only the revision, the build string, and whether the id, b1, b3 and
    member list stay the same within the stream. The URL id is never checked against the stream (misc.md §2a).
  - The downloader takes N = 1, 2, … about 1 s apart whenever chunks are ready (0x1411dff40).
  - Nothing compares the match with the clock. TaskPesLgWatching only checks the competition's match_end_date.
  - So a stored recording plays at the game's own speed.
- **With two copies:** record copy 1 vs copy 2 first. Then either copy watches; the other can stay at the top
  menu or be closed.
  - The viewer may be one of the recorded players. Nothing that compares the viewer's pid with the stream was found.
  - The stream holds node ids, not pids. **[guess: no check]**
- **Live watching** needs a viewer that is not playing, so **a third copy** (or a friend's copy).
- **Timing rules for live viewing.** Both come from 0x1411dff40 / 0x1411dfb40. **[static]**
  - The first chunk must exist within **30 s** of the viewer's first GET.
  - Each later chunk must exist within **330 s**. Answer **404** until a chunk is ready.
  - With a 5-s cut both are easy.

---

## 4. The viewer's request sequence once "Watch" is chosen

| # | what | server |
|---|---|---|
| 1 | **Selected id stored.** The chosen entry's `match_id` is copied to `[[mgr]+0xa8]+0x30` (0x141050b30, called at 0x140be1177). The copy into the URL id (match-info `+0x78`) is in protected code, not seen. **[static/inferred]** | nothing |
| 2 | **WatchMatch** (process::OnlineTestMatchWatch, setup 0x141368db0, sets mode 3 at 0x141368e04). TaskMatchSession in observe mode skips GET_MATCHID, the notices and START_SEND. It sends only **CMD_GET_SERVER_ENV** (no TURN list). **[static, misc.md §2f]** | the normal reply |
| 3 | **HTTP GET `http://<server>:8080/u6/<id>/1.enc`**. WATCHING_GAME is index 6 in pescmds.URL_TYPES, so its URL is `…/u6`, and the client appends `/<id>/<N>.enc` (0x1411df950, 0x1411dfb90). It is plain HTTP, with a 1 MiB buffer and a 3,600,000 ms timeout. `<id>` = the listed match_id, or `"0"` if it is empty. | 200 + the `.enc` body, or **404** if it is not ready |
| 4 | Each success is followed about 1 s later by a GET of N+1. On a failure the client retries the **same** N, waiting 1/1/2/3/6/8 s. A body ending in `END_OF_DATA` stops the polling. | |
| 5 | Pre-match sync (MenuOnlineObserverTaskSync), WatchMatchMenu → Match/MatchSetup → the replayed match. No further gate commands were found. **[inferred]** | |
| 6 | MatchEnd `10_postOnlineObserveMatch` → WatchMatchEnd → **CMD_SEND_WATCHING_RESULT** {match_id, is_connect, phase, error_code, reason} (0x140bebb60). `proceed` → TopMenu; `0_pesleague` → back to the list. | NOERR; log it |

**What the server has to add.** It goes in a test add-on, like lobbytest.py:
- **HTTP route.** `httpgate.py` currently answers any `/u…` GET with a 200 HTML page (the `startswith(("eula","u"))`
  branch). The client would try to decrypt that page.
- So the add-on must catch `GET /u6/<id>/<N>.enc` before that branch. It answers with the file, or 404.
- An unknown id or `0` maps to the latest recording.

---

## 5. Live test plan (two copies, stop at the first signal that proves each step)

**Step 0, offline, no game.**
- Write the add-on: the handlers above, the HTTP route, the chunk writer.
- Round-trip test in Python: decrypt your own `.enc`, strip the padding, check the flag, find `END_OF_DATA`.
- Parse the items with a copy of the viewer's reader rules (kinds 0–3).

**Step 1: record (copy 1 vs copy 2, Friendly Match Lobby or Quick Match). Set the shortest match length in the room.**
- **Signal A:** both pids send START_SEND_GAME_SEQUENCE_DATA right after GET_MATCHID, and the match is not aborted.
  This shows the upload is switched on.
- **Signal B:** the first SEND_GAME_SEQUENCE_DATA from each pid. Check:
  - every `data` is 344 characters;
  - the first record is type 0, with data starting `00 00` and three BE32 revisions;
  - type-2 data starts `02 <fromId> <channelId>`, then BE32 = `sequenceNumber` and BE16 = `data_len-9`;
  - the log has a "fragmented message" line, so reassembly works.
- **Then:** let the match run about 1 minute past kick-off and quit, so the recording has the sync, team select and
  some play. The server appends `03 00`. Play a full match only if the normal `03 00` end record needs checking.

**Step 2: watch the recording (copy 1 only; copy 2 can be closed).**
- Path: eFootball → Online Tournament → the competition → Join → team → lobby → block → room list → the
  watching-list button → the entry → Watch.
- Stop at the first signal that fails:
  1. JOIN_PESLEAGUE_COMPE_MODE … GET_PESLEAGUE_1VS1_MAINMENU_INFO, then GET_LOBBYLIST in the log. Note the
     `lobby_type` and `online_mode` strings it sends.
  2. **GET_WATCHING_MATCH_LIST** {match_category:"PESLEAGUE_LOBBY"} is in the log. That proves the gate.
  3. GET_SERVER_ENV, then **`HTTP GET /u6/<id>/1.enc`**. That proves the id: is it the listed match_id, or `0`?
  4. **`GET …/2.enc` about 1 s later.** That proves chunk 1 decrypted and was accepted: the client asks for N+1
     only after a success, and a failure repeats 1.enc. This is the cheapest proof of the container format.
  5. The replayed match is on screen. Then SEND_WATCHING_RESULT with an empty `error_code`.
- **Error codes:**
  - PWTV340: `03 00` missing.
  - PWTV347: a bad item.
  - DZWO960: chunks missing for too long.
  - OBMB107: match_end_date has passed.
- **If step 2 stops before item 2:**
  - Read the help bar of the screen it stopped on: the CompeDetail join button and the room-list button were not
    found statically.
  - Then fix the reply for the last command in the log.
  - If the lobby path can't be made to work, use the fallback (§2b, the mod's memory patch).

**Step 3 (optional): live watching.** It needs a third copy. Copies 1 and 2 play while copy 3 watches.
- **Signal:** copy 3's `1.enc` GET is answered within 30 s, and N keeps growing about every 5 s while the match runs.

## Still unknown (settled by the steps above)

- The step from `[[mgr]+0xa8]+0x30` to the URL id: step 2, signal 3.
- The `online_mode`, `compe_mode` and `lobby_type` strings for PES League: step 2, signal 1.
- Which pad buttons open the CompeDetail join and the room-list watching list: step 2, the help bar.
- Whether the TeamSelect screen accepts plain national-team ids from `team_list`: step 2.
- Whether `game_phase` filters anything in the list. Use "2ND" until tested.
