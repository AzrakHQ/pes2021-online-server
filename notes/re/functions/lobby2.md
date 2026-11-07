# Lobby 2: Team Play (MOM) lobby, room counters, room chat (PES2021.exe 1.01.00, static)

> **Live (2026-11-06):** not yet tested live (counters, chat, MOM). The server now pushes the verdict at END for Friendly Match Lobby matches (§2 fix).


Tags: **[static]** read at the address, **[log]** our logs, **[inferred]**, **[guess]**.
Dumps are in the working folder (not published). Flow JSONs were read from another session's scratch copy of dt16
(the working folder).

## Short answers
1. **Team Play Lobby: two humans can start a room match, but the match cannot connect through our P2P path.** The
   MOM lobby's connect task always uses the **"SC" game relay** (Konami's CsClient protocol, not reverse-engineered).
   It never calls 0x141092720. No server reply changes that. The start rule itself is not the problem: at least
   **2 members, all entered** is enough. You do not need 11 humans or a minimum per side. A **mod** (two vtable
   writes plus one new peer-builder function) could switch it to P2P. The details are below and are not verified.
2. **Room counters are client-side.** "N Match(es)" and "Wins H / A" are three ints in the client's room object. They
   go up **only when a CMD_WATCH_GAMEADDPOINT push arrives while the player is in a lobby room**. They are never read
   from a reply field: game_no, gameNum, the room entry and the player list play no part. In our lobby match no
   WATCH_GAMEADDPOINT was sent [log], so they stayed 0.
   Fix: push the verdict once to both members when both reach CHANGE_GAMEPHASE END.
3. **Chat: the client does not print its own line.** The only way a user line gets into the chat log is
   WATCH_TEXTCHAT. So our echo to the sender is **required**, and the line will not show twice. The chat window has
   two buttons: "Comment" (free text through the software keyboard / IME, slot 0) and "Preset" (slot 2). The
   room-screen key that moves focus to the chat window was **not** found statically [guess: see the live test].

---------------------------------------------------------------------------------------------------
## Q1. Team Play Lobby (MOM, `multilobby`, mode 0x1b ONLINE_MOM_LOBBY)

### Menu entry to match (flows + static)
- Kick Off row 9 → event `multilobby` → TopMenu `13_multilobby` → LoginFlow `02_mom` → `Online/MOMLobby/MOMLobbyInit`
  (lobby.md) [static + data].
- The lobby part is the same command set as the Free lobby, through `MOMLobbyCmd*` objects (strings
  0x14280e060…0x14280eae0):
  GET_LOBBYLIST (lobby_type MULTI) → JOIN_LOBBY → GET_BLOCKLIST → JOIN_BLOCK → REQWATCHL_ROOMLIST →
  CREATEJOIN_ROOM (match_type MULTI) or JOIN_ROOM_NTL → REQWATCHL_ROOMPLAYERLIST_NTL → WATCH_ROOMSTATE /
  DECIDE_GAMEENV / ENTRY_GAME. Its own room-list consumer is 0x1410e1480 (roomlist.md) [static names, inferred order].
- Room task `TaskMOMLobbyRoomMain` (vt 0x142829430). Its event table (built in 0x14107fef0) [static]:

  | event | handler |
  |---|---|
  | 0x107120c | GET_ENTRY_GAME (0x141081920) |
  | 0x107120d | SET_ROOM_OPTION (0x141081e20) |
  | 0x1071211 | **SET_GAMEMEMBER** (0x141082970) |
  | 0x1071212, 0x1071213 | SEND_NOTICE (0x1410822e0, 0x141082530): the MOMROOM_* countdown notices |
  | 0x1071214 | GET_ROOM_BESTPLAYER (0x1410816c0) |
  | 0x1071215 | **GET_GAME_SCORE** (0x141081b20) |

- **Start rule** (0x1410836f0, runs on every player-list update) [static]. The task posts 0x1071211, so the owner
  sends SET_GAMEMEMBER, when all of these hold:
  - the start flag task+0x14c is set (countdown) [inferred];
  - I am the owner (0x14103b5e0);
  - room state ≤ WAITING (`[room+0x20] ≤ 1`);
  - member count `[room+0x18] ≥ 2` (0x1410839a9);
  - every member node is "entered" (byte +0x58; checked in 0x141009460).

  So **2 humans are enough**. Empty positions are filled with CPU by the game [inferred].
