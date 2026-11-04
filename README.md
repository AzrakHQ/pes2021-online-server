# Play PES 2021 Online Again

<p align="center"><img src="docs/hero.svg" alt="Host your own PES 2021 server with one command; friends join with one line; the match runs player to player" width="100%"></p>

Konami shut down the PES 2021 servers. Run your own and play **online Quick Match** with your friends.
Free, no accounts, and the game isn't modified.

You need **PES 2021 on Steam** (v1.01.00). The host needs a **Linux** computer that stays on while you play.

## 1. Host: start the server

Paste this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
```

When it finishes, you get:
- **your server's web address**, like `https://brave-tiger-moon.trycloudflare.com`;
- **the line to send your friends**, which works for friends at home and far away;
- **your admin page and password**, which show who's online, live scores, results and a league table.

## 2. Players: join (once)

- **Windows:** open **PowerShell**, paste the join line, and click **Yes** when asked.
  ```
  irm https://your-server-address/join | iex
  ```
- **Linux / Steam Deck:** start the game once, close it, then paste in a terminal:
  ```
  curl -fsSL https://your-server-address/join | bash
  ```

## 3. Play

**eFootball → Quick Match → pick a team → Go to Matchmaking.** When two players are searching, they
get matched.

## Problems?

| Problem | Fix |
|---|---|
| Friend can't connect / "Failed to connect with opponent" | Everyone installs [Tailscale](https://tailscale.com). The host reinstalls with `... \| PUBLIC_IP=<host's Tailscale IP> sh` |
| "Couldn't open router ports" | Turn on **UPnP** in your router settings and run the install again |
| "Unable to connect to the server" | Run the join line again |
| Undo on a player's PC | Run the join line with `leave` instead of `join` |

Server commands: `docker logs pesserver` (shows the address and password again) · `docker restart pesserver` ·
`docker rm -f pesserver` (stop). To update, run the install line again.
The web address changes when the server restarts. Friends who already joined don't need to do anything.

<details id="hosting-from-windows-or-mac"><summary><b>Hosting from Windows or Mac</b></summary>

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), download this repo
(**Code → Download ZIP**), and in that folder run (use your PC's IP from `ipconfig`):
```
echo PUBLIC_IP=192.168.1.50 > .env
docker compose -f docker-compose.bridge.yml up -d
```
For friends outside your house, forward TCP 80, 443, 8080, 10000 and UDP 3478, 7600 on your router.
</details>

---

Developers: see [`server/README.md`](server/README.md) and [`notes/network-map.html`](notes/network-map.html).
Fan preservation project, not affiliated with Konami.
