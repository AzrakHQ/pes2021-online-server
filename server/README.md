# pesserver: server internals

Player and host instructions are in the [main README](../README.md). This page covers running the
server without Docker and how the code is laid out.

## Run without Docker

Requires Python 3.10+.

```bash
pip install msgpack cryptography
cd server
# certificate for the HTTPS network test: certs/server.crt (chain) + certs/server.key, next to the DB.
# Easiest: reuse the certs created by the Docker image (docker-entrypoint.sh).
sudo python3 -m pesserver --db data/pes.db serve --host 0.0.0.0 --auto-net --join-port 80 --https-port 443
```

`--auto-net` detects the LAN and public addresses and opens the router ports with UPnP
(`UPNP=0` disables UPnP). `--public-ip ADDR` pins one address for every client instead.
Ports below 1024 need root; otherwise use e.g. `--https-port 8443` and forward 443 to it.

| Port | Service | Module |
|---|---|---|
| 80/tcp | player pages: `/` instructions, `/join`, `/leave` (setup script picked by User-Agent) | `httpgate.py`, `web/` |
| 443/tcp | HTTPS `GateInfo.php` (NAT test) | `httpgate.py` |
| 8080/tcp | web API (form POST → msgpack), `/ca.crt`, `/banner`, the player pages, `/admin` (password) | `httpgate.py` |
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
| `ADMIN_PASSWORD` | fixed admin password (default: random, saved in `<data>/admin_password`) |
| `PESSERVER_NO_IPCHECK` | `1` = don't ask api.ipify.org for the public IP (UPnP only) |

## Code layout

| Path | What |
|---|---|
| `pesserver/ncl.py` | wire codec: XOR + MD5 frame, Blowfish body, zlib + msgpack payload, session-key switch |
| `pesserver/pescmds.py` | command handlers for gate, account and menu; reply validation against `schemas.json` |
| `pesserver/pesmatch.py` | Quick Match queue and pairing, `CMD_WATCH_MATCHING` push, game relay info, peer notice relay |
| `pesserver/httpgate.py` | HTTP/HTTPS web API, `GateInfo.php`, `/ca.crt` |
| `pesserver/stun.py` | STUN responder |
| `pesserver/netsetup.py` | address detection, UPnP port mapping, per-client advertised address, startup banner |
| `pesserver/identity.py` | the server's Ed25519 identity, signed notes and hellos for the mod, writing settings into the mod DLL |
| `pesserver/gamesetup.py` | admin page "This PC": installs the mod into PES copies the installer found (`/games/<n>`, `/prefixes/<n>`) |
| `mod/` | the mod (`dinput8.dll`): MinHook hooks, Monocypher signatures, built by the Dockerfile's first stage |
| `pesserver/events.py` | live activity for the admin page: online players, match phases and scores, saved results |
| `pesserver/web/` | player page, `join.ps1` / `join.sh` (served with the server address filled in), Proton CA installer |
| `pesserver/schemas.json` | response schemas of ~400 commands, extracted from the client (`notes/re/extract_schemas.py`) |
| `pesserver/service.py`, `gate.py` | session handling and dispatch |
| `pesserver/accounts.py`, `matchmaking.py`, `devclient.py` | SQLite accounts, generic queue, JSON dev client (used by `tests/`) |

Unhandled commands are logged as `UNHANDLED CMD_…` and answered with a bare `{result: NOERR}`, so
the client keeps its own defaults. Implementing a new command means adding an
`@cmd("CMD_…")` handler in `pescmds.py` or `pesmatch.py`. The full protocol write-up is
[`docs/protocol.html`](../docs/protocol.html).

Tests: `python3 -m pytest` (in `server/`).