- Room menu flow `MOMLobbyMenuRoomMain`: the event `0_match` goes to **`MOMLobbyProcessConnectGamerelay`**. Other
  steps on the path: `MOMLobbyMenuSideSelect` → `MOMLobbyProcessPreGameFlow`, and `MOMLobbyMenuDataSync` → PreMatchSetting [data].

### Why it does not connect through P2P
- The Free and MOM lobbies share one connect state machine, **0x1421a7af0**
  (`TaskLobbyConnectMatchSessionBase`). Only the virtual slots differ [static]:

  | vt slot | Free (vt 0x142c00220) | MOM (vt 0x142c001b8) | role in 0x1421a7af0 |
  |---|---|---|---|
  | 10 (+0x50) | 0x140ca1a50 = **true** | 0x141863350 = **false** | state 0: "use P2P?" |
  | 6 (+0x30) | 0x1421a84d0 (opponent from the Free room, 0x141040e80) | 0x1412e97f0 = base, **empty list** | P2P peer list |
  | 11 (+0x58) | false | true | state 3: "try SC?" |
  | 7 / 8 / 9 | GetGamerelayFreeLobby / … | 0x1421a8280 `MOMLobbyCmdGetGamerelay` / 0x1421a8430 / 0x1421a87b0 | SC path |

- States of 0x1421a7af0 (jump table 0x1421a8064) [static]:
  - **0**: if slot 10 is true, go to state 1 (**P2P**). Otherwise go to state 3.
  - **1**: N.type = 1, then N+0x60 = slot 6 list → **0x141092720** → TaskMatchSession.
  - **3**: if slot 11 is true, run slot 7 (MOM: GET_GAMERELAY command "MOMLobbyCmdGetGamerelay") → state 4/5.
  - **5**:
    - slot 8 0x1421a8430 needs a non-empty relay address string and a non-zero int
      (`[[g+0x28]+0x10]+0x50/+0x70`, i.e. ip_addr / port) [static; field names inferred];
    - slot 9 0x1421a87b0 lists the entered member pids;
    - then **N.type = 2 ("SC")** (0x1421a7f17) → `TaskConnectMatchSession` (CsClient to the game relay).
  - **8**: fail.
- **So MOM never reaches 0x141092720.** Its only route is the game relay. Our server has no relay. An empty
  ip_addr or port 0 makes it fail at once (state 8). A real address would then need the unknown CsClient protocol
  (net.md / coop2.md) [static + inferred].

