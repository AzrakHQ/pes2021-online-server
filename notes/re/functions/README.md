# Online functions: full reports

Static reverse engineering of every online function of PES 2021 1.01.00 (Steam), 2026-11-05. The readable
summary is [docs/functions.html](../../../docs/functions.html); these files keep every detail and address.

| File | Area |
|---|---|
| [net.md](net.md) | NAT traversal, TURN relay (ktc client, TLS), game relay, SEND/WATCH_TURN_ADDRESS_DATA |
| [match.md](match.md) | match-session state machine, peer notices, game queue, abnormal ends, GET_SERVER_ENV, reconnect |
| [results.md](results.md) | GAME_RESULT / STATS / PLAYDATA_MATCH, the WATCH_GAMEADDPOINT verdict, divisions, rankings |
| [rooms.md](rooms.md) | Free / MOM lobby, blocks, room lists, room flow, implementation spec for `pesrooms.py` |
| [coop.md](coop.md), [spectate.md](spectate.md) | co-op matching, party rooms, post-match; spectating upload and viewer stream |
| [menus.md](menus.md) | CHECK_CHEAT checksums, tamper report, news/announcements, profile, store |
| [wire.md](wire.md) | fragment frames 0x70–0x72, AES tier, kps::dlcp, ntl STUN extensions, VDPCHAT |
| [db.md](db.md) | game database: Player / Team / PlayerAssignment formats, the 108 parameters, login checksums, saves |
| [live.md](live.md) | Live Update / Variation / Extra downloads: CMD_GET_DLC_FILELIST, HTTP GET, SHA-256, version files, publishing |
| [nettune.md](nettune.md) | `proto_opt` tuning tree (all link timeouts/thresholds), P2P↔TURN failover rules, ntl_nat_type bits, NAT test |
| [messages.md](messages.md) | how message ids map to text, the server-error dialog table, on-screen code formats, useful replies |
| [sweep.md](sweep.md) | leftover unknowns settled: rating-screen modes, division/sort/mode enums, revision check, version gates |
| [lobby.md](lobby.md) | where the room lobbies are in the menu (Kick Off list), how the menu table is built, other entry points |
| [roomcrash.md](roomcrash.md) | room creation requirements: own room in the room list first, player-list field types, no duplicate player-list push (solved live) |
| [roomlist.md](roomlist.md) | room-list entry: field types, where each is stored, every filter; why a room shows as "0 rooms" (limitTime NOSET) |
| [spectate2.md](spectate2.md) | spectating with two copies: recording, the PES League lobby path to the watch list, replay from storage (works live); field lists in [peslg_path.md](peslg_path.md) |
| [gncx_labels.md](gncx_labels.md) | GNCX_0006 = the DATAPACK compare; desync path (SYNC_FAILED); the address-label rule that picks the active side, and the deadlock risk in our reflexive-address rewrite |
| [turnlinkup.md](turnlinkup.md) | how the match link-up uses TURN; the address-label rule that decides which side opens the link (relay-only match works) |
| [coop2.md](coop2.md) | why Online Co-op stopped after matching (peer list never handed to the P2P layer); fixed by mod 0.5.0 + pescoop.py |
| [turncycle.md](turncycle.md) | why TURN restarted every 21 s (DTLS socket re-created every 10 s) and the fix |
| [matchday2.md](matchday2.md) | Matchday reply layouts (compe_info.info_overview, all_reward_info array, mainmenu_info) and the squad round trip |
| [modes2.md](modes2.md) | Online Divisions (season = 10 matches, division_info blob, OC_RANK), events (no own menu), Matchday (group-stage recipe) |
| [lobby2.md](lobby2.md) | Team Play Lobby (two humans start, but it only connects through Konami's game relay), the room counters (verdict push), room chat |
| [livetests.md](livetests.md) | live tests 2026-11-05/06: TURN (DTLS leg working), Live Update end to end (keep listing it), reconnect, AFK, Friendly Match Lobby (a whole match and back to the room), duplicate-list crash confirmed |
| [datacheck.md](datacheck.md) | DATAPACK check between peers (what must match), match-layer timers, why a RELOAD push crashes the client |
| [dbgaps.md](dbgaps.md) | remaining Player/Team/PlayerAssignment fields, save container and key, Live Update reload details |
| [ktcdtls.md](ktcdtls.md) | the TURN client's DTLS framing (2-byte trailer, type byte) and what a relay must send |
| [misc.md](misc.md) | co-op group rules and 1v1 co-op recipe, spectating data format, dialog macros, match datagram header decoding |
| [survey.md](survey.md) | catalogue counts, error codes, myClub / clans / PES League / Matchday / events |

Tags in the reports: **[static]** read in code at the cited address, **[log]** seen in our traffic,
**[inferred]**, **[guess]**.

## Data
- `data/commands.json` — all 433 commands: area, transport, request keys, reply fields, our status.
- `data/error_sites.json` — all 186 error-report sites: on-screen code, file, line, sub-code, nearby message.
- `data/player_param_map.json` — file bit position of each of the 108 player parameters.
- `data/server_errors.tsv` — all 764 (command, ERR_*) server replies with the dialog kind and the English text shown.
- `data/proto_opt_example.json` — an example `GET_SERVER_ENV.proto_opt` tuning tree.
- `data/server_error_table.txt` — the client's table of (command, ERR_* the server may return, message id).

## Tools (`tools/`, need `capstone` and `numpy`)
- `pe.py` — PE sections, RTTI vtables, disassembly, function bounds (`PES_EXE` = path to PES2021.exe).
- `kb.py` — `build` (cached xref index, ~10 s, ~1.3 GB peak, written to `tools/xref_cache/`),
  `cmd CMD_X` (request keys, reply fields, users of the command), `fn`, `refs`, `callers`, `str`, `rtti`,
  `disp 0xOFF [w]` (instructions using a struct offset). References are decoded from the function start, so
  instruction boundaries are always right.
- `cpkwrite.py` — writes an uncompressed CPK shaped like the game's own. `mklivedata.py OUT.cpk --player ID:PARAM:DELTA --team-short TEAM:TXT --shirt TEAM:PLAYER:N` — a test Live Update plus the CHECK_CHEAT sums it should give.
- `testserver/` — the runtime add-ons used for the live tests (lobby handlers, TURN-over-DTLS leg, switchable Live Update); see its README.
- `probe.py` — client for the mod's **debug probe** (`server/mod/probe.c`, built only with `PROBE=1 sh build.sh`,
  enabled per game copy with `PES2021ONLINE_PROBE=<port>`): read/write the game's memory, follow pointer chains,
  trace any function (register arguments, caller, thread), call functions directly or on the game's window thread.
  To get a probe DLL with the server's address and key written in, start a test server with
  `PESSERVER_MOD_DLL=<probe dll>` and download `/mod/dinput8.dll?from=admin`. Never give it to players.
- `pesdb.py` (+ `cpk.py`) — reads the game database from the CPKs: `checksum`, `player <id>`, `team <id>`, `dump out.json`
  (`PES_GAME_DIR` = folder containing `game/`; no extra packages needed).
- `classmap.py out.json` — every RTTI class with its base classes and vtables (~2 min).
- `errtable.py out.json` — rebuilds `error_sites.json`. `catalog.py DIR` — rebuilds `commands.json`.

`../extract_schemas.py` now also recognises keys compared with the `std::string == const char*` helper
(0x140cf3990). That finds 1,907 more reply fields in 108 commands. `server/pesserver/schemas.json` has **not**
been regenerated, because the server builds default replies from it; regenerate it and run a test match together.
