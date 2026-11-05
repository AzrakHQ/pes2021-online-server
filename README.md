# Play PES 2021 Online Again

<p align="center"><img src="docs/hero.svg" alt="Host your own PES 2021 server with one command; friends get one file; the match runs player to player" width="100%"></p>

Konami shut down the PES 2021 servers. Run your own and play **online Quick Match** with your friends.
Free, no accounts.

You need **PES 2021 on Steam** (v1.01.00). The host needs a **Linux** computer that stays on while you play.

## 1. Host: start the server

Paste this in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
```

It prints your **admin page** and its **password**.

## 2. Host: send your friends the mod

Open the admin page and click **Download the mod**. You get one file, `dinput8.dll`. Send it to each
friend on Discord, WhatsApp or Telegram (email usually blocks this kind of file).

It already knows your server, and keeps working after your server restarts. Playing on the same PC?
Use the same file yourself.

## 3. Friends: drop it in and play

1. Put `dinput8.dll` in the game folder: in Steam, right-click **PES 2021** → **Manage** → **Browse local files**.
2. **Steam Deck / Linux only:** PES 2021 → **Properties** → **Launch options**, paste:
   ```
   WINEDLLOVERRIDES="dinput8=n,b" %command%
   ```
3. Start the game → **eFootball → Quick Match → Go to Matchmaking**. When two players are searching,
   they get matched.

The mod needs no administrator rights and changes nothing on your PC or in the game's files. **Undo:**
delete the file.

## Problems?

| Problem | Fix |
|---|---|
| A friend can't connect | The host's server must be running. The mod writes what it tried to `pes2021-online.log` in the game folder |
| "Router ports are closed" on the host | Turn on **UPnP** in your router settings and run the install again, or forward the ports it lists |
| "Failed to connect with opponent" | The two PCs can't reach each other directly. Everyone installs [Tailscale](https://tailscale.com); the host runs the install line again with `PUBLIC_IP=<host's Tailscale IP>` before `sh` |
| Play on another server | Get that server's file and replace yours, or hold **Shift** while the game starts and paste its address |

Server commands: `sudo docker logs pesserver` (shows the admin page and password again) ·
`sudo docker stop pesserver` · `sudo docker start pesserver`. To update, run the install line again.

<details id="hosting-from-windows-or-mac"><summary><b>Hosting from Windows or Mac</b></summary>

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it. Download this
repo (**Code → Download ZIP**) and unzip it. In that folder, create a file named `.env` containing one
line with the address every player will use: your PC's LAN address for friends in your home (`ipconfig`,
"IPv4 Address"), or your public IP / Tailscale IP for friends elsewhere:
```
PUBLIC_IP=192.168.1.50
```
(In PowerShell: `Set-Content .env "PUBLIC_IP=192.168.1.50" -Encoding ascii`.) Then, in a terminal in
that folder:
```
docker compose -f docker-compose.bridge.yml up -d
docker compose -f docker-compose.bridge.yml logs
```
With a public IP, forward TCP 80, 443, 8080, 10000 and UDP 3478, 7600 on your router to this PC (players in
your own home may then need "NAT loopback" on the router). Not yet tested.
</details>

<details><summary><b>Set up earlier with a join line?</b></summary>

The old join lines still work. To switch to the mod, run the undo line once, then use the file:
Windows `irm https://mohamedalirashad.github.io/pes2021-private-server/leave.ps1 | iex`,
Linux `curl -fsSL https://mohamedalirashad.github.io/pes2021-private-server/leave.sh | bash`.
</details>

---

**Website:** [mohamedalirashad.github.io/pes2021-private-server](https://mohamedalirashad.github.io/pes2021-private-server/):
how PES 2021's online mode works, in plain words.
Developers: [`server/README.md`](server/README.md), the mod's source is in [`server/mod/`](server/mod/).
Fan preservation project, not affiliated with Konami.
