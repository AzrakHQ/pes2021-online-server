# Live tests, 2026-11-05 (two game copies on one PC, test server from the repo code)

Setup: `TURN=1 UPNP=0 python3 -m pesserver host` from `pes2021-private-server/server` (same data folder as the
installed program, so the mod's identity check passes), copies `game/` (pid 1, with the mod) and `game2/` (pid 2,
"Tester2", redirect by netshim), both via `tools/dev-launch.sh`. The relay and the live-update listing were
instrumented at runtime from a scratch launcher (no repo code changed). Tags: [live].

## TURN relay (built-in relay, TURN=1)
- The game accepts our TURN list and certificate. Its client connects over **TLS to tls_port 5349** and
  authenticates with long-term credentials. [live]
- Exact exchange:
  - Allocate `{LIFETIME=60, REQUESTED-TRANSPORT=UDP (0x11), MOBILITY-TICKET (empty = request mobility),
    SOFTWARE "ktc-0.0.0", FINGERPRINT}` → our 401 `{REALM, NONCE}`.
  - Allocate again with `USERNAME "<expiry>:<pid>", REALM, NONCE, MESSAGE-INTEGRITY` → our success
    `{XOR-RELAYED-ADDRESS, XOR-MAPPED-ADDRESS, LIFETIME 60, MOBILITY-TICKET (16 B), MESSAGE-INTEGRITY, FINGERPRINT}`.
  - **About 30 ms later the client closes the TLS connection itself**, sending no Refresh, CreatePermission or
    ChannelBind.
  - About 1.1 s later it asks for CMD_GET_TURN_SERVER_LIST again and repeats, indefinitely (481 connects in one run).
  - CMD_SEND_TURN_ADDRESS_DATA is never sent.
  - Adding a SOFTWARE attribute to our replies changed nothing.
- The match itself ran over the direct P2P path while the TURN loop kept going.
- **Cause: the relay's SOFTWARE attribute must be a "dual route" directive `HOST:PORT,<text>,0`.**
  - Static: Turn::Impl reads the server's SOFTWARE value after allocating and parses it. Anything else, including a
    missing value, restarts the session.
  - Live, with `SOFTWARE = "192.168.1.100:3479,pes,0"` in our Allocate and Refresh successes:
    - the TLS sessions stayed open: no restart loop, a Refresh every 30 s (half of LIFETIME 60), and the session
      stayed alive through a whole match;
    - each game made the **second leg** attempt: three 189-byte UDP packets to the directive's host:port at 0, 1 and
      3 s, apparently a DTLS handshake to TURN over DTLS;
    - with nothing answering there, **neither game published CMD_SEND_TURN_ADDRESS_DATA** (watched for 3+ minutes),
      so the relay was never usable.
  - `HOST:0,pes,0` (port 0) is invalid: straight back to the 1-second restart loop.
  - Allocations from a finished match expire about 60 s after the last Refresh, and the old TLS sessions close when
    the next match starts.
- **What a working relay needs:** keep the TLS listener and also serve **TURN over DTLS on UDP** at the directive's
  port. The game then moves to that leg and publishes its relay address. Not yet built.
- **Second leg, tested with a stock DTLS server** (openssl s_server -dtls on the directive's UDP port, our relay's
  TURN certificate; second session 2026-11-05):
  - The game completes a **DTLS 1.2** handshake: ClientHello, the HelloVerifyRequest cookie exchange, our
    certificate, ECDHE key exchange, Finished.
  - Negotiated cipher: **ECDHE-ECDSA-AES256-GCM-SHA384**, the same as the TLS leg. Groups offered: x25519,
    secp256r1, secp521r1, secp384r1.
  - The certificate from GET_TURN_SERVER_LIST is accepted for this leg too.
  - Afterwards the client sends application-data records (type 0x17, epoch 1, increasing sequence numbers) of
    91 bytes each, about 67 bytes of plaintext. They are resent repeatedly while nothing answers (155 in a few
    minutes).
  - The stock server does not show the decrypted content. Its size is close to the 64-byte Allocate the client
    sends on the TLS leg, so this is presumably TURN over DTLS [inferred].
  - **Relay next step:** a DTLS 1.2 endpoint on the directive's UDP port, with the TURN certificate and cookie
    exchange, that answers the TURN messages inside (the same handling as the TLS leg). Decoding the first
    plaintext record will confirm the protocol.

## Reconnect and outages
- **Server outage during a match** (about 7 s): both games sent **CMD_RECONNECT** `{pid, platform "PC",
  patch_version "1.01.00", dlc_version "1.00", online_mode "QUICK"}`, got `{key, key_size 16}` and carried on into
  the match (GAMELOAD → ENTRY → 1ST). [live]
- **Server outage of about 15 s during a match**: "Connection to server lost. CODE:APLR_0002"
  (OnlineSystemClientReconnect.cpp, sub-code 2). No CMD_RECONNECT reached the new server. [live]
- **Server outage while in the menus**: "Connection to server lost. CODE:VNBP_0002" (OnlineSystemClient.cpp,
  sub-code 2), back to the menu, no reconnect attempt. [live]

## Match end without input (AFK)
- After kick-off (1ST, gtimer 950), **72 s without input** made the idle player send
  `SEND_ABNORMALEND {reason NO_OPERATION, error_code "VIXT2253_0x1a000000"}`. The screen said "This match will be
  called off, as you stopped playing for some time." [live]
- The other player sent `{reason DISCONN_SC, error_code "GNCX_0003_0x20000000"}` and saw "Connection quality with
  <name> has significantly worsened. Canceling match." [live]
- Both sent REQ_GAMEADDPOINT 10 s later and SEND_GAME_RESULT 22 s later (game_phase 1ST, error_code VIXT2253 /
  GNCX_0003). The server's verdict push covers only finished matches, so aborted matches still wait out the 20 s.
  A verdict with any gameend_reason other than NORMAL_END/INJURY would show OLXH186, so this wait can't be avoided
  cleanly. [live + static]

## Live Update (served from our own server)
- Our reply listed one entry `{name, size 1323008, url http://192.168.1.100:8090/livedata_101.cpk, hash <sha256
  lowercase>, dlc_version "101"}` as an inline msgpack map. The game fetched it with **one plain HTTP GET**,
  accepted the hash, saved and applied it. [live]
- Test CPK = `common/etc/version/version_wu.bin` ("1.01" | "1.01.00" | "lu-test", 3×10 B) + an edited
  `common/etc/pesdb/Player.bin` (Messi id 7511: Speed 81 → 82; WESYS header `ff 10 81`).
- The game then showed: "No Live Update changes to Condition Rating. All players' conditions will change each match
  from among the following: [form arrows]". That is its notice for an update without a PlayerWeekly table. [live]
- `JOIN_ONLINE_MODE livedata_version` became **"1.01"** (the first field of version_wu.bin); LOGIN earlier still sent
  "0". [live]
