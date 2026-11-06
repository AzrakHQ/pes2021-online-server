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
    datacheck.md predicted**; which check at line 556 fires is not read yet. It depends on the same switch.
  - `is_revision_check "NO"` (same two copies, new room): DATASYNC, SET_GAMEMEMBERENV, TEAMSELECT — both reached
    team selection. So a sum mismatch blocks the match only while `is_revision_check` is YES, as datacheck.md Q1 says. (Playing a
    whole match with different data was not tried; datacheck.md warns of desync.)

### In the server itself (branch lobby-relay, 2026-11-06 17:10)
- `pesrooms.py` + `turndtls.py`, no test add-ons, `TURN=1`: copy 1 created a room (room-list ADD, game_env
  UPDATE, player-list reply, ENTRY_GAME, room screen); copy 2 saw "[001]Room testHome" and joined; both pressed
  Start Match; "match 1 starts"; START/END; DTLS legs up in 0.02 s; both relay addresses forwarded; channels
  bound; DATASYNC, TEAMSELECT; the DTLS associations followed the game's port change every 10 s, and the TURN
  list was fetched once (no restart cycle).
