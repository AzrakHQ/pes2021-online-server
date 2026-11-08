# pesserver: server internals

Player and host instructions are in the [main README](../README.md). This page covers running the
server from source, building the program, and how the code is laid out.

## Run from source

Requires Python 3.10+.

```bash
pip install msgpack cryptography pyopenssl
cd server
sh mod/build.sh mod/dinput8.dll          # the mod (needs gcc-mingw-w64-x86-64); optional
python3 -m pesserver --data data         # same as the downloaded program: banner, admin page, feed
```

It creates its certificates, admin password and identity in `data/`, detects the LAN and public
addresses and opens the router ports with UPnP (`UPNP=0` disables it; `PUBLIC_IP=ADDR` pins one
address for every client). Without root on Linux, ports 80 and 443 are skipped: the pages move to
8080, and 8080 also answers HTTPS, which is where the mod sends the network test.
`python3 -m pesserver serve ...` is the bare development mode (`tools/pes.py restart`).

**The program:** `packaging/build.sh <name>` (PyInstaller) builds one file in `dist/`;
`packaging/smoke.py dist/<name>` checks it. `.github/workflows/program.yml` does both for every
platform and publishes the release that `run.sh` and `run.ps1` download.

| Port | Service | Module |
|---|---|---|
| 80/tcp | player pages: `/` instructions, `/join`, `/leave` (setup script picked by User-Agent) | `httpgate.py`, `web/` |
| 443/tcp | HTTPS `GateInfo.php` (NAT test) for join-line players and older mods; needs root on Linux | `httpgate.py` |
| 8080/tcp | web API (form POST → msgpack), HTTPS `GateInfo.php` for the mod (TLS told apart by the first byte), `/ca.crt`, the player pages, `/admin` (password) | `httpgate.py` |
| 10000/tcp | game gate: login, menus, matchmaking (NclMio protocol) | `gate.py`, `ncl.py` |
| 3478/udp | STUN binding (NAT test) | `stun.py` |
| 7600/udp | game-relay placeholder (logs only) | `stun.py` |

**Which address clients get:** with `--auto-net`, each client is told the address it can reach. A
client on a private network (LAN, Tailscale/ZeroTier, loopback) gets the local address it connected
to; a client from the internet gets the detected public IP (`netsetup.advertised_addr`).

Environment variables:

| Variable | Purpose |
|---|---|
| `PESSERVER_PUBLIC_ADDR` | pin the IP handed to clients in server/URL lists |
| `PESSERVER_HTTP_PORT` | port used in URL lists (default 8080) |
| `PESSERVER_RXLOG` | path of a JSONL log of every decoded message (optional) |
| `UPNP` | `0` = don't open router ports |
| `TURN` | `0` = don't run the built-in relay for players who can't connect directly (on by default: TLS 5349 + DTLS UDP 5350 + relay UDP 49160–49179, opened with UPnP; needs pyOpenSSL) |
| `UPDATE_CHECK` | `0` = don't ask GitHub whether a newer server is out |
| `ADMIN_PASSWORD` | fixed admin password (default: random, saved in `<data>/admin_password`) |
| `PESSERVER_NO_IPCHECK` | `1` = don't ask api.ipify.org for the public IP (UPnP only) |

## Online Tournament competitions

Each month the server makes a "Friends Cup" (eFootball → Online Tournament) unless another competition
is running. Hosts can make, change, end and delete competitions on the admin page (Tournaments), or with the admin API (signed in; on the host's own PC no password is needed):

```bash
A=http://127.0.0.1:8080/admin/api
T=$(curl -s -X POST -H 'x-pes-local: 1' -d '{"local": true}' $A/login | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
# (from another PC: -d '{"password": "<the admin password>"}' instead)
curl -s -H "Authorization: Bearer $T" $A/tourneys                       # every competition with its table
curl -s -H "Authorization: Bearer $T" -X POST $A/tourneys/save -d '{"name": "Saturday Cup",
  "start": "2026-11-14 18:00", "end": "2026-11-14 23:00", "match_minutes": 6, "teams": [45, 50, 8, 7],
  "info": "Knock-out night: most points by 23:00 wins."}'                 # new (or change one: add "compe_id")
curl -s -H "Authorization: Bearer $T" -X POST $A/tourneys/end -d '{"compe_id": 101}'      # end it now
curl -s -H "Authorization: Bearer $T" -X POST $A/tourneys/delete -d '{"compe_id": 101}'   # remove it and its table
curl -s -H "Authorization: Bearer $T" -X POST $A/tourneys/monthly -d '{"on": false}'      # no monthly Friends Cup
```

`teams` are the game's team ids (national teams 1–14 are Europe, 45 Brazil, 50 Argentina; `notes/re/functions/tools/pesdb.py team <id>`
names any id). `match_minutes` is 5 to 30 (default 10). `info` is shown in the game under Competition Details → Competition
Outline. When a competition ends, each player sees their final position the next time they open Online Tournament.

