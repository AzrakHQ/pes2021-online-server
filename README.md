# PES 2021 Private Server

Play **eFootball PES 2021 SEASON UPDATE** (PC, Steam, v1.01.00) online again, on a server you run
yourself. Konami shut down the official servers; this one speaks the game's own network protocol,
so **the game is not modified**.

**What works:** login, online menus, the network test, eFootball **Quick Match** matchmaking, full
90-minute matches between players, results, and straight back into the next match.
**Not yet:** myClub, rankings, events, rooms/lobbies, and a relay for players who can't connect to
each other directly.

---

## 1. Host the server: one command

On any Linux machine (gaming PC, home server, Raspberry Pi, VPS):

```bash
curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
```

That's it. The installer:
- installs Docker if it's missing;
- starts the server, which restarts by itself after a reboot;
- opens the needed ports on your router automatically (UPnP);
- prints the command your players run:

```
========================================================================
 PES 2021 private server is running
========================================================================
 Players on your network (192.168.1.50):
   Windows (PowerShell):    irm http://192.168.1.50/join | iex
   Linux / Steam Deck:      curl -fsSL http://192.168.1.50/join | bash

 Players over the internet (203.0.113.7):
   Windows (PowerShell):    irm http://203.0.113.7/join | iex
   Linux / Steam Deck:      curl -fsSL http://203.0.113.7/join | bash
    Router ports opened automatically (UPnP): 80/tcp, 443/tcp, 8080/tcp, 10000/tcp, 3478/udp, 7600/udp
========================================================================
```

If PES 2021 is installed on the same PC, the installer offers to set it up too, so the host can play.

**Manage it:** `docker logs -f pesserver` (see who logs in and which matches start; it also shows the
player commands again), `docker restart pesserver`, `docker rm -f pesserver` (stop). Run the
installer again to update; player names and the certificate are kept.

**Options** (put them before `sh`, e.g. `... | UPNP=0 sh`):

| Variable | Effect |
|---|---|
| `UPNP=0` | don't touch the router; forward the ports yourself |
| `PUBLIC_IP=<addr>` | give every player this address (e.g. a Tailscale/ZeroTier IP) instead of auto-detecting |

<details id="windows-or-macos-host"><summary><b>Windows or macOS host (Docker Desktop)</b></summary>

Docker Desktop runs containers inside a VM, so the server can't see your network or talk to your
router. Download this repo (**Code → Download ZIP**), then in the repo folder:

```
echo PUBLIC_IP=192.168.1.50 > .env
docker compose -f docker-compose.bridge.yml up -d
docker compose -f docker-compose.bridge.yml logs
```
`PUBLIC_IP` is the address your players use. For internet play, forward TCP 80, 443, 8080 and 10000
and UDP 3478 and 7600 on your router to this PC.
</details>

<details><summary><b>VPS / cloud server</b></summary>

The installer works as-is. Allow these ports in the provider's firewall: TCP 80, 443, 8080, 10000 and
UDP 3478, 7600. A small instance is enough; the server uses about 50 MB of RAM.
</details>

## 2. Players: one command

Each player runs the line from the host's banner once. Use the "your network" line on the same LAN,
and the "internet" line otherwise. Opening `http://<server>/` in a browser shows the same commands
with copy buttons.

- **Windows:** open PowerShell (Start menu → type `powershell`) and paste
  `irm http://SERVER/join | iex`. Accept the administrator prompt. When PES 2021 first goes
  online and Windows Firewall asks, click **Allow**.
- **Linux / Steam Deck:** start the game once from Steam, close it, then in a terminal run
  `curl -fsSL http://SERVER/join | bash`. It asks for your sudo password; on a Steam Deck, set one
  first with `passwd` in Desktop Mode.

The script points the game's Konami hostnames at the server (hosts file) and trusts the server's
certificate. On Linux the certificate is trusted only inside the game's Proton prefix. To undo it,
use the same command with `leave` instead of `join`.

## 3. Play

1. Start PES 2021 and press a button on the title screen. The first time only, choose your region,
   open each document on the terms screen, tick *I agree*, press *Accept*, and pick a user name.
2. Main menu → **eFootball** → **Quick Match**. The game tests your network, then says
   *No new Live Update available*. Press **OK**.
3. Choose a team → **Go to Matchmaking**. When two players are searching, the server pairs them.
   Press **Proceed** at *View Opponent*, then both press **Kick Off**.

---

## How it works

```
 Player A (PES 2021) ──┐                     ┌── Player B (PES 2021)
                       ├──>  this server  <──┤      login, menus, matchmaking
                       │                     │
                       └─────── UDP ─────────┘      the match itself: player to player
```

The server handles login and matchmaking. During a match the two games talk **directly to each
other** over UDP.
- **Same LAN:** always works.
- **Over the internet:** the game does its own NAT traversal, which works with most home routers. If
  two players can't connect ("Failed to connect with opponent"), put everyone on one virtual LAN
  with [Tailscale](https://tailscale.com) or [ZeroTier](https://zerotier.com) (both free for small
  groups), and install the server with `PUBLIC_IP=<the host's Tailscale/ZeroTier IP>`.
- **Your ISP shares one public IP between customers (CGNAT):** the banner warns you. Internet
  players can't reach a server at home then; use a VPS or Tailscale/ZeroTier.

## Troubleshooting

| What you see | What to check |
|---|---|
| "Unable to connect to the server" at login | Rerun the join command. Check that `http://SERVER/` opens in a browser on that PC. |
| The network test fails, or eFootball modes are locked | The certificate isn't trusted (rerun the join command with the game closed), or ports 443/tcp and 3478/udp aren't reachable. |
| Matchmaking keeps searching | Both players must be in eFootball **Quick Match** and searching at the same time. Check `docker logs pesserver`. |
| "Failed to connect with opponent" | The two players' PCs can't reach each other over UDP; see *Over the internet* above. |
| The match ends with a "no operation" message | The game's own AFK rule: a player gave no input for about 50 seconds. |
| Banner says router ports were NOT opened | Enable UPnP on your router, or forward the listed ports to the host manually. |

## For developers

| Path | What |
|---|---|
| `install.sh` | the one-command host installer |
| `server/` | the server (Python 3, `msgpack` + `cryptography`), `Dockerfile`, tests (`cd server && python3 -m pytest`) |
| `server/pesserver/web/` | the player pages and join scripts the server hands out |
| `server/README.md` | server internals, running without Docker |
| `docker-compose.yml` | the same as the installer, for `docker compose up -d` from a clone (`build: ./server`) |
| `notes/network-map.html` | the full reverse-engineered protocol: frame format, crypto, every command in the login, matchmaking and match flow, error codes |
| `notes/re/` | reverse-engineering helpers and sample captures used by the tests |

Every push to `main` builds and publishes the image `ghcr.io/mohamedalirashad/pes2021-private-server`
(amd64 and arm64) through GitHub Actions.

This is a preservation and interoperability project and has no affiliation with Konami. You need
your own legal copy of the game. The server stores only a player name per Steam ID.