### Mod option (two humans only; not verified)
1. `*(u64*)0x142c00208 = 0x140ca1a50`: MOM slot 10 returns true, so state 0 takes the P2P branch.
2. `*(u64*)0x142c001e8 = <new fn>`: MOM slot 6. The new function fills the 0x30-byte peer vector with the one other
   human, using the format that 0x1421a84d0 / 0x14111e8a0 write:
   - pid at +0;
   - host family +4 / sa_data +8;
   - reflexive family +0x18 / sa_data +0x1c;
   - nat +0x2c.

   The source is the MOM room model `0x14100c370([g+0x28])`, a player map at +0x10 with node pid at +0x20.
   **Not checked:** whether the MOM player-list consumer keeps the NTL addresses (Free's 0x141096320 does). If it
   doesn't, the addresses must be copied there too, as in coop2.md §6a.
3. The one-peer limit of 0x141092720 (coop2.md) still applies, so this can carry **exactly 2 humans**.
4. Whether the MOM in-match code runs correctly on a P2P session at all is **[guess]**.

### Server differences from pesrooms.py, if the mod is done [inferred]
- GET_LOBBYLIST must answer lobby_type MULTI.
- CREATEJOIN `max_players` > 2 (do not clamp it to 2 as we do now).
- The player list needs a `side` for each player (MOM side select). The room entry's `gamer` side must be the
  chosen side, not owner = HOME.
- WATCH_ENTRY_GAME as now.
- GET_ENTRY_GAME → same body.
- GET_ROOM_BESTPLAYER → `{position_best_player: []}`.
- GET_GAME_SCORE → `{score: []}` (schema: score list of {team, score_1st…score_pk}).
- Relay SEND_NOTICE as now (the countdown notices are client↔client).

### Live test (two copies, one PC; no mod) to confirm the SC route
- Kick Off → Team Play Lobby. Create a room on copy 1, join it on copy 2, and select start on both.
- Expect SET_GAMEMEMBER from the owner. Push DECIDE_GAMEENV + ROOMSTATE SETENV as in the Free lobby.
- **The proving signal is the next request: CMD_GET_GAMERELAY (MOM), and no GET_MATCHID / SEND_SYNC_NOTICE /
  SYSTEM_NOTICE START.**
  - With `game_sycom "P2P"` and `ip_addr ""` the game shows a connect error at once.
  - Stop at the first error dialog.

---------------------------------------------------------------------------------------------------
## Q2. "0 Match(es)" and "Wins 0 / 0"

### Where they are drawn and stored [static]
- `menu::OnlineLobbyRoomMain` (vt 0x1426c1470) function **0x140bce780** reads `X = [[g+0x20]+0x30]`, the room
  object (same object as the player-list tick at +0xd8). It writes three widgets:
  - `numMatches` = string 0x232001e "%d Match(es)" with `[X+0x50]`;
  - `d_side_home`.`point` = `[X+0x54]`;
  - `d_side_away`.`point` = `[X+0x58]`.
- These three fields have only two writers in the binary (scan of every user of the getter 0x141b3bc90):
  - **0x141041e40, the increment.** Its only caller is 0x141100b20, which is called from **0x141100010**. That
    function is slot 27 (OnResult) of `CommandObjectCmdWatchGameaddpoint` (vt 0x1428586d8), so it runs on the
    **WATCH_GAMEADDPOINT push**. Increment rules:
    - it needs to be in a lobby room (0x141041a20: `[R+0x78]` and `[[R+8]+0x78]` non-zero);
    - the verdict's gameend index (+0x84c) must be in {0,2,4,5}: NORMAL_END, OWNGOAL, INJURY, GIVEUP
      (`bt 0x35`, 0x141100b53);
    - `matches++` always;
    - winner_side (+0x848): 1 = WINNER_HOME → `home++`; 2 = WINNER_AWAY → `away++`; anything else (draw) → no win.
  - **0x141044e70 (TaskLobbyRoomMainFree per-frame), the reset.** All three go to 0, and event 0x1071117 is posted,
    when a player-list update arrives while the room has **fewer than 2 members** (0x141044f24..0x141044f5a).
- So nothing the server sends in a reply sets them. game_no, gameNum, entry numbers and room-list fields do not
  feed them [static].

### Why they stayed 0
- tools/lobbytest/server-match.log (13:28): after END the server got SEND_GAME_RESULT and LEAVE_GAME from both
  copies. **No CMD_WATCH_GAMEADDPOINT was pushed and no REQ_GAMEADDPOINT arrived** [log].
- In pesmatch.py the verdict is pushed only in two cases [static, our code]:
  - as the answer to REQ_GAMEADDPOINT;
  - under `PESSERVER_VERDICT_AT_END=1`.

### What the server must send [inferred]
- When **both** lobby members have sent `CHANGE_GAMEPHASE END`, push one CMD_WATCH_GAMEADDPOINT (rqid 0, kind 0x60)
  to each member. This is the existing `_verdict()` body:
  `{player_count 2, player_list [blobs], winner_side WINNER_HOME|WINNER_AWAY|WINNER_NONE,
  gameend_reason "NORMAL_END", is_pk "NO"}`.
  - HOME = room owner.
  - The winner comes from the ADD_SCORE tally.
  - A draw → `WINNER_NONE` → only "Match(es)" goes up.
- **Push it once.** Every push adds 1 on that client. The cached REQ_GAMEADDPOINT re-push is fine, because a client
  that already has the verdict does not send REQ.
- Timing: the push must arrive while TaskWatchGame's watcher is alive. That means at END, before a player confirms
  the stats screen. The first player to confirm leaves, and the other then gets DISCONN_P2P (livetests.md).
- No rating screen in the lobby: group 15 is not in RatingPre's set {13,19,28,29} (sweep.md).
- **Risk:** TaskWatchGame raises OLXH171 if M+0x10 is not 25 or 1 when the verdict arrives (results.md). This push
  timing has never been tested live.

### Live test (two copies)
- Set `PESSERVER_VERDICT_AT_END=1` (it already pushes at END to every member), or add the same push for lobby rooms.
- Play a short lobby match with autoplay, 5 min, and let it end.
- Proof:
  - the server log shows `VERDICT room …`, then `TX … CMD_WATCH_GAMEADDPOINT` to both pids;
  - after LEAVE_GAME / WAITING, the room screen shows **"1 Match(es)"**, and for a decided match "Wins 1 / 0" or
    "0 / 1".
- Stop there. If OLXH171 or OLXH186 appears, note it: that is the risk above.

---------------------------------------------------------------------------------------------------
## Q3. Room chat

### Own line: not printed locally [static]
- Send path:
  - TextChatManager (vt 0x14282af60, global `[0x1436fe210]`) queues the text at +0x90;
  - 0x14108ba30 → **0x14108af80**: stores statement, flag and pid list in ParameterTextChat (`[g+0xb8]`, via
    0x14108b590) and creates `CmdSendTextchat`. **Nothing is added to the log.**
  - CmdSendTextchat has no OnResult slot of its own (vt 0x14284a938 ends at the base slot 26).
- Receive path: `CmdWatchTextchat` OnResult **0x1410ad920** → queue `[g+0xb8]+0x48` {from_pid, name, statement}
  → drain **0x14108b7f0** → AddLog **0x14108aea0** (TextChatManager+0xa0, at most 30 lines).
- The only other AddLog callers are 0x14108b460 (from 0x14108b2a0 / 0x14108b380, the "%s has just joined/left."
  lines 0x3c80000/1) and 0x14108b640 (system lines such as 0x1fa00ea "This feature has been restricted by admin.").
- The window (0x140e979e0) marks a line as "mine" (`+1 = from_pid == own pid`, 0x140e97a5d) and draws it on the
  **right** (0x140ecf780). Lines from others go on the left (0x140ecf6e0).
- **Conclusion:** the server's echo of WATCH_TEXTCHAT to the sender is what shows the sender's own line. It will
  appear **once**. Keep the echo.
- Drop rules in the drain (0x14108b8e6..0x14108b900): from_pid ≠ 0, a non-empty `name` and a non-empty
  `statement` are required. Our handler sends all three.
- All chat (send and display) is blocked when `[ntl+0x10]+0x15a == 0 && +0x158 != 0`
  (0x14108afca / 0x14108b8bf):
  - +0x15a = `chat_probi == "YES"` from SET_CURRENTPLAYER (0x1410f9d49);
  - +0x158 is set elsewhere (platform restriction) [guess].

  If the test shows "This feature has been restricted by admin.", try `chat_probi:"YES"`.

### Opening the chat input
- `menuutility::TextChatWindow` (vt 0x1427e1338), created by TextChatMenuManager 0x140e98060 → 0x140ecf510
  ("MenuOnlineTextChatWindow"). It is shown on the room screen [inferred].
- Its button guide: slot 0 **"Comment"** (0x3c8001a), slot 1 / 10 "Close" (0x6c0078), slot 2 **"Preset"**
  (0x3c80021, only when presets exist).
- Button handlers are vt slots 49+i:
  - slot 49 (`i=0`) → 0x140ed0930 opens the software keyboard (0x140e7b0a0, limit 0x2a = 42 chars) [static; the
    unit is a guess];
  - event 0x1080011 (keyboard OK) → 0x140ed0150 sends.
- On the room-choice menu (vt 0x1427366f8), slot 51 (`i=2`) posts 0x108104d and slot 52 (`i=3`) posts 0x1081049.
  Which of these focuses the chat could not be settled: the events are handled in data-driven menu code. The PC key
  for each slot depends on the player's key settings [guess].

### Live test (two copies, both in one Friendly room)
- On copy 1's room screen, read the on-screen button guide for "Text Chat" / the chat panel.
- Press that key. If there is no guide entry, try the keys for button slots 2 and 3 (pkey.sh: one key, then a
  screenshot).
- In the chat window press Return on **Comment**, type `type:hello`, then Return.
- Proof:
  - server: `CHAT ROOM 3 -> [3, 4]: 'hello'`;
  - copy 1 shows "hello" **once, on the right**; copy 2 shows it on the left.

  If copy 1 shows nothing, the echo was dropped (check `name`).