## Players: names, Block, Move

Each copy of the mod is one player (its install id, sent on the mod's id line). The first time, the game asks for
a name; `pesserver/pesnames.py` stores it lowercased with `_` for spaces, `-` and `.` (letters of any language,
digits and `_`; at most 24), and refuses one that equals an existing name apart from case and those characters
(`ERR_ALREADYEXISTS`, "There is already a User with that name."). When a new copy asks for a taken name the server
keeps a *claim*: the activity feed and that player's panel offer **Move**, which puts the old player (name, rating,
results) on the new copy. **Block** makes a copy's sign-in fail with the game's "account suspended" message.

```bash
curl -s -H "Authorization: Bearer $T" -X POST $A/players/block -d '{"pid": 3, "on": true}'
curl -s -H "Authorization: Bearer $T" -X POST $A/players/move -d '{"pid": 3, "copy": "m:<install id>"}'   # copy: from the player's "claims"
```

## The mod in PES 2021 on the server's own computer

`pesserver/gamesetup.py` finds PES 2021 copies (Steam libraries, `~/Games`) and, every minute while the game isn't
running, puts the mod into each copy without one and replaces this server's mod when the file it hands out changed
(a new mod version, new addresses), with the Wine `dinput8` override in the copy's prefix. Copies with another
server's mod, someone else's `dinput8.dll`, or one the host removed the mod from are left alone.
`GET/POST /admin/api/games/auto {"on"}` switches it (saved in `<data>/auto.json`); `AUTO_MOD=0` turns it off for a
development server.

## The host program on Linux: install, update, restart

`run.sh` downloads the program to `~/.local/share/pes2021-server/` and runs `pes2021-server install`
(`pesserver/hostctl.py`):

- a systemd user service, `~/.config/systemd/user/pes2021-server.service` (`PES_SERVICE=1`; exit code 75 means
  "restart me"), enabled unless `--no-autostart`;
- `~/.local/share/applications/pes2021-server.desktop` ("PES 2021 Server": `pes2021-server open` starts the
  server if needed and opens the admin page) and `~/.local/bin/pes2021-server`;
- then it starts the service, opens `http://localhost:8080/admin` and shows the server's screen. `--here` runs it in
  the terminal instead.

A server in the background also writes its window's screen (the banner, activity lines and warnings, in colour) to
`<data>/console.log`, a new file at each start. `pes2021-server` with no arguments (`console`) shows it live, starting
the service if needed; Ctrl+C closes the view only. `pes2021-server stop` stops the service.

The admin page (Server → The server program) uses the admin API:

```bash
curl -s -H "Authorization: Bearer $T" $A/program                                 # version, update state, autostart, log
curl -s -H "Authorization: Bearer $T" -X POST $A/program/update -d '{"when": "now"}'   # or "idle", "cancel"
curl -s -H "Authorization: Bearer $T" -X POST $A/program/restart -d '{}'
curl -s -H "Authorization: Bearer $T" -X POST $A/program/stop -d '{}'
curl -s -H "Authorization: Bearer $T" -X POST $A/program/autostart -d '{"on": true}'
curl -s -H "Authorization: Bearer $T" -X POST $A/program/background -d '{}'      # a server in a terminal -> the service
```

An update downloads the release's `pes2021-server-linux-x64` (or `-arm64`) next to the program, checks it against
the release's `SHA256SUMS`, test-runs it (`pes2021-server selfcheck`), swaps it in and restarts (as a service:
exit 75 and systemd starts it again; in a terminal: in place). "idle" waits until no match is running.
`PESSERVER_RELEASE_URL` points it at another copy of a release (tests). Run from source, these report why they
can't work.

## Code layout

