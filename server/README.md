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
| `pesserver/pesmanner.py` | courtesy rating from early exits over the last 10 matches; the game's own warning dialogs |
| `pesserver/pesprofile.py`, `pesavatar.py` | player profiles (favourite team/player, comment, courtesy rating, Online Divisions and Matchday records) and avatars |
| `pesserver/pesresults.py`, `pesranking.py` | match history (Last 20 results), Play Analysis uploads, rankings (overall and per division) |
| `pesserver/pesblock.py` | Match Block List: blocked pairs are never matched |
| `pesserver/peswords.py` | the host's word filter: whole words or phrases in `<data>/blocked_words.txt` (one a line) are refused in player names, room names and profile texts |
| `pesserver/announce.py` | news, messages to one player (their Information list), restart warning, lobby welcome notes |
| `pesserver/httpgate.py` | HTTP/HTTPS web API, `GateInfo.php`, `/ca.crt` |
| `pesserver/stun.py` | STUN responder |
| `pesserver/liveupdate.py`, `cpkread.py` | the host's Live Update: the newest CPK in `<data>/liveupdate/` is offered to every game (CMD_GET_DLC_FILELIST) and served at `/liveupdate/<name>`; build one with `notes/re/functions/tools/mklivedata.py` |
| `pesserver/turn.py`, `turnrelay.py`, `turndtls.py` | the relay: TURN list and credentials; TURN over TLS (first leg, sends the dual-route directive); TURN over DTLS with the game's framing (second leg, the one the match uses). Details: `notes/re/functions/ktcdtls.md`, `turncycle.md`, `turnlinkup.md` |
| `pesserver/netsetup.py` | address detection, UPnP port mapping, per-client advertised address, startup banner |
| `pesserver/identity.py` | the server's Ed25519 identity, signed notes and hellos for the mod, writing settings into the mod DLL |
| `pesserver/gamesetup.py` | admin page "The server computer" card: finds PES copies on this computer and installs the mod |
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
