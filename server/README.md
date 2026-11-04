# pesserver: server internals

Player and host instructions are in the [main README](../README.md). This page covers running the
server without Docker and how the code is laid out.

## Run without Docker

Requires Python 3.10+.

```bash
pip install msgpack cryptography
cd server
# certificate for the HTTPS network test: certs/server.crt (chain) + certs/server.key, next to the DB.
# Easiest: reuse ./server-data/certs created by the Docker image (docker-entrypoint.sh).
PESSERVER_PUBLIC_ADDR=192.168.1.50 python3 -m pesserver --db pes.db serve \
    --host 0.0.0.0 --public-ip 192.168.1.50 --https-port 8443
```

The HTTPS endpoint listens on 8443. Players' games connect to port 443, so forward 443 to 8443,
or run with `--https-port 443` as root.

| Port | Service | Module |
|---|---|---|
| 10000/tcp | game gate: login, menus, matchmaking (NclMio protocol) | `gate.py`, `ncl.py` |
| 8080/tcp | web API (form POST → msgpack), `/ca.crt` | `httpgate.py` |
| 8443/tcp | HTTPS `GateInfo.php` (NAT test) | `httpgate.py` |
| 3478/udp | STUN binding (NAT test) | `stun.py` |
| 7600/udp | game-relay placeholder (logs only) | `stun.py` |

Environment variables:

| Variable | Purpose |
|---|---|
| `PESSERVER_PUBLIC_ADDR` | the IP handed to clients in server/URL lists |
| `PESSERVER_HTTP_PORT` | port used in URL lists (default 8080) |
| `PESSERVER_RXLOG` | path of a JSONL log of every decoded message (optional) |

## Code layout

| Path | What |
|---|---|
| `pesserver/ncl.py` | wire codec: XOR + MD5 frame, Blowfish body, zlib + msgpack payload, session-key switch |
| `pesserver/pescmds.py` | command handlers for gate, account and menu; reply validation against `schemas.json` |
| `pesserver/pesmatch.py` | Quick Match queue and pairing, `CMD_WATCH_MATCHING` push, game relay info, peer notice relay |
| `pesserver/httpgate.py` | HTTP/HTTPS web API, `GateInfo.php`, `/ca.crt` |
| `pesserver/stun.py` | STUN responder |
| `pesserver/schemas.json` | response schemas of ~400 commands, extracted from the client (`notes/re/extract_schemas.py`) |
| `pesserver/service.py`, `gate.py` | session handling and dispatch |
| `pesserver/accounts.py`, `matchmaking.py`, `devclient.py` | SQLite accounts, generic queue, JSON dev client (used by `tests/`) |

Unhandled commands are logged as `UNHANDLED CMD_…` and answered with a bare `{result: NOERR}`, so
the client keeps its own defaults. Implementing a new command means adding an
`@cmd("CMD_…")` handler in `pescmds.py` or `pesmatch.py`. The full protocol write-up is
[`notes/network-map.html`](../notes/network-map.html).

Tests: `python3 -m pytest` (in `server/`).
