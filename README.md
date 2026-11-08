# Play PES 2021 Online Again

<p align="center"><img src="docs/hero.svg" alt="Host your own PES 2021 server with one command; friends get one file; the match runs player to player" width="100%"></p>

Konami shut down the PES 2021 servers. Run your own and play **online Quick Match** with your friends.
Free, no accounts.

You need **PES 2021 on Steam** (v1.01.00). The host needs a PC that stays on while you play: **Windows, Linux or
Steam Deck**, nothing else to install.

## 1. Host: start the server

**Windows** (Start menu → PowerShell):

```powershell
irm https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/run.ps1 | iex
```

**Linux, Steam Deck, Mac** (terminal):

```bash
curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/run.sh | sh
```

It downloads the server (one program, 15–30 MB, no admin rights) and prints your **admin page** and its
**password**. On the server's own PC the admin page opens without the password; other devices on your Wi-Fi
(or Tailscale) use it. To open it from anywhere, turn on **Admin page from the internet** on its Network card
(HTTPS: the browser warns once, since the certificate is the server's own). Keep the window open while you play: **Ctrl+C** or closing it stops the server. Paste the same line
next time: it always starts the newest version.

## 2. Host: invite your friends

Open the admin page → **Invite friends** → **Make their invite**. Each friend gets a handle like `@nutmeg_ninja`
(keep the funny one or type your own) and a message with their personal link to paste into Discord, WhatsApp or
Telegram. The link opens a page with their own copy of the mod and the steps; you never send files yourself.

Their copy knows your server, keeps working after it restarts, and tells the server who they are on any PC. In
the game they still choose their own name (no two players can take the same one). **Revoke** locks one person
out, and **Only invited friends can join** keeps everyone else out. Details and the admin API:
[Invites](server/README.md#invites-one-mod-per-friend).

**Playing on the same PC?** The installer finds PES 2021 on your computer (Steam libraries and
`~/Games`): close the game, then on the admin page under **The server computer** click **Install mod**. On Linux it
also sets the Wine option for you, so no launch option is needed.

## 3. Friends: drop it in and play

1. Put `dinput8.dll` in the game folder: in Steam, right-click **PES 2021** → **Manage** → **Browse local files**.
2. **Steam Deck / Linux only:** PES 2021 → **Properties** → **Launch options**, paste:
   ```
   WINEDLLOVERRIDES="dinput8=n,b" %command%
   ```
3. Start the game → **eFootball → Quick Match → Go to Matchmaking**. When two players are searching,
   they get matched.
4. For a league among friends: **eFootball → Online Tournament → Friends Cup** (a new one every month).
   Pick a team, press Start Match, and the table in **Competition Menu → Rankings** keeps the points
   (win 3, draw 1).

The mod needs no administrator rights and changes nothing outside the game folder (it writes its own small settings and log file there) or in the game's own files. **Undo:**
delete the file.

## Problems?

| Problem | Fix |
|---|---|
| A friend can't connect | The host's server must be running. The mod writes what it tried to `pes2021-online.log` in the game folder |
| "Router ports are closed" on the host | Turn on **UPnP** in your router settings and start the server again, or forward the ports it lists |
| The match ends right after kick-off ("unforeseen circumstances") | A Windows player and a Linux / Steam Deck player computed the match differently. Both download the mod again from the admin page (version 0.4.0 or newer keeps their games in step), and the host updates the server (paste the start line again) |
| "Failed to connect with opponent" | The two PCs can't reach each other directly. Everyone installs [Tailscale](https://tailscale.com); the host starts the server again with `PUBLIC_IP=<host's Tailscale IP>` before `sh` (PowerShell: `$env:PUBLIC_IP="…";` before `irm`) |
| Play on another server | Get that server's file and replace yours, or hold **Shift** while the game starts and paste its address |

**New players** need nothing from you: the first time someone with your mod goes online (eFootball →
Quick Match), the game asks for a region and a player name, and your server saves that player, tied to
their copy of the mod (each game folder gets its own player, also when the game reports the same Steam ID).
They appear on the admin page, where you can also delete a player.

Updating: paste your start line again (the admin page also tells you when a newer version is out).

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
