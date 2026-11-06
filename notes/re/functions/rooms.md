# Rooms / Online Match Lobby — static RE + implementation spec

## Corrections from later findings (2026-11-05)
- **Menu path: Kick Off → Friendly Match Lobby** (8th item, below Cup in a scrolling list; Team Play Lobby is 9th). An earlier correction here wrongly called it hidden (lobby.md).
- Live (livetests.md): REQWATCHL_ROOMLIST is a real request; the order is CREATEJOIN_ROOM then SET_GAMEENV; in REQWATCHL_ROOMPLAYERLIST_NTL `pname.psn_user_id` is a nested blob `{account_id, online_id, is_valid_cache}` (an object exits the game; an empty list is read as garbage). After CREATEJOIN_ROOM the server must push the owner's room into the room list before the player list (roomcrash.md), and must answer the player-list request **only with the reply**: an extra copy pushed right after it ends the game. With that, the owner gets to ENTRY_GAME and the room screen. A second player's room list showed 0 rooms for our entry (livetests.md); code reading (roomlist.md) found the cause: the list hides every room whose `gameenv.limitTime` is NOSET (or unknown), and also rooms with `is_invite_limit` YES or an empty `gamer` list. Our test server never stored the real `game_env` blob from SET_GAMEENV, so it sent NOSET. Fix: store the blob, echo it as `gameenv`, push a room-list UPDATE after SET_GAMEENV. **Live 2026-11-06: the whole flow works with these rules** (room list → details → join → both Start Match → owner's SET_GAMEMEMBER → DECIDE_GAMEENV + ROOMSTATE SETENV → team selection on both games); the exact sequence is in livetests.md.
- Mode names (sweep.md): `lobby_type` FREE/MULTI/MYCLUB/PESLEAGUE; JOIN_ONLINE_MODE `online_mode` QUICK/RANKMATCH/FREE/MULTI/MYCLUB/PESLEAGUE/MATCHDAY/COOP/EDIT_DATA_LIVE_UPDATE.


Group **rooms**. Binary PES2021.exe 1.01.00. Scratch material (scripts, dumps): the working folder (dumps not published; general tools in `notes/re/functions/tools/`)
(`packer.py` request-key scanner, `ptypes.py` reply-type scanner, `enums.py` enum-string resolver,
`wl_c1340.asm` = watch-list merger). Tags: **[static]** read in code at the address, **[log]** our traffic,
**[inferred]**, **[guess]**.

## 0. Big picture

There are two lobby products in the binary, both fully implemented client-side:

| | "Free" lobby | "Multi" / MOM lobby |
|---|---|---|
| main-menu item id | `freelobby` (table 0x1434b6c58, init 0x1401c874c) | `multilobby` (0x1434b6c60) |
| game mode string | `LOBBY1VS1` (mode table 0x14296cbb0) | `MOMLOBBY` |
| room task | `onlinemode::TaskLobbyRoomMainFree` (vt 0x14281e730), OnlineModeTaskLobbyRoomMain.cpp | `TaskMOMLobbyRoomMain` (vt 0x142829430), OnlineModeTaskMOMLobbyRoomMain.cpp |
| processes | `process::OnlineLobbyFree*` (ProcessOnlineLobbyFree*.cpp) | `process::OnlineLobbyMulti*` |
| room size | `max_players = 2` hard-coded in the Free CreatejoinRoom builder (0x1410e86bb) | many (menu `modeMainMenuNetRoomEleven`, side select, countdown notices `MOMROOM_*`, position rating, best player) |

**For "play a chosen friend" we need only the Free (1 vs 1) lobby.** MOM = "multi online match" lobby (several
humans per side, 11-a-side room UI); it shares 90 % of the protocol (same commands, `…ForLobbyMulti` command
objects) plus countdown notices and `CMD_GET_ROOM_BESTPLAYER` / `CMD_GET_POSITION_RATING`. Not needed now. [static names, inferred meaning]

Hierarchy (menu labels → protocol): **"Server" = lobby** (`CMD_GET_LOBBYLIST`/`CMD_JOIN_LOBBY`), **"Lobby" = block**
(`CMD_GET_BLOCKLIST`/`CMD_JOIN_BLOCK`; a block is a sub-area of a lobby with its own room list and player cap),
**room** (`CMD_CREATEJOIN_ROOM`/`CMD_JOIN_ROOM_NTL`). Menu process names: `Online/Lobby/LobbyServerSel` →
`LobbySel` / `LobbyProcessInitBlockSelect` → `LobbyRoomList` → `LobbyRoomSettings` (create) or join →
`LobbyRoomMain` → `LobbySync` (OnlineLobbyNetMatching) → `LobbyTeamSel` → `LobbyMatchMenu`. [static strings 0x14265e4a8…, inferred mapping]
The flow graph between processes is data-driven (event ids such as 0x1071114 are only *posted* in the exe,
never compared) — it lives in menu data, so the exact screen order between SETENV and kick-off is **[inferred]**.

### Three kinds of server commands (important)
1. Plain request/reply (`CMD_CREATEJOIN_ROOM`, `CMD_ENTRY_GAME`, …).
2. **`CMD_WATCH_*` = pure server pushes.** The client registers a local watch and never sends the request
   [log: no `RX CMD_WATCH_*` in any of our logs; QM WATCH_MATCHING/…NOTICE all TX-only]. Push with kind 0x60,
   seq 1, **rqid 0**, routed by msgid. Watch objects are queued in a separate registry (0x1436feb70, filled by
   0x141215a90) from plain requests (0x1436fea68/0x1436feb38, 0x141215240) [static].
3. **`CMD_REQWATCHL_*` = request + list subscription.** Separate registry 0x1436febc8 (0x141215870) [static];
   the request carries arguments (`room_id`) that only make sense if sent, e.g. the room-info preview
   `CmdReqwatchlRoomplayerlistNtlForRoomInfo` (0x14136d190) asks for a room you are not in → **a request is sent
   and must be answered; later pushes with the same msgid (rqid 0) update the list** [inferred, strong].
   Safe server behaviour: answer the request *and* push proactively (see §3), the merge is idempotent.

### The watch-list protocol (all `REQWATCHL_*`) — onlinesystem::ReqWatchListMessageResult
Gate path = vt@+280 slot2 **0x1411c1340** (HTTP twin 0x1411c0bc0) [static]. For every incoming
REQWATCHL message (reply or push) it:
- reads `list_key_name` (STR), `list_type` (STR), `list_data` (array of **msgpack blobs**);
- **if `list_data` is missing or empty → nothing changes** (0x1411c1657/0x1411c1660), even for RELOAD;
- `RELOAD` → clear the cached map, then insert every element; `ADD`/`UPDATE` → (re)insert; `DEL` → remove;
- the map key = value of field `list_key_name` inside each unpacked element, **must be a msgpack int**
  (type 2 check at 0x1411c189c; a double/str key becomes 0 → elements collide);
- then rebuilds the message: `list_data` = all cached blobs in ascending key order, `list_num` = count, other
  top-level keys copied, and hands it to the normal parser.
Consequences: always send `list_key_name` (`room_id` for room lists / block match list, `pid` for room player
lists — also the client defaults, strings in 0x141d01350 / 0x141bb6e60 / 0x141d59810); send full lists as
`RELOAD`; remove entries with `DEL` + element blob `{<key>: N}`; to "clear" a list you must DEL each element.

## 1. Flow — Free lobby, owner (A) and friend (B)

```
both: JOIN_ONLINE_MODE (online_mode presumably "FREE")  ...usual online-mode prelude (SEND_IPANDPORT_NTL etc.)
both: GET_LOBBYLIST{lobby_type,lang} -> JOIN_LOBBY{lobby_id} [-> GET_LOBBYINFO{lobby_id}]
both: GET_BLOCKLIST -> JOIN_BLOCK{block_id}
both: REQWATCHL_ROOMLIST            (TaskWatchFreeLobbyRoomList, started by OnlineLobbyFreeStartWatchOnBlock 0x14136aa30)
      [optional: REQWATCHL_BLOCK_MATCH_LIST{pid}  "other matches" list]
A:    SET_GAMEENV{…} (room settings, "Online/Lobby/LobbyCmdSetGameEnv")   -- order vs. CREATEJOIN not proven
A:    CREATEJOIN_ROOM{name, match_type, lang, max_players=2, is_invite_limit, password, …}  -> {room_id}
        client registers WATCH_ROOMSTATE + WATCH_NOTICE (0x141396200)
        OnlineLobbyFreePostCreateJoinRoom 0x14137a8f0:
          st0 register REQWATCHL_ROOMPLAYERLIST_NTL + WATCH_DECIDE_GAMEENV + WATCH_ENTRY_GAME, remember tick
          st1 WAIT until a ROOMPLAYERLIST message arrives after that tick (no timeout here)
          st2 find own pid in the player list (else error FreePostCreateJoinRoom.cpp:130)
              -> ENTRY_GAME{entry:"FALSE", side:"HOME"}   (entry byte = 0 here; side from own is_owner)
          st3 wait reply -> st4 start TaskLobbyRoomMainFree (registers WATCH_TEXTCHAT, 0x141043970)
        server -> all watchers of the block: REQWATCHL_ROOMLIST UPDATE (new room)
B:    (room list shows A's room; optional preview: REQWATCHL_ROOMPLAYERLIST_NTL{room_id})
B:    JOIN_ROOM_NTL{room_id, is_invited:"NO", password}  -> {room_owner:{…A + addresses}, room_stat}
        errors: ERR_PASSWDINCORRECT -> password prompt/"err_pass", ERR_ROOMNOTFOUND -> "err_dlg_noroom" (0x1410f30a0)
        client registers WATCH_ROOMSTATE + WATCH_NOTICE (0x1410f3c80)
        OnlineLobbyFreePostJoinRoom 0x1413749e0: registers the same 3 watches, then waits for a ROOMPLAYERLIST
          message newer than the start tick, **timeout 10 000 ms → error FreePostJoinRoom.cpp:107** (0x141374a2f)
        0x1413745d0: own entry must exist (else :159), ENTRY_GAME{entry:"FALSE", side: own is_owner?HOME:AWAY}
        0x1413743c0: start TaskLobbyRoomMainFree
        server -> A and B: REQWATCHL_ROOMPLAYERLIST_NTL (B added), room list UPDATE (room_user_count 2)
In room (TaskLobbyRoomMainFree 0x141042bb0, states via fn-ptr [task+0x140]):
  - user presses Entry / Cancel  -> ENTRY_GAME{entry:"TRUE"|"FALSE", side}   (events 0x107110a / 0x107110b -> 0x141044210)
  - server pushes WATCH_ENTRY_GAME {entry_count, entrygame:[{pid, entryNum, gameNum}]} to the room
      handler 0x1410e48e0: player pid counts as "entered" iff gameNum >= 0 → flags room-map node +0x58
  - every frame 0x141044e70 (owner only, room state <= WAITING): when a ROOMPLAYERLIST update has arrived since
    last check AND >= 2 players in room AND all of them entered (0x141043610 → 0x14103e9a0)
    -> event 0x1071110 -> **owner sends CMD_SET_GAMEMEMBER** (0x141043ff0; empty body: msgid,rqid,svrtype,client)
       Also, first time >= 2 players: owner posts 0x107110c = SEND_NOTICE "ROOM_SHARE_INFO" to the others (0x1410446e0).
  - server: room.state = SETENV, push WATCH_DECIDE_GAMEENV (room env), WATCH_ROOMSTATE{event:"StateChanged",state:"SETENV"}
      handler 0x141102880 queues it; 0x141044d25: StateChanged→SETENV posts 0x1071114 → menu flow to sync/team select
  - match: TaskLobbyConnectMatchSessionFreeLobby (vt 0x142c00220) reuses the generic TaskConnectMatchSession /
    TaskMatchSession (same as Quick Match): opponent = first pid in the room map ≠ me (0x141040e80); its
    host/reflexive/relayed addresses come from the **ROOMPLAYERLIST element** (copied into the member list by
    0x14111e8a0, slot 0x1421a84d0); game relay via CmdGetGamerelayFreeLobby = HTTP CMD_GET_GAMERELAY (0x1421a8170).
    From here the wire flow is the Quick-Match one (GET_GAMERELAY, SET_GAMERELAY_CHECK_RES, SEND_*NOTICE relay,
    SET_GAMEMEMBERENV, GET_MATCHID, CHANGE_GAMEPHASE …, SEND_GAME_RESULT …) [inferred from shared code].
  - after the match Free post-game flow sends **CMD_LEAVE_GAME{pid}** (OnlineLobbyFreePostGameFlow 0x1413891f0) and
    restarts the room-list watch (0x141389350). Room state must go back to WAITING (see §3) or the owner can never
    start again (all start paths gate on room state <= 1, 0x141044ea5 / 0x141043641).
Leave: LEAVE_ROOM{error_code} (also "CmdLeaveRoomForce" on errors), LEAVE_BLOCK{pid}, LEAVE_LOBBY.
```
Owner hand-over: WATCH_ROOMSTATE `AppointOwner` (event idx 1) copies `password` into room data (0x141102b1e) and
makes the new owner re-send its options → `CMD_SET_ROOM_OPTION` (event 0x107110d, 0x141043b20). [static]

## 2. Per-command reference

Request keys = gate msgpack packer (all also carry msgid, rqid, svrtype "MENU", client "NETCLIENT").
S = msgpack str, N = int, B = nested msgpack blob, [B] = array of blobs. Reply fields with offsets are the parser's.

### CMD_GET_LOBBYLIST — list lobbies ("servers")
- Request: `lobby_type` S (from a virtual getter, value not resolved), `lang` S. Packer 0x141d3b700.
- Reply (0x141d3be20): `lobby_list_num` N, `lobby_list` [B{`lobby_id` N, `name` S (≤0x20 chars used), `player_num` N, `player_num_max` N, `gamerelay_index` N}]. Empty list → "nodata" branch (0x1410ed900).
- Errors seen in table: ERR_COMMAND_BLOCKED.
- Server: already implemented (pesmatch.get_lobbylist) — keep. Confidence high [static].

### CMD_JOIN_LOBBY / CMD_LEAVE_LOBBY
- JOIN request `lobby_id` N; reply `gamerelay_web_url` S (empty works in QM [log]). Error ERR_LOBBYUSERMAX.
- LEAVE: no args. Server: existing handlers fine.

### CMD_GET_LOBBYINFO — lobby notice text
- Request `lobby_id` N. Reply `has_info` S (YES/NO), `title` S, `sentence_info` S, `sentence_intro` S.
- Server: `{has_info:"NO"}` (or YES + server MOTD). [static]

### CMD_GET_BLOCKLIST — list blocks in the joined lobby (**must implement**)
- Request: none. Reply (0x141d274c0): `count` N, `bklist` [B{`name` S, `player_num` N, `max_player_num` N, `block_id` N}]. Empty → "nodata".
- Server: `count:1, bklist:[{block_id:1, name:"Friendly Rooms", player_num:<online in block>, max_player_num:1000}]`.

### CMD_JOIN_BLOCK / CMD_LEAVE_BLOCK
- JOIN request `block_id` N; reply bare. Errors ERR_BLOCKISFULL, ERR_BLOCKNOTEXIST, ERR_DIVISIONREFUSED.
- LEAVE request `pid` N. Server: track `sess.lobby_block`; LEAVE removes room-list subscription.

### CMD_REQWATCHL_ROOMLIST — room list of the block (request + pushes)
- Request: none (packer 0x141d02b00). Reply/push parser 0x141d03090 (Result list at +0x100).
- Body: `list_type` S, `list_key_name` S = `"room_id"`, `list_num` N, `list_data` [B room], room =
  `room_id` N, `status` S (room state name, e.g. WAITING) [inferred], `game_phase` S, `name` S,
  `gameenv` B{`cpuLevel` S, `gametime` N, `injury` S, `ball_type` N, `exGame` S, `pkOnOff` S, `substitution` N, `exSubstitution` S, `limitTime` S},
  `gamer_num` N, `gamer` [B{`pid` N, `is_room_owner` S YES/NO, `side` S HOME/AWAY, `enter_no` N, `game_no` N}],
  `is_passwd` S YES/NO, `max_players` N, `is_invite_limit` S, `dlcontent` S, `is_original_player` S, `vs_com` S,
  `owner_pname` B{`name` S, `xuid_one` S, `psn_user_id` B{`account_id` N, `online_id` S}, `is_valid_cache` S, `is_deleted` S},
  `owner_lang` S, `room_user_count` N, `is_anyplayers` S, `is_select_gk` S, `auto_start_num` N, `room_number` N, `game_rating_limit` S.
  (Nesting from key order + blob unpacks; owner_pname same blob shape as our `_pname`.) [static order, inferred nesting]
- Errors: list → "Online/Lobby/LobbyErrorRoomList" process (0x141127d20).
- Server: reply RELOAD with all rooms of the block (if none: reply `list_data:[]`, client shows empty list);
  subscribe the session; push ADD/UPDATE on create/change, DEL `{room_id}` on removal, to every subscribed
  session of the block (rqid 0).

### CMD_CREATEJOIN_ROOM — create a room and enter it as owner
- Request (packer 0x141d22310; defaults from Option ctor 0x141d21b10; Free builder 0x1410e8300…):
  `name` S, `match_type` S ∈ {OC_RANKING, OC_FREE, MULTI, MYCLUB_FREE, PESLEAGUE} (enum 0x141b99f00), `lang` S,
  `max_players` N (=2 Free), `is_invite_limit` S YES/NO, `password` S, `enable_guest` S, `vs_com` S
  ∈ {PL_VS_PL, PL_VS_COM} (default PL_VS_PL), `use_original_player` S (YES), `is_anyplayers` S (YES),
  `is_select_gk` S (YES), `auto_start_num` N, `game_rating_limit` S ∈ {NONE, NORMAL, HARD} (default NONE).
- Reply (0x141d232a0): `room_id` N only.
- Errors (table, approximate): ERR_ROOMCREATELIMIT, ERR_ALREADYINROOM, ERR_NGWORDINRNAME, ERR_INVALIDARG.
- Server: create room (owner = sender), reply `{room_id}`, then pushes of §3 "join" (player list to owner, room list ADD).

### CMD_JOIN_ROOM_NTL — enter an existing room (the "choose your friend" step)
- Request (0x141d51fb0): `room_id` N, `is_invited` S (YES when coming from a platform invite), `password` S.
- Reply (0x141d52730): `room_owner` B{`pid` N, `pname` B{…}, `host_addr` B{`address_family` N, `sa_data` S}, `reflexive_addr` B{…},
  `relayed_addr` B{…}, `ntl_nat_type` N} (+0x1b0 for nat type), top-level `room_stat` S (→ +0x1b8). [static, nesting verified by loop bounds 0x141d52c10…0x141d53e0a]
- Errors handled (0x1410f30a0): `ERR_PASSWDINCORRECT` (wrong/missing password → password dialog), `ERR_ROOMNOTFOUND`
  (→ "no room" dialog), anything else → generic error dialog. Table also lists ERR_ROOMISFULL, ERR_TOOMANYMEMBERS,
  ERR_ROOMSTATNOTMATCH, ERR_JOINKICKEDROOM, ERR_RATING_NOTMET, ERR_DIFFDCVERSION, ERR_ALREADYINROOM.
- Server: check room exists (ERR_ROOMNOTFOUND), full (ERR_ROOMISFULL), state WAITING (ERR_ROOMSTATNOTMATCH),
  password if room has one (ERR_PASSWDINCORRECT); add member; reply with owner's stored NTL addresses
  (same blobs as pesmatch `st["ntl"]`) and `room_stat` = state name; then §3 pushes **within 10 s**.

### CMD_REQWATCHL_ROOMPLAYERLIST_NTL — members of a room + their P2P addresses (request + pushes)
- Request `room_id` N. Parser 0x141bb80f0. Body: `list_type`, `list_key_name` = `"pid"`, `list_num`, `list_data` [B player]:
  `host_addr` B{address_family N, sa_data S}, `reflexive_addr` B{…}, `relayed_addr` B{…}, `ntl_nat_type` N, `pid` N,
  `room_player_id` N, `room_entry_no` N, `game_entry_no` N, `pname` B{name, xuid_one, psn_user_id B{account_id, online_id}, is_valid_cache, is_deleted},
  `lang` S, `is_owner` S YES/NO, `side` S HOME/AWAY, `is_quality_bad` S YES/NO, `has_guest` S YES/NO,
  `guest_info` B{`pname` B{…}, `is_platform_guest` S}.
- Uses: PostCreate/PostJoin wait for it (10 s for the guest), owner check (`is_owner` → entry+0x30, read by
  0x141041500), auto-start trigger (each update), match peer addresses (0x141040e80 / 0x1421a84d0), room preview.
- Server: per room; reply RELOAD of current members to the requester; push RELOAD/UPDATE to all members on any change
  (join, leave = DEL `{pid}`, owner change, entry change). Address blobs exactly as stored from SEND_IPANDPORT_NTL.

### CMD_WATCH_ROOMSTATE — room events (push only)
- Parser 0x141d88ae0: `event` S (+0x1d0) ∈ {`OwnerChanged`=0, `AppointOwner`=1, `Kicked`=2, `StateChanged`=3}
  (table 0x1437f4788, init 0x140222010), `owner_pid` N (+0x1f0), `state` S (+0x1f8) ∈ {`NA`=0, `WAITING`=1,
  `SETENV`=2, `GAME`=3, `RESULT`=4, `DISCONWAIT`=5, `RESULTWAIT`=6, `FORFEITEDGAME`=7, `RESULTRETURNWAIT`=8}
  (table 0x1437f2a20, init 0x14021e180), `password` S (+0x218).
- Handler 0x141102880 queues {event,state,owner_pid}; consumer 0x141044d25: AppointOwner → new owner sends
  SET_ROOM_OPTION (password taken from push); StateChanged → room.state=state; →WAITING resets; (prev RESULTWAIT &
  →WAITING) → owner re-shares room info; →SETENV → event 0x1071114 = go to match. Other states only stored
  (gate the owner's start when > WAITING). [static]
- Server: push to all members: SETENV on start, GAME when the match loads, WAITING after the match; on owner
  leaving: `AppointOwner{owner_pid:new, password}` to the new owner, `OwnerChanged{owner_pid:new}` to others.

### CMD_ENTRY_GAME — "I want to play the next game" toggle
- Request (0x141d24c30): `entry` S TRUE/FALSE, `side` S HOME/AWAY (strings 0x1410e90e4). Reply (0x141d25350): `entryflag` S, `gameNum` N.
- Sent once automatically on entering the room (entry FALSE), then on the Entry/Cancel menu action.
- Errors: ERR_ROOMISFULL, ERR_NOTINROOM, ERR_NOGUEST.
- Server: update room.entries; reply `{entryflag: entry, gameNum: room.game_no}`; push WATCH_ENTRY_GAME, then a
  ROOMPLAYERLIST UPDATE (the owner's start check only runs after a player-list update, 0x141044eff).

### CMD_WATCH_ENTRY_GAME (push) / CMD_GET_ENTRY_GAME (request {pid})
- Body (0x141d0eeb0 / 0x141bc3820): `entry_count` N, `entrygame` [B{`pid` N, `entryNum` N, `gameNum` N}] (12-byte entries at Result+0x1e0, count +0x1d8).
- A pid is "entered" iff `gameNum >= 0` (0x1410e4975). Send only entered players (gameNum = room.game_no ≥ 0).
- GET_ENTRY_GAME is used by MOM flows (0x141081920, 0x141396f60); answer with the same body.

### CMD_SET_GAMEMEMBER — owner's "start the game" (re-used command)
- Request: no fields (packer 0x141d6bec0). Reply bare. In QM both clients send it after SEND_MATCHING_RESULT [log];
  in the Free lobby the **owner** sends it from the room when everyone has entered (0x141043ff0). [static]
- Server: if sender owns a lobby room in WAITING with ≥2 members all entered → start the match (§3 "start").
  Otherwise bare NOERR (QM behaviour).

### CMD_SET_GAMEENV / CMD_WATCH_DECIDE_GAMEENV — match settings
- SET request (0x141caf3c0): `game_env` (B?, packer key without visible source), `cpuLevel` S ∈ {VERYEASY, EASY, NORMAL,
  HARD, VERYHARD, HARDEST, LEGEND} (0x141b99d80), `gametime` N, `injury` S YES/NO, `ball_type` N, `exGame` S YES/NO,
  `pkOnOff` S YES/NO, `substitution` N, `exSubstitution` S YES/NO, `limitTime` S ∈ {NOSET, SHORT, MIDDLE, LONG} (0x141b99ea0).
  Sent by the owner when creating ("LobbyCmdSetGameEnv") and when editing in the room ("LobbyRoomCmdSetGameEnv").
- DECIDE push (0x141d7d790): `game_env` B{cpuLevel, gametime, injury, ball_type, exGame, pkOnOff, substitution, exSubstitution, limitTime}.
  Handler 0x1410ffac0 copies it into the match settings object (0x141007db0). [static]
- Server: store env on the sender's room (or pending until CREATEJOIN); push DECIDE to all members right before
  StateChanged→SETENV; mirror into room-list `gameenv`. Safe default env:
  `{cpuLevel:"NORMAL", gametime:10, injury:"NO", ball_type:0, exGame:"YES", pkOnOff:"YES", substitution:3, exSubstitution:"YES", limitTime:"MIDDLE"}` [guess values]. **Never limitTime "NOSET"**: the room list hides such rooms (roomlist.md). Live, the game sends the 9 settings packed in the `game_env` blob, not as top-level keys.

### CMD_SET_ROOM_OPTION — owner edits room
- Request (0x141cfe040): `pid` N, `name` S, `password` S, `is_anyplayers` S, `auto_start_num` N, `game_rating_limit` S. Reply bare.
- Errors: ERR_NOTROOMOWNER, ERR_NOTINROOM, ERR_ROOMNOTFOUND (error site OnlineModeTaskLobbyRoomMain.cpp:571).
- Server: update room, push room-list UPDATE.

### CMD_LEAVE_ROOM / CMD_LEAVE_GAME
- LEAVE_ROOM request `error_code` S (e.g. "EYRH763" [log]); LEAVE_GAME request `pid` N (Free post-game, 0x1413891f0).
- Errors: LEAVE_ROOM ERR_NOTINROOM/ERR_ROOMNOTFOUND/ERR_ROOMSTATNOTMATCH; LEAVE_GAME ERR_NOTINROOM.
- Server LEAVE_ROOM: remove member; push DEL{pid} to remaining; owner hand-over; empty room → delete + room list DEL.
  LEAVE_GAME: mark member back in room; when all members did it → state WAITING, clear entries, game_no += 1.

### CMD_SEND_NOTICE / CMD_WATCH_NOTICE — client↔client room messages
- Already relayed generically (pesmatch `_relay_notice`). Room notices are key/value text built by 0x141090560 /
  0x141091250 with keys `notice_key`, `from_pid`, `roomid`, `pid_list`, `xsession_info`, values `ROOM_SHARE_INFO`,
  `MOMROOM_START_COUNTDOWN`, `…CANCEL_COUNTDOWN`, `…REQUEST_COUNTDOWN`, `…SEND_XSESSION_INFO`, `…REQ_RESULT_TIMEOUT`,
  `…RESULT_TIMEOUT`, `…CONNECT_COMPLETE` (strings 0x14282bfd0…). Keep the existing relay (rqid 0 push to listed pids).

### CMD_SEND_TEXTCHAT / CMD_WATCH_TEXTCHAT — room chat
- SEND request (0x141d09060): `channel` S ∈ {BLOCK, ROOM, TEAM, GAME_QUICK, TEAM_QUICK, COMPETITION, COMMUNITY,
  COMMUNITY_LEAGUE, COOP_ROOM} (enum 0x141b99c50; picker 0x1410e2a80 uses COOP_ROOM or ROOM), `statement` S,
  `list_flg` S ∈ {CHAT_BAN, CHAT_SEND} (0x141b99c40), `list_pid` N(list). Error ERR_CHATENABLE.
- WATCH push (0x141c14170): `channel` S, `from_pid` N, `name` S, `statement` S. Registered on room entry (0x14108a920).
- Server: on SEND with channel ROOM push WATCH_TEXTCHAT{channel, from_pid, name, statement} to every room member
  (including the sender, so it appears locally — [guess]; drop the sender copy if it shows twice); BLOCK → block members.

### CMD_REQWATCHL_BLOCK_MATCH_LIST — matches running in the block ("other match list")
- Request `pid` N. Body: list (key `room_id`) of B{`room_name` S, `home` B{`pname` B, `pid` N, `team_id` N, `score` B{`score_1st`,`score_2nd`,`score_ex1st`,`score_ex2nd`,`score_pk` N}}, `away` B{same}, `game_phase` S, `match_id` S, `room_id` N, `room_number` N}. Error ERR_NOBLOCKBELONG.
- Server: reply `list_type RELOAD, list_key_name room_id, list_data []` (optional feature).

### CMD_GET_ROOM_BESTPLAYER (MOM) / CMD_REQWATCH_ROOM_XUIDONE_LIST (Xbox)
- BESTPLAYER request `pid`,`room_id`; reply `position_best_player` [B{pid, rating, game_count}]. Server: empty list.
- XUIDONE request `pid`,`room_id`; reply `list_size` N, `xuid_one_list` S. Server: `{list_size:0, xuid_one_list:""}`.

### CMD_SET_PLATFORM_SESSION / CMD_GET_PLATFORM_SESSION — platform (Steam) session blob for invites
- SET request `pid`, `data_size` N, `session` S ("CmdSetPlatformSessionLobby"). GET request `pid`, `room_id`; reply `data_size`, `session` ("CmdGetPlatformSessionFree/Multi"); error ERR_ROOMNOTFOUND.
- Invites arrive via the platform overlay (`onlineinterruption::ACCEPT_INVITATION`, `OnlineModeTaskWatchAcceptInvitation`,
  `Online/Invite/ProcessResetInitInvite`) and end in JOIN_ROOM_NTL `is_invited YES`. With our Steam setup
  (lsteamclient disabled) this path is unlikely to work → use room list + password instead. Server: store/return the blob.

### Follow list (CMD_FOLLOW_USER, CMD_GET_FOLLOW_LIST, CMD_LOCK_FOLLOW_USER)
- Lives with the clan / co-op social code (wrappers 0x141065bc5/0x141065ed5/0x141066075 next to CmdCreateClan,
  CmdInviteClan, …; `is_clan_member` field). **No path from a followed player into a Free-lobby room was found.**
- FOLLOW_USER request: `pid`, `follow_user_id_list` [B{pid, pname B}], `unfollow_user_id_list` [same]. Error ERR_FOLLOW_USER_LOCKED.
- GET_FOLLOW_LIST request `pid`; reply `follow_list_num` N, `follow_list` [B{`user_id` B{`pid` N, `pname` B}, `is_locked` S, `is_clan_member` S}].
- LOCK_FOLLOW_USER request `pid`, `target_pid`, `is_lock`. Error ERR_LOCK_USER_FULL.
- Server (later, co-op group): persist follows per pid; for now `follow_list_num 0`.

### Related: CMD_SET_MATCHREJECTPID {pid, reject_pid, is_reject}, CMD_GET_MATCHREJECTLIST → `reject_list_num`, `reject_list` [B{pid, pname…}] (TaskRejectList 0x14112c180). Bare/empty is fine.

## 3. Implementation spec for our server (new module, e.g. `pesrooms.py`)

### State (per service, next to `pes_mm`)
```
lobby = {
  "blocks": {1: {"name": "Friendly Rooms", "max": 1000, "members": set(pids)}},
  "rooms": {room_id: Room},  "next_room": 1,
  "roomlist_subs": {block_id: set(pids)},     # sessions that sent REQWATCHL_ROOMLIST
  "pending_env": {pid: env},                  # SET_GAMEENV before CREATEJOIN
}
Room = {room_id, block_id, room_number, name, password, owner, members:[pid…] (join order),
        state:"WAITING", entries:{pid: {"entryNum":n, "side":"HOME"/"AWAY"}}, game_no:0,
        env:{…}, opts:{match_type, max_players, is_invite_limit, vs_com, is_anyplayers, is_select_gk,
        auto_start_num, game_rating_limit, enable_guest, use_original_player, lang}, left_game:set()}
sess.pes_block, sess.pes_room
```
Reuse from pesmatch: `st["ntl"]` (addresses from SEND_IPANDPORT_NTL), `_pname`, `_player`/`_username`,
`_send_later` (push, kind 0x60 seq 1 rqid 0), `_relay_notice`, `get_gamerelay`, `set_gamerelay_check_res`,
`get_matchid`, `events.observe` — the last three find the match through `_room_of(svc,pid)` =
`st["rooms"]` entries with `owner`/`members`; so **at match start insert `st["rooms"][mid] = {"owner": A,
"members": [A, B], "t": now, "lobby_room": room_id}`** and everything after kick-off works unchanged.

### Element builders
```
room_player(pid, room) = blob{ **ntl[pid] (host_addr, reflexive_addr, relayed_addr, ntl_nat_type),
    pid, room_player_id: index+1, room_entry_no: index+1, game_entry_no: entries[pid].entryNum if entered else 0,
    pname: _pname(p), lang:"en", is_owner: "YES" if pid==owner else "NO",
    side: "HOME" if pid==owner else "AWAY", is_quality_bad:"NO", has_guest:"NO" }
room_entry(room) = blob{ room_id, status: room.state, game_phase: "", name, gameenv: blob(env),
    gamer_num: len(members), gamer:[blob{pid, is_room_owner, side, enter_no, game_no}],
    is_passwd: "YES" if password else "NO", max_players, is_invite_limit, dlcontent:"NO",
    is_original_player: opts.use_original_player, vs_com, owner_pname: _pname(owner), owner_lang: lang,
    room_user_count: len(members), is_anyplayers, is_select_gk, auto_start_num, room_number, game_rating_limit }
wl(msgid, key, typ, elems) = {result:NOERR, list_type:typ, list_key_name:key, list_num:len(elems), list_data:elems}
```

### Handlers (in order of the flow)
1. `GET_BLOCKLIST` → `{count:1, bklist:[blob{block_id:1, name, player_num:len(members), max_player_num:1000}]}`.
2. `JOIN_BLOCK{block_id}` → add to block, `sess.pes_block`; reply NOERR; schedule (0.5 s) push
   `CMD_REQWATCHL_ROOMLIST` RELOAD (only if rooms exist). `LEAVE_BLOCK` → remove + unsubscribe.
3. `REQWATCHL_ROOMLIST` → subscribe; reply `wl("room_id","RELOAD", [room_entry(r) for r in block rooms])`.
4. `SET_GAMEENV{…}` → if sender in room: room.env = fields; push room-list UPDATE; else pending_env[pid].
5. `CREATEJOIN_ROOM{…}` → if in a room: ERR_ALREADYINROOM; create Room(owner=pid, env=pending_env or default);
   reply `{room_id}`; then `after_join(room, pid)`; push room-list `ADD [room_entry]` to block subscribers.
6. `REQWATCHL_ROOMPLAYERLIST_NTL{room_id}` → room missing: `ERR_ROOMNOTFOUND`; reply
   `wl("pid","RELOAD",[room_player(m) for m in members])`.
7. `JOIN_ROOM_NTL{room_id,is_invited,password}` → checks (ERR_ROOMNOTFOUND / ERR_ROOMISFULL when
   len(members) >= max_players / ERR_ROOMSTATNOTMATCH when state != WAITING / ERR_PASSWDINCORRECT when
   room.password and password != room.password); add member; reply `{room_owner: blob{pid, pname, **ntl[owner]},
   room_stat: room.state}`; `after_join(room, pid)`; room-list UPDATE.
8. `after_join(room, pid)`: at +0.5 s and again at +2 s push `CMD_REQWATCHL_ROOMPLAYERLIST_NTL`
   `wl("pid","RELOAD", all members)` to **every** member (guest must get one within 10 s; owner's PostCreate
   waits for one with no timeout; duplicates are harmless).
9. `ENTRY_GAME{entry,side}` → TRUE: entries[pid] = {entryNum: len(entries)+1, side}; FALSE: pop.
   Reply `{entryflag: entry, gameNum: room.game_no}`. Push to all members, in this order:
   `CMD_WATCH_ENTRY_GAME {entry_count:n, entrygame:[blob{pid, entryNum, gameNum: room.game_no}]}` then
   `CMD_REQWATCHL_ROOMPLAYERLIST_NTL wl("pid","UPDATE", all members)` (bumps the owner's start check).
10. `SET_GAMEMEMBER` → if sender is owner of a WAITING room, len(members) >= 2 and all entered: start:
    room.state = "SETENV"; insert st["rooms"][mid] (see above); push to all members, in order:
    `CMD_WATCH_DECIDE_GAMEENV {game_env: blob(env)}`,
    `CMD_WATCH_ROOMSTATE {event:"StateChanged", state:"SETENV", owner_pid: owner, password:""}`;
    room-list UPDATE (status). Reply NOERR. Otherwise (Quick Match) bare NOERR as today.
11. Match: existing QM handlers. On first `CHANGE_GAMEPHASE` GAMELOAD/ENTRY from a room member → room.state = "GAME",
    push `WATCH_ROOMSTATE{StateChanged, GAME}` (optional; the client only stores it).
12. `LEAVE_GAME{pid}` (and/or SEND_GAME_RESULT, see unknowns) → left_game.add; when all members left the game:
    room.state="WAITING", entries.clear(), game_no += 1, push `WATCH_ROOMSTATE{StateChanged, WAITING}`,
    `WATCH_ENTRY_GAME{entry_count:0, entrygame:[]}`, player list UPDATE, room-list UPDATE.
13. `SET_ROOM_OPTION{pid,name,password,is_anyplayers,auto_start_num,game_rating_limit}` → owner only
    (ERR_NOTROOMOWNER); update; room-list UPDATE.
14. `SEND_TEXTCHAT{channel,statement,…}` → push `CMD_WATCH_TEXTCHAT{channel, from_pid, name, statement}` to room members.
15. `LEAVE_ROOM{error_code}` → if pid in a lobby room: remove; push player list `DEL [blob{pid}]` to remaining;
    if owner left and members remain: owner = members[0]; push to new owner
    `WATCH_ROOMSTATE{event:"AppointOwner", state: room.state, owner_pid:new, password: room.password}` and to others
    `{event:"OwnerChanged", owner_pid:new}`; push player list UPDATE (is_owner/side flipped); if the room becomes
    empty: delete it and push room-list `DEL [blob{room_id}]`. Keep the existing QM `st["rooms"]` bookkeeping.
16. Session disconnect → same as LEAVE_ROOM + LEAVE_BLOCK.
17. Bare/simple: GET_LOBBYINFO `{has_info:"NO"}`, GET_ENTRY_GAME (same body as 9), REQWATCHL_BLOCK_MATCH_LIST
    (empty RELOAD), GET_ROOM_BESTPLAYER `{position_best_player:[]}`, REQWATCH_ROOM_XUIDONE_LIST
    `{list_size:0, xuid_one_list:""}`, SET/GET_PLATFORM_SESSION store/return, GET_FOLLOW_LIST
    `{follow_list_num:0, follow_list:[]}`, FOLLOW_USER/LOCK_FOLLOW_USER NOERR.

### Quick Match comparison (OnlineModeTaskQuickMatching.cpp)
QM: START_MATCHING → server pairs → one push WATCH_MATCHING{matching_state FINISH, member[blob{pid, pname, is_owner,
matching_order, side, host/reflexive/relayed_addr, ntl_nat_type}]}. Lobby: the same per-player data is delivered
incrementally as REQWATCHL_ROOMPLAYERLIST_NTL elements (same address blobs and pname blob, plus room fields), the
"match found" moment is replaced by owner SET_GAMEMEMBER → ROOMSTATE SETENV, and the connect/match tasks are the
shared TaskConnectMatchSession/TaskMatchSession. So `_member`'s address/pname code, the NTL store, push helper,
notice relay, game relay, relay-check and match-id handlers are reusable as-is; only the room/list layer is new.

## 4. Still unknown (needs a live test, two clients)
> **Answered live (2026-11-05/06, livetests.md):** 1. yes, REQWATCHL_ROOMLIST and REQWATCHL_ROOMPLAYERLIST_NTL are real requests; 2. CREATEJOIN_ROOM first, then SET_GAMEENV with the 9 settings inside a `game_env` blob; 3. `online_mode` "FREE", `lobby_type` "FREE"; 4. after SETENV: GET_MATCHID, SEND_SYNC_NOTICE, GET_SERVER_ENV, P2P START/END, DATASYNC, SET_GAMEMEMBERENV, TEAMSELECT (no GET_GAMERELAY / SEND_MATCHING_RESULT seen). Kept below as written.

1. Whether `REQWATCHL_*` requests actually appear on the wire (strong inference: yes). The spec covers both
   (reply + proactive pushes). Check `RX CMD_REQWATCHL_ROOMLIST` after JOIN_BLOCK.
2. Order of SET_GAMEENV vs CREATEJOIN_ROOM and the `game_env` request field's real shape (packer key without source).
3. Exact `lobby_type` / `online_mode` strings (JOIN_ONLINE_MODE likely `FREE`, enum near 0x142ad679c).
4. Screens/commands between ROOMSTATE SETENV and GAMELOAD (data-driven menu flow): expected GET_GAMERELAY →
   SET_GAMERELAY_CHECK_RES → SEND_MATCHING_RESULT? → SET_GAMEMEMBERENV → GET_MATCHID. Log what arrives.
5. Whether a Free-lobby client sends LEAVE_ROOM after a match (generic OnlineMatchEnd 0x14137e390 has a LEAVE_ROOM
   step, gated by 0x141041a20 = a lobby flag) or only LEAVE_GAME; if LEAVE_ROOM arrives post-match, decide whether to
   keep the room. Also which event the client needs to go back to the room menu (RESULT/RESULTWAIT → WAITING?).
6. `room_entry_no` / `game_entry_no` / `entryNum` semantics beyond "gameNum ≥ 0 = entered"; ENTRY_GAME `side` source
   (derived from own `is_owner`: owner→HOME, other→AWAY, 0x1413747aa).
7. Whether WATCH_TEXTCHAT should echo to the sender; `list_flg`/`list_pid` meaning (CHAT_BAN list?).
8. Watch timeouts: registry consumer 0x1412160b0 has ERR_CLIENT_TIMEOUT for watches — not measured.
