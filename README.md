# PES 2021 Private Server

Play **eFootball PES 2021 SEASON UPDATE** (PC, Steam, v1.01.00) online again: Quick Match
between two players, through a server you run yourself.

Konami shut down the official servers. This server speaks the game's own network protocol, so
**the game is not modified**. Each player only points a few Konami hostnames at your server and
trusts its certificate.

**What works:** login, online menus, the network test, eFootball **Quick Match**
matchmaking, a full 90-minute match between two players, results, and back to the menu for the
next match.

**Not yet:** myClub, rankings and ratings, events, rooms and lobbies, and a relay for players who
cannot connect to each other directly.

---

## How it works

```
 Player A (PES 2021) ──┐                     ┌── Player B (PES 2021)
                       ├──>  this server  <──┤      login, menus, matchmaking
                       │    (Docker)         │
                       └─────── UDP ─────────┘      the match itself: player to player
```

The server handles login and matchmaking. During the match the two games talk **directly to
each other**, so the two players must be able to reach each other over UDP.

- **Same LAN:** nothing extra is needed.
- **Over the internet:** the easiest way is to put everyone on one virtual LAN with
  [Tailscale](https://tailscale.com) or ZeroTier, and use the server's Tailscale/ZeroTier IP
  everywhere below.

---

## Step 1: Host the server (one person)

You need [Docker](https://docs.docker.com/get-docker/) with Docker Compose.

```bash
git clone https://github.com/MohamedAliRashad/pes2021-private-server.git
cd pes2021-private-server
PUBLIC_IP=192.168.1.50 docker compose up -d --build
```

- **`PUBLIC_IP`:** the address the **players' PCs** use to reach the server. Use the LAN IP on a
  LAN, the Tailscale/ZeroTier IP on a virtual LAN, or a public IP if you forward ports.
- **First start:** the server creates its own certificate authority in `./server-data/certs/`.
- **Check it works:** open `http://PUBLIC_IP:8080/ca.crt` in a browser. It should download a
  file.

Ports, which `docker-compose.yml` already maps. Open them in your firewall:

| Port | Used for |
|---|---|
| 10000/tcp | login, menus, matchmaking |
| 8080/tcp | web API, certificate download |
| 443/tcp | the game's HTTPS network test |
| 3478/udp | NAT test (STUN) |

Useful commands:

```bash
docker compose logs -f        # watch players log in and matches start
docker compose restart        # restart
docker compose down           # stop (player names and the certificate stay in ./server-data)
```

If you change `PUBLIC_IP` later, just run the `docker compose up -d` line again with the new IP.
The server reissues its certificate under the same CA. Players only update the IP: they run
their setup script again with the new address.

---

## Step 2: Set up each player's PC (every player, once)

You need the official Steam game, version 1.01.00. The game files are not touched.

### Windows

1. Download this repo: **Code → Download ZIP** on GitHub, then unzip it.
2. Open **PowerShell as administrator**: Start menu → type `powershell` → right-click →
   *Run as administrator*.
3. Run the setup script, replacing `SERVER_IP` with the host's `PUBLIC_IP`. You may need to
   change the path to where you unzipped the repo.
   ```powershell
   cd $HOME\Downloads\pes2021-private-server-main\client
   powershell -ExecutionPolicy Bypass -File .\windows-setup.ps1 -Server SERVER_IP
   ```
   The script adds the Konami hostnames to your `hosts` file, pointing at the server. It also
   adds the server's certificate to Windows' trusted roots.
4. When PES 2021 first goes online and Windows Firewall asks, click **Allow**.

To undo everything: `powershell -ExecutionPolicy Bypass -File .\windows-setup.ps1 -Uninstall`

<details><summary>Manual setup (instead of the script)</summary>

1. As administrator, add these lines to `C:\Windows\System32\drivers\etc\hosts`:
   ```
   SERVER_IP  pes21-x64-gate.cs.konami.net
   SERVER_IP  pes21-x64-stun.cs.konami.net
   SERVER_IP  ntl.service.konami.net
   SERVER_IP  ntleu.service.konami.net
   SERVER_IP  ntlus.service.konami.net
   SERVER_IP  ntljp.service.konami.net
   ```
2. Download `http://SERVER_IP:8080/ca.crt`, then in an administrator command prompt run
   `certutil -addstore -f Root ca.crt`.
</details>

### Linux / Steam Deck (Proton)

Start the game once with Proton so its prefix exists, close it, then run:

```bash
git clone https://github.com/MohamedAliRashad/pes2021-private-server.git
cd pes2021-private-server/client
./linux-setup.sh SERVER_IP          # asks for sudo to edit /etc/hosts
```

The script does two things:
- adds the same hostnames to `/etc/hosts`;
- trusts the certificate **only inside the game's Wine prefix**, which is
  `~/.steam/steam/steamapps/compatdata/1259970/pfx`.

If your Proton version or prefix is somewhere else, set `PROTON_WINE=/path/to/proton/files/bin/wine`
and/or `PES_PREFIX=/path/to/pfx`.

To undo: `./linux-setup.sh --uninstall`

---

## Step 3: Play

1. Start PES 2021 and press a button on the title screen. The first time only:
   - choose your country or region;
   - open each document on the terms screen, tick *I agree* and press *Accept*;
   - pick a user name.
2. Main menu → **eFootball** → **Quick Match**. The game tests your network, then says
   *No new Live Update available*. Press **OK**.
3. Choose a team → **Go to Matchmaking**. When two players are searching, the server pairs them.
   Press **Proceed** at *View Opponent*.
4. In the pre-match menu, both players press **Kick Off**. Have fun.

---

## Troubleshooting

| What you see | What to check |
|---|---|
| "Unable to connect to the server" at login | The hosts entries are missing, or port 10000 can't be reached. Test `http://SERVER_IP:8080/ca.crt` in a browser on that PC. |
| The network test fails, or eFootball modes are locked | The certificate isn't trusted (run the setup again), or port 443 or UDP 3478 is blocked on the server. |
| Matchmaking keeps searching | Both players must be in eFootball **Quick Match** and searching at the same time. Check `docker compose logs`. |
| "Failed to connect with opponent" | The two players' PCs can't reach each other over UDP. Use Tailscale/ZeroTier or a LAN. |
| The match ends with a "no operation" message | The game's own AFK rule: a player gave no input for about 50 seconds. |

Server logs show every request decoded: `docker compose logs -f | grep -E "CMD_LOGIN|MATCH FOUND|GAMEPHASE"`.

---

## For developers

| Path | What |
|---|---|
| `server/` | The server (Python 3, `msgpack` + `cryptography`), `Dockerfile`, tests (`cd server && python3 -m pytest`) |
| `server/README.md` | Server internals, running it without Docker |
| `client/` | Player setup scripts (Windows, Linux/Proton) |
| `notes/network-map.html` | The full reverse-engineered protocol: frame format, crypto, every command in the login, matchmaking and match flow, error codes |
| `notes/re/` | Reverse-engineering helpers: schema extraction, error-code decoder, sample captures used by the tests |

This is a preservation and interoperability project and has no affiliation with Konami. You need
your own legal copy of the game. The server stores only a player name per Steam ID.
