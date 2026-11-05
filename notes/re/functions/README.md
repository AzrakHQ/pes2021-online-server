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
  `cmd CMD_X` (request keys, reply fields, users of the command), `fn`, `refs`, `callers`, `str`, `rtti`.
- `pesdb.py` (+ `cpk.py`) — reads the game database from the CPKs: `checksum`, `player <id>`, `team <id>`, `dump out.json`
  (`PES_GAME_DIR` = folder containing `game/`; no extra packages needed).
- `errtable.py out.json` — rebuilds `error_sites.json`. `catalog.py DIR` — rebuilds `commands.json`.

`../extract_schemas.py` now also recognises keys compared with the `std::string == const char*` helper
(0x140cf3990). That finds 1,907 more reply fields in 108 commands. `server/pesserver/schemas.json` has **not**
been regenerated, because the server builds default replies from it; regenerate it and run a test match together.