| Path | What |
|---|---|
| `pesserver/ncl.py` | wire codec: XOR + MD5 frame, Blowfish body, zlib + msgpack payload, session-key switch |
| `pesserver/pescmds.py` | command handlers for gate, account and menu; reply validation against `schemas.json` |
| `pesserver/pesmatch.py` | Quick Match queue and pairing, `CMD_WATCH_MATCHING` push, game relay info, peer notice relay |
| `pesserver/pesmatchday.py` | Matchday: one standing event (group stage only), Home/Away sides, the player's squad stored and returned, points per player and side |
| `pesserver/pesdivisions.py` | Online Divisions: D10 (bottom) to D1, ten-match seasons the game counts down, win 3 / draw 1 / loss 0, promotion and relegation on FINISH_SEASON; OC_RANK players get their own matchmaking queue |
| `pesserver/pesrooms.py` | Friendly Match Lobby: block, room list, create/join, player list, ready (ENTRY_GAME), owner start, back to the room after a match. Rules (never push a RELOAD list, etc.): `notes/re/functions/rooms.md` |
| `pesserver/pesreplay.py` | match replays: every online match is recorded (the games upload it) into `<data>/replays/`, newest 20 kept; watched from Online Tournament → Match Replays → Match List, served at `/r/<match_id>/<N>.enc`. `REPLAYS=0` turns it off |
| `pesserver/pestourney.py` | Online Tournament: friends' 1v1 competitions in the game's PES League screens (a monthly Friends Cup by default, or the host's own via `/admin/api/tourneys`): entry with a team, its own matchmaking queue, 3/1/0 points, rankings, match length, the results notice at the end |
| `pesserver/pesmanner.py` | courtesy rating from early exits over the last 10 matches; the game's own warning dialogs |
| `pesserver/pesprofile.py`, `pesavatar.py` | player profiles (favourite team/player, comment, courtesy rating, Online Divisions and Matchday records) and avatars |
| `pesserver/pesresults.py`, `pesranking.py` | match history (Last 20 results), Play Analysis uploads, rankings (overall and per division) |
| `pesserver/pesblock.py` | Match Block List: blocked pairs are never matched |
| `pesserver/peswords.py` | the host's word filter: whole words or phrases in `<data>/blocked_words.txt` (one a line) are refused in player names, room names and profile texts |
| `pesserver/pesnames.py` | player names (lowercased, `_`, unique apart from case/spaces/`_-.`), claims and Move to a new copy, Block |
| `pesserver/announce.py` | news, messages to one player (their Information list), restart warning, lobby welcome notes |
| `pesserver/hostctl.py` | the Linux host program: `install` (systemd user service, apps-menu entry), `open`, `selfcheck`; update from the admin page (checksum, self-check, swap, restart), restart, stop, start when I log in |
| `pesserver/httpgate.py` | HTTP/HTTPS web API, `GateInfo.php`, `/ca.crt` |
| `pesserver/stun.py` | STUN responder |
| `pesserver/liveupdate.py`, `cpkread.py` | the host's Live Update: the newest CPK in `<data>/liveupdate/` is offered to every game (CMD_GET_DLC_FILELIST) and served at `/liveupdate/<name>`; build one with `notes/re/functions/tools/mklivedata.py` |
| `pesserver/turn.py`, `turnrelay.py`, `turndtls.py` | the relay: TURN list and credentials; TURN over TLS (first leg, sends the dual-route directive); TURN over DTLS with the game's framing (second leg, the one the match uses). Details: `notes/re/functions/ktcdtls.md`, `turncycle.md`, `turnlinkup.md` |
| `pesserver/netsetup.py` | address detection, UPnP port mapping, per-client advertised address, startup banner |
| `pesserver/identity.py` | the server's Ed25519 identity, signed notes and hellos for the mod, writing settings into the mod DLL |
| `pesserver/gamesetup.py` | PES 2021 on the server's own computer: finds the copies, keeps the mod in them up to date by itself (and the admin page's buttons) |
| `pesserver/certs.py`, `updates.py` | the server's CA and TLS certificate; the "newer version is out" check |
| `pesserver/__main__.py` | command line: `host` (the program), `serve` (development), `adduser` |
| `mod/` | the mod (`dinput8.dll`): MinHook hooks, Monocypher signatures, same-math functions from musl (`mod/musl-math/`, checked by `mod/test/mathcheck.c`), built by `mod/build.sh` (MinGW) |
| `packaging/` | PyInstaller build script, entry point and smoke test for the program |
| `pesserver/events.py` | live activity for the admin page: online players, match phases and scores, saved results |
| `pesserver/web/` | player page, `join.ps1` / `join.sh` (served with the server address filled in), Proton CA installer |
| `pesserver/schemas.json` | response schemas of ~400 commands, extracted from the client (`notes/re/extract_schemas.py`) |
| `pesserver/service.py`, `gate.py` | session handling and dispatch |
| `pesserver/accounts.py`, `matchmaking.py`, `devclient.py` | SQLite accounts, generic queue, JSON dev client (used by `tests/`) |

Unhandled commands are logged as `UNHANDLED CMD_…` and answered with a bare `{result: NOERR}`, so
the client keeps its own defaults. Implementing a new command means adding an
`@cmd("CMD_…")` handler in `pescmds.py` or one of the feature modules (and importing the module in
`service.py`; a test checks that). The full protocol write-up is
[`docs/protocol.html`](../docs/protocol.html).

Tests: `python3 -m pytest` (in `server/`; run all of them, as the release build does).
