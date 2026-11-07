# Reply parsing inside the game: receive path, parser bench, message trace, title without keys

PES2021.exe 1.01.00 (image base 0x140000000). Static reading 2026-11-06, then tried live the same day with the
probe build (`server/mod/probe.c`). Tags: **[live]** seen in the running game, **[static]** read in code,
**[inferred]**. Tools: `tools/parsebench.py`, `tools/pesflow.py traffic|title`, `tools/scenario.py`,
`tools/probesession.py` (see "Tools" at the end).

## 1. Receive path: from a frame to the command's parser [static]

1. Client update 0x141218bc0 → `ServerConnection` update 0x141239b60 → NclMio pump 0x141854150 → frame reader
   0x141853120.
2. 0x1418566c0 decodes, reassembles and inflates one message. The plain msgpack body is copied into the caller's
   buffer at 0x141856a9c (`0x1403d3aa0(buf, data, len)`).
3. The frame reader parses it once into a throw-away generic result (new 0x1d0, ctor 0x1411c0430,
   `0x14184d960(gen, body, 2)`, generic parser 0x14184eb20). It calls the connection's vtable slot 1, then
   deletes `gen`.
4. **`ServerConnection::OnMessage` 0x141239840(conn, gen, body)** (vtable 0x1428b5a68):
   - It reads msgid = std::string at gen+0x140 and rqid = u32 at gen+0x160.
   - **It drops a msgid that is not registered** (0x141216f00: request registry 0x1436feb30, then the push
     registries 0x1436feb68…).
   - It queues a 0x60-byte entry in conn+0x1e0 (at most 256; 0x141239ed0): +8 char[64] msgid, +0x48 rqid, +0x50
     a copy of the body, +0x58 len+1.
5. The pending-request loop 0x141216440 (under lock 0x1436fea28; entries 0x70 bytes, +0x48 = command object):
   - For each pending command, 0x1412395f0 takes **the first queued entry with the same msgid**.
   - **The client never compares rqid when it matches a reply.** Pushes go through 0x1412392b0.
   - Then 0x141174250(cmd, &buf) → **`0x14184d960(Result=[cmd+0xd0], &buf, 2)`**.
6. **`0x14184d960(Result* R, Buffer* b, int mode)`**: mode 1 = msgpack, 0 = HTTP/JSON, 2 = take the global
   [0x143777d10].
   - In msgpack mode it copies the body into the sbuffer at R+0x120/R+0x128, then calls the parser
     `[[R+0x118]+0x10](R+0x118)` (vtable at +0x118, slot 2).
   - It returns 0, or 0xffffff37 for an empty body.
   - Buffer (0x30 bytes): +0 data, +0x18 size, +0x20 capacity (> 0), +0x28 flags (bit 1 = 0).
7. **Parsers** (e.g. CmdGetMatchdayInfoResult 0x141cc3cd0; `this` = R+0x118):
   - They unpack with msgpack-c (0x141c190a0 → zone 0x1421a5c50 → unpack 0x1421a6710; objects are 0x18 bytes:
     +0 type, +8 size/value, +0x10 pointer).
   - They walk the top map and store each known key at a fixed offset.
   - **They do not check value types and always return 1.** A wrong type gives garbage or a crash, never an
     error. That is why Matchday's `all_reward_info` sent as a blob killed the game (matchday2.md): live, the
     same body faults inside msgpack code at 0x1421a5e9a (c0000005) [live].
8. Common header in every Result: **msgid** std::string at R+0x140, **rqid** u32 at R+0x160, **result**
   std::string at R+0x1b0. Other fields are at R+0x118 + the parser offset used in the KB notes. [static; live
   for GET_MATCHDAY_INFO, GET_SERVER_ENV]

Consequences for the server:
- A reply's rqid does not matter to the client. Two requests with the same msgid in flight take replies in order.
- A reply or push whose msgid the client never registered is silently dropped.
- The queue holds at most 256 messages (0x1412399be).

## 2. Parser bench: the game's own reader on our bytes [live]

**Every step runs with `gcall`** (the game's window thread). The game allocator (operator new 0x140227920 →
0x1403fb240) uses per-thread state the probe thread lacks.

| step | call |
|---|---|
| allocate the Result | `gcall 140227920 <size>`. The size comes from `tools/result_table.json`, e.g. 0x6d8 for GET_MATCHDAY_INFO. |
| zero it | write zeros. Fields the ctor never sets then stay 0, so diffs are clean. |
| construct | `gcall <ctor> R`. The table has ctor, dtor, parser, size and class for all 398 reply classes. |
| body + buffer | allocate D (body) and F (0x30). Write the body to D and {D, 0, 0, len, len, 0} to F. |
| parse | `gcall 14184d960 R F 1`. It returns 0. |
| read | msgid R+0x140, rqid R+0x160, result R+0x1b0, other fields at R+0x118+offset. |
| free | `gcall <dtor> R 1` (scalar deleting dtor), then `gcall 140227930 D` and `gcall 140227930 F`. |

- The factory 0x14116dee0 is not the way to create Results: it only builds about 15 onlinesystem tasks.
- **Crash guard [live]:** the probe catches a fault inside any gcall (a vectored handler puts the registers back
  to just before the call). The fault comes back as `gcall VA FAULT code=… rip=…`, and the game keeps running.
  - Memory the call allocated leaks, and a lock it held stays held.
  - After a fault the game kept working (main menu, more bench runs), but treat it as tainted for real tests.