- **CHECK_CHEAT `param_check_sum` = `25D6E9E6091EAC7425E05AF193DA6094`, exactly what pesdb.py predicted** for the
  update in live-data mode. `entry_check_sum` was unchanged (`1E21A82F…`). This confirms that the update's table
  overrides the base data, that live mode adds the form term, and the whole checksum model. [live]
- Saved as `C:\ProgramData\KONAMI\eFootball PES 2021 SEASON UPDATE\save\LIVE00000000`: 1,323,998 bytes (the CPK
  plus 990 bytes), encrypted by the save system (entropy 8.0). It sits in ProgramData, not Documents. [live]

## Menus
- The eFootball menu lists Quick Match, Online Divisions, Online Co-op, Matchday, Online Tournament.
- Kick Off lists Local Match, Local Match [UEFA EURO], Co-op, Random Selection Match, Versus, League, Cup.
- **Correction (later the same day):** the lobbies **are** in the menu. The Kick Off list scrolls, and after Cup come
  Friendly Match Lobby, Team Play Lobby and Training. The first test stopped scrolling at Cup.

## Friendly Match Lobby (second session, test handlers per rooms.md) [live]
- `JOIN_ONLINE_MODE {online_mode "FREE"}`, then SEND_IPANDPORT_NTL, GET_SERVER_ENV, CHECK_CHEAT,
  GET_URGENT_ANNOUNCEMENT (online_mode FREE), GET_MANNER_WARN, GET_PRIVATEINFOTITLELIST.
- `GET_LOBBYLIST {lobby_type "FREE", lang}` → JOIN_LOBBY {lobby_id 1} → GET_LOBBYINFO {lobby_id 1} → GET_BLOCKLIST.
  - A bare NOERR for GET_BLOCKLIST gives "Unable to send or receive data."
  - With `{count 1, bklist [blob{name, player_num, max_player_num, block_id 1}]}` it goes on: JOIN_BLOCK {block_id 1}
    → **REQWATCHL_ROOMLIST is sent as a request** → the "Match Room List" screen ("Set up Match Room",
    "Number of rooms 0").
- Set up Match Room:
  - Room Settings (Room Name "Room <player>", Room Security, Password) → General Match Settings (Match Time 10 min,
    Injuries, Ball Type, Time limit, Overtime, PK, Max. No. of Substitutions) → Next.
  - The game then sends CHECK_STRING {str room name} → **CMD_CREATEJOIN_ROOM** {name, match_type OC_FREE, lang,
    max_players 2, is_invite_limit NO, password "", enable_guest YES, vs_com …} → **CMD_SET_GAMEENV** {game_env:
    blob of cpuLevel, gametime, injury, ball_type, exGame, pkOnOff, substitution, exSubstitution, limitTime} →
    CMD_REQWATCHL_ROOMPLAYERLIST_NTL {room_id}.
- **The game exits** (window gone, no SEH record) after our player-list reply:
  - with `pname.psn_user_id` as an object (as in WATCH_MATCHING): within 1.5 s;
  - with it as an empty list (as the parser types it): the list is accepted, but the game still exits about 2 s
    later, before CMD_ENTRY_GAME;
  - sending the list once instead of twice changed nothing *in that run* (it was before the room-list fix below,
    so the game failed for another reason; see the bisection at the end of this section).
  - Code reading (roomcrash.md): after creating a room the game looks for **its own pid in the room list** (filled
    only from REQWATCHL_ROOMLIST, not from the player list), so the server must push the owner's room into the room
    list before or with the player list. In this parser `pname.psn_user_id` is a nested blob `{account_id,
    online_id, is_valid_cache}`; an empty list is read as garbage memory.
  - **Re-test with both fixes (20:12): the game still exits**, now about 0.2 s after the player list (0.27 s with an
    empty-map blob, about 1.85 s with the list form). *Superseded by the bisection below.*
- **Bisection (20:48–21:20): the exit came from a second copy of the player list.** Our test handler answered
  REQWATCHL_ROOMPLAYERLIST_NTL with the list **and** pushed the same list (RELOAD, rqid 0) 50 ms later. The game
  exits about 0.2 s after that push. Rounds (own room pushed into the room list first in all of them):
  - R1, reply + push, player entry cut to `{pid, is_owner, side}`: still exits, so no single field is to blame.
  - R2, bare `NOERR` reply, no list, no push: no exit. The game sends `CMD_LEAVE_ROOM {error_code "VZLN_0002"}`
    (room creation failed, it found no player list).
  - R3, **reply only** (no push), entry `{pid, is_owner, side}`: the game sends **CMD_ENTRY_GAME {entry FALSE,
    side HOME}** within 0.12 s and shows the room screen "[001]Room <name>".
  - R4, reply only with the full entry (`pid, room_player_id, room_entry_no, game_entry_no, pname` blob with the
    nested `psn_user_id` blob, `lang, is_owner, side, is_quality_bad, has_guest, host_addr, reflexive_addr,
    relayed_addr, ntl_nat_type`): works the same (ENTRY_GAME, room screen, WATCH_ENTRY_GAME accepted).
  - **Rule: answer the owner's REQWATCHL_ROOMPLAYERLIST_NTL request with the list, and do not push the list
    again before the owner's ENTRY_GAME.** Pushes are for later changes (a second player joins, ENTRY_GAME, leave). Why the extra
    copy ends the process is not known yet.
- **Second player's room list (still open):** copy 2 (pid 2) opened the Match Room List while copy 1's room
  existed. Our REQWATCHL_ROOMLIST reply was `RELOAD, list_num 1` with the same room entry copy 1 had accepted
  (`room_id 1, status WAITING, gamer [blob{pid 1, is_room_owner YES, side HOME, …}], owner_pname` blob with the
  nested `psn_user_id` blob, `max_players 2, room_user_count 1, vs_com PL_VS_PL, owner_lang en, …`). The screen
  still showed **"Number of rooms 0"**. Code reading (roomlist.md) found why: the list hides rooms whose
  `gameenv.limitTime` is NOSET, and our test handler had kept the default NOSET because it looked for the
  settings as top-level SET_GAMEENV keys instead of unpacking the `game_env` blob (which said "MIDDLE").
  Fix confirmed live on 2026-11-06 (next section).
- A player-list UPDATE pushed **after** the owner's ENTRY_GAME (21:17:08) did no harm, so the rule is narrower:
  no player-list push between the reply and the owner's ENTRY_GAME.
- **Do not send `owner_pname.psn_user_id` as an empty msgpack list:** a room-list RELOAD in that form, pushed to
  both copies, ended **both** game processes within 0.25 s (same unchecked nested unpack as in roomcrash.md).

## Friendly Match Lobby, whole room flow (2026-11-06) [live]
Test handlers rebuilt with every rule above (own room into the room list after CREATEJOIN_ROOM; player list only as
the reply before the owner's ENTRY_GAME; nested `psn_user_id` blobs; `game_env` unpacked from SET_GAMEENV, echoed
as the room-list `gameenv`, then a room-list UPDATE). Copy 1 = owner (pid 1), copy 2 = Tester2 (pid 2).
- Entering the lobby shows "No new Live Update available." (the mode entry checks Live Update), then the Match
  Room List.
