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
  Fix not yet tested live.
- A player-list UPDATE pushed **after** the owner's ENTRY_GAME (21:17:08) did no harm, so the rule is narrower:
  no player-list push between the reply and the owner's ENTRY_GAME.
- **Do not send `owner_pname.psn_user_id` as an empty msgpack list:** a room-list RELOAD in that form, pushed to
  both copies, ended **both** game processes within 0.25 s (same unchecked nested unpack as in roomcrash.md).