- **First live result:** our current GET_MATCHDAY_INFO reply against the old flat layout (no
  `compe_info.info_overview`). The bench diff shows:
  - this+0xb8 compe_id/base_id going 1,1 → 0,0;
  - the event name at this+0xc0 falling back to the game's default "NotImplement";
  - the side names "Side A"/"Side B" (this+0x118, +0x1a0) going empty;
  - the date block at this+0x290 going to zero.
  That is exactly the "compe_id 0 / Group Match Stage has ended" bug found by playing, now visible in seconds.

## 3. Every message, inside the game [live]

- `trace 1403d3aa0 ncl 100000 1:128` sees every message body as plain msgpack (the first 128 bytes). Keep the hits
  whose caller is:
  - **0x14184e5b8**: a request being sent (the Option serializer 0x14184e530; HTTP-mode commands come from
    0x14184e5fb as JSON);
  - **0x141856aa1**: a message received, decoded and inflated, **before** the registry filter, so dropped
    messages show too.
  - Other callers are the compressed or encrypted layers. Ignore them.
- `pesflow.py PORT traffic [SECONDS]` prints `-> CMD_X rqid` / `<- CMD_X rqid result`.
  - Our replies often put `msgid` after a long first field, beyond the 128-byte dump. They show as `?{keys}`.
  - `scenario.py` keeps this log for every variant.
- Text-only alternative for registered messages: `trace 141239ed0 rxq 4000 1:96` (the msgid string is at bytes
  8..71, the rqid at 72). [static]

## 4. Title screen and login without key presses [live]

- Intro/MenuTitle is `menu::TitleStart` (vtable 0x142668880).
  - Its slot 49 is a no-op, so the dialog OK call does nothing here.
  - Its "button pressed" function is **0x140a98d00(this)**: it plays "titleOut" and sets state 1. The per-frame
    update 0x140a9a8f0 then posts "proceed".
  - `pesflow.py PORT title` finds the TitleStart node and gcalls it.
  - Live: title → data load → IntroLoginFlow → login → TopMenu/TopMenuNew, with `wait TopMenu/` confirming the
    login dialogs. No keys at all for an account that already has a player.
- Intro/IntroLoginFlow (`OnlineFlowLoginIntro`, update 0x140bc9e10) runs TaskLoginManager by itself. Its dialogs
  ("User Data Created." = DialogProceed "CreateFirstLoginDialog"; "No new Live Update available.") are confirmed
  through vtable +0x188 (`pesflow ok`).
- Jumping straight to the main menu (debug event `0_sample`, or the raw 0x1030008 jump) would skip the system data
  load, the edit-data / Live Update load and the login. Not used.
- Still needing keys: the first start of a copy (region, terms) and the player name.

## 5. Navigation facts found with the scenario runner [live]

- Entering an online mode from the main menu goes through `Online/Login/LoginFlow`. It ends on the "No new Live
  Update available." dialog.
  - Matchday asks JOIN_MATCHDAY_MODE + GET_MATCHDAY_LIST, then sends LEAVE_MATCHDAY_MODE + LEAVE_ONLINE_MODE.
  - Next come the Live Update check (GET_DLC_FILELIST) and the dialog. Only after OK does it reach
    `Online/MyClub/Matchday/MatchdayCompeList`.
- On the event list, Return opens `DialogAgreementCB` "Enter this Competition. Proceed?" with the cursor on No
  (button 0). Picking Yes (`pesflow ok 1`) leads to GET_MATCHDAY_INFO and `MatchdayGroupSelect` ("Choose the Side
  that you want to compete for…").
- An error reply (ERR_DATABASE) to GET_MATCHDAY_INFO stops on `MatchdayProcGetCompeDetail` with a DialogReturn
  "An error occurred while accessing the database."
- Pick a dialog button: write the index to `[[dlg+0x88]+0xd8]`, then call slot 49 (`pesflow.py PORT ok N`).

## Tools

- `probesession.py up 1|2|both` does the whole setup:
  1. builds the probe DLL;
  2. starts the test server (real repo handlers + `testserver/replypatch.py`);
  3. installs a stamped probe copy;
  4. launches and logs in to the main menu.
  `down` closes everything, restores the release DLL and `logs/server.log`, and deletes test Live Update saves.
- `testserver/replypatch.py` (`PATCH_CTL`) changes any real reply from `logs/patch.json` without restarts:
  - `patch` edits fields (`set` / `del`) on paths that go through nested blobs;
  - `err` replaces a reply with an error;
  - `push` sends one-shot pushes.
  - It logs every reply's msgpack to `logs/replies.jsonl`.
- `scenario.py FILE.json [VARIANT…]` runs reply variants × steps (`home`, `go`, `wait`, `reply`, `ok N`, `dialog`,
  `shot`, `trace`, `key`, …).
  - It checks after every step that the game still runs.
  - It writes `logs/scenarios/<name>-<time>/report.md` with the steps, the dialogs, the server-log problems and
    the in-game traffic.
  - Example: `tools/scenarios/matchday.json`.
- `parsebench.py PORT CMD SRC [SRC2] [--set PATH=JSON] [--del PATH] [--words]` runs the game's parser on
  `last` / `last~N` (our replies from replies.jsonl), `hex:…`, `json:…` or a file. With two sources it prints the
  words that differ.