- **Owner:** CREATEJOIN_ROOM → our ADD push → SET_GAMEENV (`game_env` blob: cpuLevel HARDEST, gametime 10, injury
  YES, ball_type 22, exGame NO, pkOnOff NO, substitution 3, exSubstitution YES, limitTime MIDDLE) → our UPDATE push
  → REQWATCHL_ROOMPLAYERLIST_NTL (reply) → ENTRY_GAME {entry FALSE, side HOME} → room screen.
- **Second player's room list: "Number of rooms 1"** with the row "[001]Room Mohamed Rashad", Owner, Capacity 1/2
  (`gamer_num`/`max_players`) and an "EN" tag (`owner_lang "en"`). This confirms the limitTime NOSET filter from
  roomlist.md.
- **Room details (before joining):** selecting the row sends REQWATCHL_ROOMPLAYERLIST_NTL {room_id 1} from a player
  who is not a member; our reply (the room's list) gives the "Match Room Details" screen: Room Security, Language
  English, Match Time 10 min., Injuries On, Time limit **Normal** (= MIDDLE), Overtime Off, PK Off, substitutions
  3 (+1 in Extra Time); Home = the owner, Away = "Accepting Participants", button Enter.
- **Join (Enter):**
  - `JOIN_ROOM_NTL {room_id 1, is_invited "NO", password ""}`; we reply `{room_owner: blob{pid, pname (stock
    _pname), NTL addresses}, room_stat "WAITING"}` and push a room-list UPDATE.
  - The joiner then asks REQWATCHL_ROOMPLAYERLIST_NTL (reply) and sends ENTRY_GAME {entry FALSE, side AWAY}.
  - We push a player-list UPDATE (2 players) to the owner only.
  - The owner then sends `SEND_NOTICE {pid_list [1, 2], data {"notice_key":"ROOM_SHARE_INFO", from_pid 1,
    has_team_data "NO", team_id 0, team_name "", team_power 0, chemistry 0}}` and an empty `SET_GAMEMEMBER`.
    Our handler ignored this SET_GAMEMEMBER (nobody had entered yet) with no visible effect.
  - Both screens: Home Mohamed Rashad, Away Tester2, "You can now start the match by selecting Start Match."
- **"Start Match" is the ready flag:** it sends `ENTRY_GAME {entry TRUE, side}`. After our WATCH_ENTRY_GAME push
  `{entry_count, entrygame [blob{pid, entryNum, gameNum}]}`, that side's "Pre-Match Preparations" OK turns green
  and the owner sees "Please wait for everyone to select [Start Match]".
- **Start:** when the second player also entered, the owner sent `SET_GAMEMEMBER` (empty request) again. We pushed
  `WATCH_DECIDE_GAMEENV {game_env blob}` and `WATCH_ROOMSTATE {event StateChanged, state SETENV, owner_pid 1}` to
  both. Both games then ran the normal match start within 8 s:
  - GET_MATCHID, SEND_SYNC_NOTICE ("1," / "2,"), GET_SERVER_ENV;
  - P2P SYSTEM_NOTICE START/END;
  - CHANGE_GAMEPHASE DATASYNC;
  - the owner's `SET_GAMEMEMBERENV {list_num 2, envlist [blob{pid 1, side HOME, sideLeader YES}, blob{pid 2,
    side AWAY, sideLeader YES}]}` (our server's default reply was enough);
  - CHANGE_GAMEPHASE TEAMSELECT.
  **Both games showed the team selection screen** (Home / Away, a countdown showing 57 s when captured). Stopped there; the match itself is the
  same session as Quick Match.
- Player-list UPDATE pushes after a player's ENTRY_GAME, and to the owner when someone joins, were all harmless.

## Second round, 2026-11-06 afternoon (two copies, test handlers + add-ons outside the repo) [live]
Test server: the repo server plus `tools/lobbytest/` add-ons in the game folder (lobby handlers, `dtlsleg.py` =
TURN-over-DTLS leg, `livedata.py` = switchable Live Update, `HELLO_MAX` to switch the mod's same-math hooks off).
Both copies ran mod 0.4.0 with install ids, so they were new accounts (pid 3 "testHome", pid 4 "TestAway").

### Friendly Match Lobby: a whole match and back to the room
- 10-minute match (Arsenal v Arsenal, autoplay on both): phases 1ST → 1ST_15MIN → 1ST_30MIN → BREAK1 (half
  time, about 1.5 min) → 2ND → 2ND_15MIN → 2ND_30MIN → END, about 29 real minutes in all (15 game minutes ≈ 4.5 real
  minutes with autoplay). No reconnects, no aborts.
- After the "Full Time" stats screen (countdown 148 s, A = Confirm):
  - each game sends HTTP `CMD_SET_PLAYDATA_MATCH` (play_data JSON, `gamemode` 26 = Friendly Match Lobby),
    `CMD_SEND_STATS_DATA` and `CMD_SEND_GAME_RESULT {gameend_reason "NotImplement", game_phase END, side, scores}`;
  - then `CMD_REQWATCHL_ROOMLIST` (a new watch request) and `CMD_LEAVE_GAME {pid}`.
  - The player who confirms first leaves the match session; the other one, still on the stats screen, then
    reports `SEND_ABNORMALEND {reason DISCONN_P2P, error_code GNCX_0003_0x20000000}`, sends its result with
    `error_code GNCX_0003` and sees "This match will be abandoned. There has been a connection problem between you
    and your opponent." (OK → back to the room). Same NOERR / DISCONN_P2P pair as in Quick Match.
  - The first player waits in the room with "Please wait for all users to return to the Main Menu."
  - When both have sent LEAVE_GAME, our `WATCH_ROOMSTATE {StateChanged, WAITING}` push brings both back to
    "You can now start the match by selecting Start Match": a rematch is possible. The room still says
    "0 Match(es)" and Wins 0–0 (the match was a draw; how the count rises is not known yet).
- The room list keeps showing a room whose status is SETENV/GAME (Capacity 2/2). Selecting it gives
  "Could not enter because a match is currently being played." from the client itself (no request sent).

### Duplicate list crash: mechanism confirmed (datacheck.md Q3)
- With both games in team selection we pushed a player-list `UPDATE` to copy 2 (no effect) and, 4 s later, a
  player-list `RELOAD` to copy 1 (its list cache already held the room's players). Copy 1's process ended within
  0.25 s; its Wine log shows `dispatch_exception code=e06d7363 (EXCEPTION_WINE_CXX_EXCEPTION)` right before.
  This is the uncaught `std::out_of_range` from the watch-list merger's RELOAD loop.
- **Rule:** send RELOAD only into an empty client cache (the reply to a new REQWATCHL_* request); every later push
  is ADD / UPDATE / DEL.

### Live Update: the server must keep listing it
- The update is downloaded at boot (one HTTP GET), saved as LIVE00000000 and reported as `livedata_version`. But the
  game asks for `CMD_GET_DLC_FILELIST` again when it enters an online mode (always `pid=0`). **If that second
  list is empty, the game computes CHECK_CHEAT from the base data** (stock sums, no live-mode term), although it
  still reports `livedata_version` "1.01"/"1.02". With the update listed every time, the sums were exactly as
  pesdb.py predicts (`param_check_sum 25D6E9E6091EAC7425E05AF193DA6094`, Messi Speed +1). A reboot with the update
  still listed does not download it again.
- The mod's same-math hooks (0.4.0) have no effect on this (tested with them off).

### Data mismatch (not settled cleanly)
- Copy 1 with the Live Update, copy 2 stock. Both runs failed at the room start, but both were confounded by our
  test handler: a stale room from an earlier session meant copy 2 joined the wrong room (13:55, owner:
  "Unable to connect.") or the owner's SET_GAMEMEMBER crashed the test handler (14:45). The gate's reply to a
  handler crash, `ERR_DATABASE`, shows "An unexpected problem has occurred while communicating with the PES 2021 server.
  CODE:C_DYUN_0013". The handler is fixed (one room per player); the clean repeat is still to do.

### TURN relay: the DTLS leg works (ktcdtls.md)
- The game's TURN client uses stock OpenSSL 1.1.0i for DTLS, wrapped by Konami's ktc layer. That layer:
  - appends 2 bytes after the last record of each application-data datagram it sends and adds 2 to that record's
    length, so a standard DTLS server drops every record;
  - ends every plaintext with a type byte (0x00 = plain message);
  - reads incoming records unchanged, but drops any message whose last plaintext byte is not a known type.
- Server fix (in `dtlsleg.py`): strip the 2 bytes and correct the length before OpenSSL, strip the 0x00 after
  reading, and send replies with `SSL_write(message + b"\x00")`. Do not pad ChannelData on this leg.
- With that, on the DTLS leg each game: Allocate → 401 → authenticated Allocate (LIFETIME 60, MOBILITY-TICKET) →
  Refresh → **closes the TLS connection** (the TLS leg only bootstraps) → `CMD_SEND_TURN_ADDRESS_DATA {address,
  address_data_size 7}` → (our `WATCH_TURN_ADDRESS_DATA` push to the opponent) → ChannelBind to the opponent's
  relayed address → ChannelData both ways (≈295 bytes first, then ≈40-byte packets every 5 s) and a Refresh every
  5 s.
- The address blob is 20 bytes: `00`, the relayed port big-endian, the IPv4 address in little-endian byte order,
  then zeros (`00 c00a 6401a8c0` = 192.168.1.100:49162).
- The match itself ran over the direct path. **Open:** every 21.4 s each game asks for the TURN list again and
  rebuilds both legs (new allocations, new address published). The DTLS-leg Allocate success must not carry the
  dual-route SOFTWARE directive (we fixed that; the cycle stayed).

### TURN restart cycle: fixed (turncycle.md)
- The game's DTLS socket is re-created every 10 s while DSCP is on (`Network.DscpEnable`, default true), so the
  client's UDP source port changes while the DTLS session stays. A relay keyed by (ip, port) stops answering, the
  next Refresh "ping" waits 10 s, and the client restarts TURN: that was the 21.4 s cycle.
- With session roaming in `dtlsleg.py` (a record from an unknown port of a known IP that decrypts with a session's
  keys moves that session and its relayed traffic to the new port), the log showed "session moved" for both
  games every 10.0 s, Refresh answered every 5 s, and no new TURN list request for 70+ s. Fixed.
- Alternative without relay changes: `GET_SERVER_ENV.proto_opt {"P2P":{"Network":{"DscpEnable":false}}}`
  (static; not tested).

### A match forced onto the relay: not yet
- Both games given unreachable P2P addresses (192.0.2.1:9 in the player list and `room_owner`), relay working:
  after SYSTEM_NOTICE START/END no ChannelData moved and about 10 s later the owner left with
  `LEAVE_ROOM {error_code MXNV_0013}` (MXNV476, OnlineModeTaskMatchSession.cpp line 476) and saw C_DYUN_0013.
  So the P2P link-up does not fall back to the TURN route on its own.
- Repeat with `GET_SERVER_ENV.proto_opt {"P2P":{"Network":{"NetworkIoConnectionTimeoutUs":40000000,
  "DscpEnable":false},"Session":{"ConnectionTimeoutUs":30000000}}}`: **proto_opt is honoured** — no DTLS port
  changes at all (DscpEnable false), and the failure moved from ChannelBind + 10 s to ChannelBind + 30.3 s
  (`Session.ConnectionTimeoutUs`). START was sent to the right peer, addresses were exchanged and channels bound
  both ways, but no END and **no ChannelData at all**: the kps session never sent over TURN.
- **Cause and fix (turnlinkup.md):** kps sorts the two `ip:port` labels and only the side with the lower one opens
  the link. Both games saw 192.0.2.1:9 sort below their own 192.168.1.100, so both waited. With the owner shown
  as **192.0.2.1:9** and the guest as **203.0.113.1:9** (host_addr and reflexive_addr, in the player list and
  JOIN_ROOM_NTL), the owner sent 14 bytes of ChannelData right after the binds, got 270 back (the key
  exchange), and **the match ran entirely over our relay**: DATASYNC, TEAMSELECT, ENTRY, 1ST (kick-off,
  played), 10,000+ relayed packets, no END notice, no direct game-to-game UDP. **A relay-only match works.**
- Consequence for real matches (inferred): the same label rule can leave both players waiting between homes
  (P2P or relay); the server should check the order of the labels it hands out and fix it when both would wait.

### Online Co-op, 1 vs 1 (misc.md §1/§3, coop2.md)
- Menu: eFootball → Online Co-op → Casual Match / Clan Match / CO-OP Stats / CO-OP Settings (+ "Co-op Room
  List"). Entry: JOIN_ONLINE_MODE COOP, the usual mode-entry commands, GET_CLAN_LIST, GET_COOP_PROFILE,
  GET_COOP_REJECT_LIST, GET_COOP_MAINMENU_INFO, GET_IS_JOIN_COOP_ROOM. Casual Match → GET_COOP_QUICKMATCHOPTION →
  "Matchmaking Settings" (VS Settings 3 vs 3, Match Level, Guest User; locked at co-op level 1) →
  SET_COOP_QUICKMATCHOPTION.
- **SYNC_PROGRESS must be echoed:** after "Go to Matchmaking" each copy sends `SEND_NOTICE {pid_list:[self],
  data {"notice_key":"SYNC_PROGRESS","pid_list":[self]}}` and waits up to 30 s for its own group's members in a
  WATCH_NOTICE. Our 1v1 relay never sends a notice back to its sender; with an echo the copy continues with
  `START_COOP_MATCHING {matching_type ONLINE_COOP, coop_player_info_list [blob{pid, is_good_reputation,
  guest_kind, user_id blob{pid, pname}, owner_pid}], use_team_id 262143, retry_num 0}`.
- After our WATCH_COOP_MATCHING FINISH push (two solo groups), both copies start the session:
  - with GET_GAMERELAY `game_sycom ""`: CreateMultiplaySession fails (`SEND_MATCHING_RESULT error_code
    MXNV_0010`, pid 0 and zero addresses). Co-op takes its network type from `game_sycom` ("P2P" = 1);
    Quick Match forces type 1 itself.
  - with `game_sycom "P2P"`: SEND_SYNC_NOTICE, the TURN list and SEND_TURN_ADDRESS_DATA follow, but
    `SEND_SYSTEM_NOTICE {pid_list [], data "START:pid=0;…"}`, SET_GAMERELAY_CHECK_RES `ER_CONNECT` after 30 s
    and `LEAVE_ROOM MXNV_0013`.
  - Cause (coop2.md, static): the peer list is right, but co-op never calls the function that hands it to the
    P2P layer (0x141092720, called only by Quick Match and the lobby room), so NtlImpl has no own pid and no
    peers. It also passes at most one peer, so P2P could carry 1 v 1 co-op at most; 2v2/3v3 used Konami's game
    relay ("SC"). **Server-side fix: none; it needs a mod patch** (call 0x141092720 after 0x141134c00 at
    0x1411346c3, and copy the peer addresses in 0x14110fe00).

### Dialogs photographed (misc.md §3 list; the error was forced on one copy)
| Reply | On screen | Then |
|---|---|---|
| JOIN_ONLINE_MODE → ERR_DIFFERENT_PATCHVERSION | "Your version of PES 2021 is out of date. Please update to the latest version and reconnect." | main menu |
| CHECK_CHEAT → ERR_PARAMCHECK_FAILED | "Invalid player data found.", then a second dialog "Unable to load content data. The data may be corrupted, or conditions for use may not have been met. CODE:CCWR_0014" | main menu |
| GET_LOBBYLIST → ERR_MODE_BLOCKED | "Some of our online services are currently unavailable. Please refer to Online Information for further details." | main menu |
| JOIN_LOBBY → ERR_LOBBYUSERMAX | "Cannot participate due to full quota." | main menu |
| GET_BLOCKLIST → ERR_DATABASE | "An error occurred while accessing the PES 2021 database." | main menu |
| CREATEJOIN_ROOM → ERR_ROOMCREATELIMIT | "You cannot create any more Match Rooms." | back to Match Room Settings |
| GET_MANNER_WARN `warn_kind A_TO_B` | "Your courtesy rating has fallen to "B". Please ensure that the number of times you disconnect does not increase any further. Users with a "B" courtesy rating are subject to the following restrictions. · You will be more likely to be paired with users who have "B" or "C" courtesy ratings. · If your courtesy rating remains at "B" for some time, it will eventually fall to "C", thereby making you subject to further restrictions." | continues into the lobby |
| LOGIN → ERR_INVALID_ACCOUNT | "It has been noted that you have acted in direct breach of our Operation Policy and Terms of Use. Therefore, we have suspended your account in line with our policy on violations. Thank you for your understanding." | main menu, offline (the online menus are gone) |
| SET_GAMEMEMBER → ERR_DATABASE (our test handler crashed; the gate answers a handler crash with ERR_DATABASE) | "An unexpected problem has occurred while communicating with the PES 2021 server. CODE:C_DYUN_0013" | room |
- `{re}`/`{rf}` show as "PES 2021" in these texts, as misc.md says.

### Data mismatch, clean repeat
- Fresh server, one room. Copy 1 with the Live Update (`param_check_sum …DA6094`), copy 2 stock (`…DBAE73`):
  - `is_revision_check` absent (= YES): the room start ran GET_MATCHID … SYSTEM_NOTICE START/END, then both
    copies sent `LEAVE_ROOM {error_code GNCX_0006}` 2 s later and showed "Unable to connect." GNCX_0006 is
    GNCX556 (OnlineModeMultiplay.cpp line 556, fn 0x1410001f0), **not the MXNV_0017 "DATAPACK error" that
    datacheck.md predicted**. Code reading later (gncx_labels.md): it is the same sum compare, reported by the multiplay object one tick before the match task. It depends on the same switch.
  - `is_revision_check "NO"` (same two copies, new room): DATASYNC, SET_GAMEMEMBERENV, TEAMSELECT — both reached
    team selection. So a sum mismatch blocks the match only while `is_revision_check` is YES, as datacheck.md Q1 says. (Playing a
    whole match with different data was not tried; datacheck.md warns of desync.)

### In the server itself (branch lobby-relay, 2026-11-06 17:10)
- `pesrooms.py` + `turndtls.py`, no test add-ons, `TURN=1`: copy 1 created a room (room-list ADD, game_env
  UPDATE, player-list reply, ENTRY_GAME, room screen); copy 2 saw "[001]Room testHome" and joined; both pressed
  Start Match; "match 1 starts"; START/END; DTLS legs up in 0.02 s; both relay addresses forwarded; channels
  bound; DATASYNC, TEAMSELECT; the DTLS associations followed the game's port change every 10 s, and the TURN
  list was fetched once (no restart cycle).

### Online Co-op fixed: mod 0.5.0 + pescoop.py (2026-11-06 evening) [live]
- Mod 0.5.0 hooks the co-op peer-list builder (0x141134c00: afterwards it calls 0x141092720, as Quick Match does)
  and the WATCH_COOP_MATCHING handler (0x14110fe00: afterwards it copies each member's host/reflexive address and
  NAT type from the parsed reply into the member record). Only on the exact 1.01.00 code; the id line says
  `coop1`. Mod log: "co-op: addresses of 2 of 2 member(s) kept", "co-op: 1 peer(s) handed to the P2P layer".
- Server (`pescoop.py`, branch coop-mod): force_alone matching of two `coop1` players with equal game data,
  the WATCH_COOP_MATCHING push, a room (GET_GAMERELAY says "P2P" for it), SYNC_PROGRESS echoed to its sender.
- Result: START:pid=<peer> / END both ways (direct link up), SET_GAMERELAY_CHECK_RES NOERR, DATASYNC,
  GET_COOP_MATCH_DATA, MATCHMENU, GAMELOAD, ENTRY, 1ST — **an Online Co-op Casual Match kicked off and played**
  (testHome + COM2 + COM3 v TestAway + COM5 + COM6). Repeated on main after the lobby/relay merge (relay on).
- With `use_team_id` 262143 ("no team") on both sides the match menu showed blank crests and both games crashed
  in GAMELOAD (access violation writing address 8 at 0x1403db255). Co-op has no team-select screen; the server
  must assign each side a real team (pescoop picks two from the Quick Match team list).
### Server features checked live (branch lobby-relay, 2026-11-06 18:05–18:20)
- **Live Update from the data folder** (`liveupdate.py`): a CPK in `<data>/liveupdate/` was listed with
  dlc_version "101" and fetched with one HTTP GET from `/liveupdate/<name>`, then applied (the "No Live Update
  changes to Condition Rating" notice); the game's CHECK_CHEAT then reported the updated sum. The first try
  failed with **"Communication error CODE:SGJC_0007_0"** (LiveDataSetFlow line 809, download error): the URL
  carried the server's *public* address because HTTP commands have no gate session, and a game on the host's
  own network can't reach the public address. Fixed by building the URL from the address the HTTP request
  came in on. An update is also downloaded on entering an online mode, not only at boot.
- **Room join with different data**: owner with the update, friend stock → JOIN_ROOM_NTL `ERR_DIFFDCVERSION`
  → the friend sees **"Data Pack Incompatible."** and stays in the room list (instead of "Unable to connect."
  two seconds after kick-off).
- Room chat (SEND_TEXTCHAT relay) is built but was not tried live (typing in the chat box was not driven).

## Third round, 2026-11-06 evening (main's server + `tools/testserver/spectest.py`) [live]

### Spectating: record a match, watch it from storage (spectate2.md)
- **Recording.** With GET_MATCHID `upload "YES"` both copies sent `START_SEND_GAME_SEQUENCE_DATA {pid}` right after
  GET_MATCHID, then `SEND_GAME_SEQUENCE_DATA` about every 5 s through DATASYNC, TEAMSELECT and play. Each message
  is 3 kB or more and arrives as 0x70–0x72 fragments (3 frames, ~3,040 bytes). The first one held 115 records,
  types 0 (header) and 2, starting `00 00 00 00 27 74 …`. The server kept the first uploader's stream (35 chunks, about 3 minutes in all, kick-off and 3 minutes of play) and dropped the other copy's 33 messages. The stream was closed
  when the other copy quit (SEND_ABNORMALEND), with `03 00` + `END_OF_DATA` appended by the server.
- **Viewer path, server replies only (no game-file change):** eFootball → **Online Tournament** →
  JOIN_ONLINE_MODE / JOIN_PESLEAGUE_COMPE_MODE / GET_PESLEAGUE_MAINMENU_INFO / GET_PESLEAGUE_COMPE_LIST (all with
  `mode`/`online_mode` "PESLEAGUE") → our competition card "Friends Replays · 1 vs 1 · Eligible" → "Enter this
  Competition. Proceed?" Yes → GET_PESLEAGUE_COMPE_INFO → a notice that ratings are made uniform → team select
  with the national teams we listed (France chosen: team_id 8) → "Enter FRANCE into Competition. Proceed?" Yes →
  JOIN_PESLEAGUE_COMPE, GET_PESLEAGUE_1VS1_MAINMENU_INFO.
  - **Correction:** with the competition fields only at the top level of COMPE_INFO, the game opened the
    "eFootball League" 1v1 menu ("Calculating final competition results…"). With the same fields also inside a
    `compe_data` blob, it went to the **PES League lobby**: GET_LOBBYLIST `lobby_type "PESLEAGUE"`, JOIN_LOBBY,
    GET_BLOCKLIST, a first GET_WATCHING_MATCH_LIST, REQWATCHL_ROOMLIST → Match Room List with "X Match List".
  - X → GET_WATCHING_MATCH_LIST {match_category "PESLEAGUE_LOBBY", compe_id 1, index 0} → "Match List": our
    entry "Player2 0 - 0 Player3", Brazil v France flags, "In Progress" → A → "Spectate Match / User List".
- **Watching:** Spectate Match → GET_SERVER_ENV → **HTTP GET `<WATCHING_GAME>/<match_id>/1.enc`, 2.enc, … every
  0.2 s** until the last chunk (the URL id is the match_id from the list) → the stadium → **the recorded match
  replays**: kick-off with name tags "Player 2"/"Player 3" from our list, then live play (clock running).
  Replay from storage works with two copies; the chunk format (IV + AES-256-CBC, flag byte 0, items, END_OF_DATA)
  is confirmed.

### Live Update: team data (Team.bin + PlayerAssignment.bin) is reloaded
- A Live Update with FC Barcelona's short name "BAR" → "BRX" (Team.bin), Messi's FCB shirt 10 → 11
  (PlayerAssignment.bin) and Messi Speed +1: in an offline Local Match FC Barcelona v Anderlecht the scoreboard
  shows **"BRX 0 0 AND"**. CHECK_CHEAT afterwards: `entry_check_sum 1E21A8301DFF00C9DBE591735FF8746A` — **exactly
  the pesdb.py prediction** (stock …A82F…), `param_check_sum 25D6E9E6…6094` as predicted. So Team.bin and
  PlayerAssignment.bin from a Live Update override the base data, and the entry checksum model is confirmed.
- A second update (Speed +3…+15 for the 11 Arsenal starters): `param_check_sum 25D6EA22091EAC5325E05ADB93DA60E7`,
  exactly as predicted.

### Login "server full"
- LOGIN → `ERR_FREE_TO_PLAY_FULL`: "Due to excessive traffic on the PES 2021 server, login is currently restricted.
  Please try reconnecting again later."

### A match with different player data (check off): no desync seen
- Copy 1 on a Live Update with the 11 Arsenal starters 3–15 Speed faster (sum `…60E7`), copy 2 stock (`…AE73`),
  `GET_SERVER_ENV.is_revision_check "NO"`, Friendly Match Lobby, Arsenal v Arsenal, autoplay on both.
- The match ran normally through 1ST and **1ST_15MIN** (same gtimer 12750 from both copies); no SYNC_FAILED,
  no abnormal end. The two minimaps, captured at the same moment at about 16:00, show **every player in the same
  place**. Stopped there (gncx_labels.md's stop point).
- So the match simulation does not visibly diverge with different player stats over 16 minutes. Most likely the
  team data is exchanged at DATASYNC (TaskSyncTeamData), so both sides simulate with the same data
  [inferred]; the CHECK_CHEAT sums (and the server refusing mismatched friends) remain the only guard that
  friends play with the same data, and "check off" is a fairness question, not a crash.
### Online Divisions menu and search (2026-11-06 19:12–19:20, modes2.md)
- Bare GET_DIVISION_INFO: the season-end path runs every time (GET_DIVISION_INFO ×2, FINISH_SEASON, "Season
  Results", "You have been promoted to Division 11.", then an empty screen after GET_DIVISION_INFO
  `is_season_end:"YES"`). So today Online Divisions is unusable.
- With a `division_info` blob (D10, `other_match_num` 10, one history entry of 3 points): the Online Divisions
  screen shows Division 10, the D9 line at 12, 3 points, "W 1-0" and the opponent crest from `enemy_team_id`.
- "To Next Match": GET_OFTEN_USED_TEAM_LIST → team select ("Season Points Info", "Frequently Selected Teams") →
  GET_QUICKMATCHOPTION → Matchmaking Settings → START_MATCHING `matching_type "OC_RANK"` from both copies →
  paired by the Quick Match code → "View Opponent: Connection status with opponent. Proceed?" (Find Another /
  Proceed). Stopped there.

### Online Divisions in the server (pesdivisions.py, 2026-11-06 19:36–20:20)
- New player: "Division 10. This is the first of 10 games this season. You will need to obtain at least 0
  Season Points to stay in the division. Get 12 or more Season Points to earn a promotion." then the empty
  Divisions chart (D9 line at 12).
- Two copies: To Next Match → team select → START_MATCHING OC_RANK → paired (Divisions and Quick Match players
  are now separate queues) → View Opponent → Proceed → a full 10-minute match (0-0).
- At END the server recorded a draw (+1) for both and pushed the verdict to both: both showed **"Changes in
  rating"** (1000 > 1000), a screen Quick Match never shows. Then GET_DIVISION_INFO ×3 and the Divisions chart
  with the first match **"D 0-0", 1 point**.
- Open: the history shows no crest (`enemy_team_id` 0: the server doesn't learn the opponent's team); a
  season end (10 matches) with FINISH_SEASON and promotion is covered by unit tests only.

### Matchday in the server (pesmatchday.py, 2026-11-06 20:45–22:07, modes2.md §C, matchday2.md)
- eFootball → Matchday: JOIN_MATCHDAY_MODE → GET_MATCHDAY_LIST → the event card ("Friends Matchday", "Group Match
  Stage ends in: 30d0h", "Eligible") → "Enter this Competition. Proceed?" → GET_MATCHDAY_INFO → side choice →
  team (only the side's leagues) → "You cannot change your Side/Team later" → JOIN_MATCHDAY {compe_id,
  entry_matchday_group, team_id, sp_gameplan_data, sp_member_list} → GET_MATCHDAY_SP_GAMEPLAN → GET_MATCHDAY_INFO →
  GET_MATCHDAY_MAINMENU_INFO → the Matchday menu (Start Match, Game Plan, Side Ranking, Competition Details, the
  Home/Away bar, Event Points).
- Bugs found on the way: the event map must be one level down in `compe_info.info_overview` (flat keys are
  ignored → compe_id 0 and "The Group Match Stage has ended."); a guessed `all_reward_info` blob crashed the game
  (it is an array read without a type check); an empty GET_MATCHDAY_SP_GAMEPLAN left the line-ups empty and both
  games crashed in GAMELOAD (access violation 0x141afae35).
- Start Match on both → START_MATCHING `matching_type "MATCHDAY"` → paired → full match (0-0) → verdict at END →
  GET_MATCHDAY_ACHIEVEMENT_REWARD → "Match Rewards: 1 Points" → the menu shows Home 1 – Away 1, Event Points 1.
- Also new: SET_GAMETEAM (both team ids, sent at the match menu) is stored, so the Online Divisions history shows
  the opponent's team.

## 2026-11-07: courtesy warnings, co-op profile layout, player messages (probe tools, one copy)

Run with `tools/probesession.py up 1`, `tools/scenario.py` (scenarios/courtesy.json, courtesy_real.json,
messages.json, private_popup.json) and `tools/parsebench.py`. No key presses except the player name.
- **Courtesy warnings:** all four `warn_kind` dialogs shown and read (menus.md table). DOWN_C is "…remains
  high", STILL_C is "…has fallen to C". OK continues into the Friendly Match Lobby. With the server's own
  rule (pesmanner.py): one early exit gave DOWN_B at the online login, and a second one gave A_TO_B on the next
  lobby entry, shown once each.
- **GET_COOP_PROFILE layout** (parsebench, four candidate placements in one reply): only a **top-level**
  `coop_profile_data` blob is read, beside `user_info{pid, pname}`. The KB table had it inside user_info.
  GET_COOP_REJECT_LIST: `reject_list` blobs {pid, pname} are read (2 entries, 0xb0 bytes each).
- **Player messages:**
  - A PERSONAL_INFO item in GET_PRIVATEINFOTITLELIST pops up once (MenuInformationNoticeTask) at login or on
    entering an online mode, right after the box before Quick Match (MenuUrgentAnnouncement).
  - Once closed, it never popped up again, even after a game restart.
  - Server change: player messages and the kick note go through the private list only (the box gave a
    duplicate).
- **Tools:** pesflow now closes both message windows like dialogs (vtable +0x188). Escape is the game's back
  key once no window is open. `scenario.py` got `relaunch`, `"since": "variant"` and `reply … "ok": true`.

## 2026-11-07 (later): Divisions season end, lobby notice, two-copy Quick Match, idle end

- **Side-by-side test server** (tools/probesession.py, default): the host's own server keeps running.
  - The test server uses ports 30000 (gate), 28080 (web), 28443, 23478, 25349/23479/25350 (TURN), 27600, and its
    own data folder `<game folder>/testdata` (own players, identity and admin password).
  - netshim (`NETSHIM_PORTMAP`) moves the game's fixed ports there.
  - Only the game's STUN probe (UDP sendto) still reaches port 3478 of the host's server. It is harmless: it only
    tells the game its own address.
- **Divisions season end:** the game picks promotion / relegation / stay itself, from the points and thresholds of
  the reply before FINISH_SEASON. The rules match the server's, and FINISH_SEASON comes before the is_season_end
  YES request. Details in modes2.md.
- **Lobby notice** (GET_LOBBYINFO has_info YES): a notice window with title and two paragraphs (rooms.md).
- **Quick Match without key guessing:**
  - `pesflow go quick` → (dialogs) → QuickTeamSelect.
  - Return ×2 (team, then confirm) → QuickMatchSettings → Return → QuickSearching (START_MATCHING). The server
    paired the two copies at once.
  - Online/Quick/QuickMatchMenu (Kick Off highlighted) → Return on both → GAMELOAD → ENTRY → 1ST, about 50 s.
  - Vtable +0x188 does nothing on the team select (base no-op 0x140e3eec0), so keys stay for these three screens.
- **Idle end:** both copies idle → both report NO_OPERATION 39 s after kick-off (match.md). Closing copy 2's game
  afterwards was not counted against it (pesmanner fixed so every self-reported idle counts and later drops don't).
- **Information list:** main-menu item 0x19 opens "Online Information". A message the host sent to the player
  (private list) is listed there with its date and can be opened again.
- **Avatars:** a served test pattern showed in Edit Profile. An edit in the avatar editor uploaded with
  SET_AVATAR (49152 bytes) and came back unchanged. The other copy's Matchmaking screen showed it (menus.md).
- **Profiles:** SET_PLAYERPROFILE carries a `profile` blob. GET_PLAYERINFO's top-level favoriteTeam /
  favoritePlayer / intro fill Edit Profile (menus.md).
- **Fixed on the way:** the service never loaded pesavatar (its import line had changed under a merge). Now a test
  checks that every feature module is imported there.
- **Profiles of others + block list:** Match Block List lists the blocked player. User Details shows their
  team, player and comment and the **Courtesy Rating** from the server's rating. Number of Disconnections
  ignores every value tried. `manner` sent as a string crashed the game; it must be a blob (menus.md).
- **Match replays through the real server (pesreplay.py):**
  - A Quick Match between the two copies was recorded: 26 chunks, 132 KB, closed by the idle end.
  - Online Tournament → "Match Replays" card → Yes → team (France) → Yes → PES League lobby (Match Room List,
    "X Match List" = the **a** key).
  - The Match List showed "probe1 0 - 0 robe2", Arsenal v Arsenal (from SET_GAMETEAM). A → Spectate Match → the
    game fetched all 26 `/r/<match_id>/N.enc` → the stadium (Emirates) → the replay.
  - At its end the game showed "An unexpected problem has occurred while communicating with the PES 2021 server.
    CODE:GNCX_0003", probably because the recorded match itself ended abnormally (idle). A replay of a match that
    reaches full time has not been watched yet.
- **Match history:** "Last 20 results" lists our stored matches. Participating Users → opponent → Add to Match
  Block List works (menus.md).
- **Rankings:** Online Stats → Overall Ranked Match Rankings lists the server's players with ratings, plus a
  User Information panel (results.md).
- **Own profile** (Online Stats → User Details): Courtesy Rating B and Number of Disconnections "Moderately
  High", from probe1's two idle exits. The SEMI_HIGH_RATE label is confirmed.
- **Tools lesson:** stopping tools/autoplay.sh mid-press left an arrow key held down, and every later key press to
  the games was ignored. Running `xdotool keyup Right Up Left Down a s d w` fixed it; autoplay now releases its keys
  on exit. Autoplay also never took the kick-off in today's Quick Match (both copies stayed at the kick-off
  position until the idle end); not looked into yet.
- **First complete unattended match** (Quick Match, two copies, no input, `pesflow noafk` on both):
  - Timeline: kick-off 19:58:38 → BREAK1 20:03:39 → a 60 s half-time menu that continued by itself → 2ND
    20:05:30 → END 20:10:30, so about 13 minutes in all.
  - After END:
    - the recording closed with the game's own end item (196 chunks, 816 KB);
    - the full-time screens counted down by themselves;
    - SEND_GAME_RESULT arrived from both (NOERR/END, and DISCONN_P2P/END from the second leaver), with team ids;
    - the match was saved as a normal finish, and both players got a completed match on their courtesy record.
  - The replay of this full match played with name tags (watched for about 2 minutes). Its end was not
    watched, because `probesession restart` relaunches every game copy.
  - Autoplay's key presses do not count as activity: with them the match was still called off after 39 s.
- **The idle trigger:** 0x14213cfe0 sets the flag, reports VIXT2253 and ends the match (match.md). `pesflow noafk`
  makes it return at once, in test copies only.
- **A full-match replay plays to the end:** the Online Divisions match (197 chunks) replayed from the Match List
  through both halves to the 90:00 "Full Time" result screen with player ratings, with no error (21:05 → 21:25).
  The earlier GNCX_0003 at the end of a replay came from a recording of an abandoned match.
- **Why keys never moved anyone in a match** [static, agent; fix live]:
  - The idle check reads only the player's own controller devices.
  - A real key press on the title screen makes the input manager take that device as the player's
    (0x140a9a6c0 → `0x141b52a60(1)`). `pesflow title` called the title's press function directly, which
    skipped that step, so the player had no controller: menus took keys, matches ignored them.
  - `pesflow title` now calls 0x141b52a60(1) first (`pesflow PORT bindpad` does it alone); 0x141b52380 went 0 → 1.
- **Online Co-op "1+1 vs COM"** [static, agent, not run]: one push after START_COOP_VSCOM_OPPONENT,
  `CMD_WATCH_COOP_VSCOM_OPPONENT {result, owner_pid, my_team: blob{base_team_id}, com_team: blob{side, base_team_id,
  com_level, pid: 4294967295}}` (pid as unsigned 0xFFFFFFFF; the game then builds the CPU team itself). It needs 2
  humans in one group, which the current one-peer co-op can't connect.
- **Real input in a match:** with the controller bound, autoplay's keys count as play. No idle end happened, and
  a full match with real play ran 28 minutes (kick-off 21:29 → END 21:57), 0-0. The recording was 457 chunks.
  Random key presses never scored, so goals through the server are still unseen.
- **Forfeit, live** (pesmatch.forfeit): in an Online Divisions match, copy 2's game was closed 60 s after kick-off.
  - 15 s after its connection dropped the server logged: "DIVISION 1: WIN 3-0 vs 2 (+3)", "DIVISION 2: LOSE",
    ratings 1000→1016 / 1000→984.
  - Copy 1 saw a "connection" dialog (DialogProceed), then the Divisions main menu with match 2 as "W 3 - 0"
    and 4 points.
  - The rule: the one player who left early (own GIVEUP / EXIT / NO_OPERATION report, or a dropped connection
    while the match ran) loses. The other wins 3-0, or with the real score if already ahead. Both idle → no winner.
- quickmatch.py: on the Divisions main menu, "To Next Match" needs a moment after the season dialog; the script
  retries.
- **Quick Match ends cleanly when the server pushes the verdict at full time** (2026-11-07, now the default for
  every one-on-one match):
  - Before, the first player to leave the result screens cut the link. The other game then reported
    `SEND_ABNORMALEND {DISCONN_P2P, GNCX_0003}` and showed "This match will be abandoned. There has been a
    connection problem between you and your opponent."
  - With WATCH_GAMEADDPOINT pushed to both at END: no SEND_ABNORMALEND, both SEND_GAME_RESULT `NORMAL_END` with an
    empty error_code, LEAVE_ROOM from both, and both copies went straight back to Matchmaking Settings.
  - Ratings moved by the draw: 1016→1015, 984→985.
  - Quick Match has no rating screen, so the push is never visible; it only ends the games' wait for it.
- **A goal has reached the server before** (server-prev2.log, 2026-11-04): ADD_SCORE {goal_side AWAY,
  goal_player_pid 2, goal_gameplayer_id_info…} from both games, 6 ms apart. That log ends before the match did.

## 2026-11-08: review fixes checked live
- Code review of the 2026-11-07 server work (8 findings) fixed in one commit. Live, in a Quick Match with copy 2's
  game closed 60 s after kick-off:
  - the replay recording closed at the disconnect ("closed (disconnect): 29 chunks"; before, it stayed open);
  - FORFEIT 15 s later (ratings 1012→1027 / 988→973);
  - the saved result (pes_matches) was fixed up to 3-0 FORFEIT, with left_pid = the leaver, after the games' own
    SEND_GAME_RESULT (0-0, DISCONN_P2P) arrived.
- Also fixed (unit-tested):
  - no normal verdict while a forfeit is pending;
  - deleting a player clears their Divisions/Matchday rows and the "left early" marks, and the forced
    disconnect is not counted as leaving;
  - handlers use the session's pid before a pid in the request (private messages, courtesy warnings);
  - block lists only take real players, at most 400;
  - a replay chunk pruned mid-download is a 404.
