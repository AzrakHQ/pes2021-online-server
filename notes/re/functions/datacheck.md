# DATAPACK check, match-layer timing values, duplicate player-list crash (static, 2026-11-06)

## Corrections from later findings (2026-11-06)
- **Live 2026-11-06:** with the check off, a lobby match with different player stats stayed in sync past 1ST_15MIN (identical minimaps). The sums are the only guard; a mismatch with the check off is unfair, not fatal (livetests.md).


> **Live checks (2026-11-06, livetests.md):**
> - Q3 confirmed: a player-list **RELOAD** pushed to a game whose list cache is not empty ended the process within 0.25 s, and its Wine log shows `code=e06d7363 (EXCEPTION_WINE_CXX_EXCEPTION)` just before the exit; an **UPDATE** to the other game was harmless.
> - Q1, clean repeat (livetests.md): one copy on a Live Update, the other stock. With `is_revision_check` absent both sessions ended 2 s after START with **GNCX_0006** (GNCX556, OnlineModeMultiplay.cpp:556), "Unable to connect.", not the MXNV_0017 predicted below; with `is_revision_check:"NO"` both reached team selection. The switch works as described. **gncx_labels.md Q1:** GNCX556 is this same verdict, reported by OnlineModeMultiplay (GNCX_0004 = 60 s timeout, GNCX_0006 = sums differ) one tick before TaskMatchSession would report MXNV_0015/0017, so the player sees GNCX_0004/0006; the table below lists the later MXNV codes.


Tags: **[static]** read at the cited address, **[inferred]**, **[guess]**. Dumps are in the working folder (not published).
PES 2021 1.01.00 Steam exe. No network traffic, no game launched.

---------------------------------------------------------------------------------------------------
## Q3 first (it changes server code): why a second player-list RELOAD ends the game

### Answer
It is **not** a use-after-free and not the PostCreateJoinRoom states. It is a bug in the client's generic
watch-list cache (`onlinesystem::ReqWatchListMessageResult`, used by every `REQWATCHL_*` command).
On `list_type "RELOAD"`, the client tries to free the old cache entries with `std::map::at(i)` for
`i = 0 .. size-1`. The cache is keyed by the value of `list_key_name` (for the player list, `pid`).
The owner's cache after the first reply is `{1: blob}`. `at(0)` does not exist, so the client throws
`std::out_of_range("invalid map<K, T> key")`. Nothing catches it, so the process ends (std::terminate → abort).

### Evidence
- Gate watch-list handler **0x1411c1340** (sub-vtable 0x142892000 slot 2 of ReqWatchListMessageResult;
  RTTI at 0x142892018) [static]:
  - reads `list_key_name`, `list_type`, `list_data`; if `list_data` is missing or empty it skips everything
    (0x1411c1657 / 0x1411c1660);
  - if `list_type == "RELOAD"` (0x1411c168d): loop `ebx = 0 .. [this+0x100]-1` (cache size). For each `ebx` it
    walks the map at `[this+0xf8]` for key `ebx` (lower_bound 0x1411c16c0..0x1411c16e9). If the key is not
    there (0x1411c16ee / 0x1411c16f3) it calls **`std::_Xout_of_range("invalid map<K, T> key")`**
    (0x1411c171f → IAT 0x14252fa30). Only if every key 0..n-1 exists does it free them and clear the map
    (0x1411c1734 → 0x1411c24d0).
  - The key of each element is the int value of the field named by `list_key_name`; a non-int or missing value
    gives key 0 (type check 0x1411c189c).
  - ADD / UPDATE: find key → free old blob → erase → insert (0x1411c192e, 0x1411c195a, 0x1411c0050).
    DEL: find → free → erase, no insert. **These paths never throw.**
- The cache is per command object and is only destroyed with it (other caller of the clear: 0x1411c25e0).
  The player-list watcher is created once and kept in the holder `[0x1436fe360]+0x10` (0x1411287c0 creates it
  only if missing; torn down by 0x14103ff60). So after the reply, **every** later RELOAD to the owner throws —
  not only before ENTRY_GAME. [static + inferred]
- Call chain of the parse: client update 0x141218bc0 → 0x1412160b0 (response queue, lock 0x1436fea28) →
  0x141216440 → 0x141174250 (lock cmd+0xd8) → 0x14184d960 → `[[result+0x118]+0x10]` = 0x1411c1340 [static].
  The C++ EH FuncInfo of 0x1411c1340, 0x14184d960, 0x141174250, 0x141216440 and 0x1412160b0 all have
  **0 try blocks**; 0x141218bc0 has no EH handler at all [static, unwind info]. Its own caller was not found, so
  "nothing catches it higher up" is [inferred]. The observed symptom (window gone, no crash dialog, ~0.2 s after
  the push) matches an uncaught C++ exception ending in abort()/__fastfail.
- Why the first reply is fine: the cache is empty, the RELOAD loop runs 0 times [static].
- Why player-list UPDATE pushes were harmless (livetests.md): UPDATE never enters the RELOAD loop [static].
- The things roomcrash.md suspected are clean: the consumer 0x141096320 ignores `list_type`, clears and
  rebuilds the model (0x141095760) and copies by value; st2 helpers 0x14103fcf0 / 0x14103fb80 / 0x14103e630
  copy the player record by value and keep no pointers into the map [static]. No use-after-free there.
- **The same bug explains the other "both games exit" run** in livetests.md: a room-list RELOAD pushed to two
  copies that already held room 1 (key `room_id` 1) → `at(0)` throws in both. The empty `psn_user_id` list was
  probably not the cause there [inferred].
- Why Konami's own server never hit it: co-op lists are keyed by `player_index` (0-based, contiguous), where
  `at(0..n-1)` succeeds; for other lists Konami presumably sent RELOAD only as the first message [guess].

### Safe server rule (all `REQWATCHL_*` lists: ROOMLIST, ROOMPLAYERLIST, BLOCK_MATCH_LIST, …)
1. **RELOAD only into an empty cache**: that is, only the first non-empty message a client watcher receives.
   After the client holds any element, never send RELOAD with non-empty `list_data`, unless the cached keys
   are exactly 0..n-1 (true only for 0-based keys such as `player_index`).
2. For every later change use **UPDATE** (insert-or-replace, takes full element blobs) and **DEL** (`{<key>: N}`) per
   removed key. ADD behaves like UPDATE.
3. Simplest correct implementation: send **every** list message, reply or push, as `UPDATE` with the full current
   list, and send `DEL` for keys that left. The consumers rebuild their model from the merged cache on every
   message and do not read `list_type` (0x141096320 for the player list) [static], so UPDATE is a safe default
   for the first reply too [inferred; the room-list ADD push already worked live].
4. RELOAD with empty `list_data` does nothing at all (no clear, no crash). Use DEL to empty a list.
5. Always send `list_key_name`, and make its field an msgpack **int** in each element.
6. **Fix rooms.md**: it says RELOAD "clears the cached map" and recommends "send full lists as RELOAD" and
   "answer and push, the merge is idempotent". That is wrong and crashes the client.

### Live test that settles it
- A. Owner path, reply only, then **after** ENTRY_GAME push the same player list as **RELOAD** (rqid 0).
  Prediction: game exits within ~0.3 s (proves it is the RELOAD path, not the PostCreateJoinRoom timing).
- B. Repeat the old failing timing (push 50 ms after the reply, before ENTRY_GAME) but with `list_type "UPDATE"`.
  Prediction: no exit, ENTRY_GAME arrives.
- Optional: run once with `WINEDEBUG=+seh`; expect a `RaiseException` with code `e06d7363` (C++ exception)
  just before the exit. Stop at the first signal.

---------------------------------------------------------------------------------------------------
## Q1: DATAPACK check between peers

### What is exchanged
`CmdRevisionCheck` over P2P. Sender 0x141002ea0, receive parser 0x1411b01a0 [static]. Fields:
`msgid, rqid, pid, platform_id{}, np_account_id, xuid_one, name, revision, param_check_sum, entry_check_sum,
is_request`.

Local record (built by 0x140ffbe90 into multiplay+0x48; layout confirmed by the copy ctor 0x140abe310) [static]:

| record off | field | source |
|---|---|---|
| +0x00 | name | OnlineSystem player name |
| +0x58 | pid | `[[OnlineSystem+0x10]+0x15c]` |
| +0x80 | `revision` | `[0x14149a470()+0xe8]`, a `const char*` set to `"unknown"` in the ctor 0x14149a3a6; no other writer found [static, inferred "never changes"] |
| +0xa0 | `param_check_sum` | info+0x40 (0x140ffbc50) |
| +0xc0 | `entry_check_sum` | info+0x60 (0x140ffbe50) |

info+0x40 / +0x60 are written by 0x141042240 and by the CHECK_CHEAT fill 0x1410e7940: +0x40 ← 0x141128e90
(param sum), +0x60 ← 0x141129240 (entry sum) [static 0x1410422a4..0x14104230f].
The peer record is built from the received command by 0x141002520 with the same layout: cmd+0x220 revision →
+0x80, cmd+0x240 param → +0xa0, cmd+0x260 entry → +0xc0. The parser writes revision/param/entry at
data+0x108/+0x128/+0x148, i.e. cmd+0x118 base [static].

### What is compared, in which order
Verdict 0x141001190 (result into multiplay+0x5c4 at 0x141000ec5) [static]:
1. Wait (return 0) until the number of received peer records equals the number of connected slots
   (0x140ffbbc0 vs vector +0x210), and every pid in the member list +0x750 has a record (0x140ffbaa0).
   This step runs **even when the check is off**.
2. If `[mp+0x5c0] == 0` → return 1 (ok).
3. For every slot: compare **param_check_sum** (record +0xa0; 0x1410012db), then **entry_check_sum** (record
   +0xc0; 0x14100133e) as exact byte strings (length + memcmp, case-sensitive). Any difference → 4.
- **`revision` is exchanged but never compared.** dlc_version, livedata version and patch version are not in
  the packet at all; they matter only through the two sums (live-update data changes them, see live.md).

### On-screen codes (TaskMatchSession state 21, 0x1411440c4..)
| +0x5c4 | text | report line / code | result +0x74 |
|---|---|---|---|
| 2 | "Revison check TIMEOUT" (60 s, deadline `now+0xea60` at 0x141003351) | 522 / **MXNV_0015** | 2 |
| 3 | "Revision error [unknown]" | 527 / MXNV_0016 | 3 — **dead code**, nobody writes 3 (sweep.md) |
| 4 | "DATAPACK error" | 533 / **MXNV_0017** | 3 |
| 5 | "Other user disconnected" | 537 / MXNV_0018 | 2 — dead code |
| other | "UNKNOWN" | 542 / MXNV_0019 | 3 |

On screen these show as `MXNV_00NN` (messages.md format) [static 0x14114410a..0x141144393].

### The switch
- `mp+0x5c0` = 1 in the multiplay ctor, then cleared if env+0x38c is 0 (0x140fff59e..0x140fff5bc) [static].
- env+0x38c = (`GET_SERVER_ENV.is_revision_check` == exactly `"YES"`) (0x141210f5a..0x141210f81) [static].
  The Result ctor default is `"YES"` (0x141e45fff, field +0x218), so **omitting the key keeps the check on**;
  `"NO"` or any other text turns it off [static].
- The flag is sampled when the multiplay object is built (match start), from the env of the most recent
  GET_SERVER_ENV [static].
- GET_MATCHID has no part in it.

### Server rules (what must match for two friends)
1. With `is_revision_check:"YES"` (default): both players' **param_check_sum and entry_check_sum must be equal**
   (as sent in CHECK_CHEAT). That means the same player stats, the same transfers and rosters, and the same
   shirt numbers, i.e. the same Live Update / option-file roster data.
   Cosmetic edits (names, kits, faces) are fine. Mismatch → both get MXNV_0017 "DATAPACK error" after connecting.
2. The server already has both sums from CHECK_CHEAT. It can refuse or warn **before** matching or before room
   start, instead of letting the session fail.
   Caveat: 0x141042240 recomputes the sums later (callers 0x14136b0b0, 0x1413964d0, 0x14139a5a0), so a player
   who changes data after login can drift from the CHECK_CHEAT value [inferred].
3. With `is_revision_check:"NO"`: no compare. The record exchange still has to complete within 60 s, or you
   get MXNV_0015.
   Desync risk with different data: the match runs both simulations in lockstep, so expect SYNC_FAILED or other
   divergence [inferred; mod-install-id notes show what a sync mismatch looks like].
4. The `revision` string, DLC version and patch version are not checked between peers.

### Live test
Two PCs, one with a single edited player stat (CHECK_CHEAT sums differ in the server log), friendly lobby,
`is_revision_check:"YES"` → expect MXNV_0017 right after "connecting". Repeat with `"NO"` → expect the team
selection screen. Stop there.

---------------------------------------------------------------------------------------------------
## Q2: match-layer timing and retry values (match.md "Still unknown")

Most of this was settled in sweep.md. I re-checked it at the addresses below and added what it means for players.

### `[0x140ff0a20()+0x58]` network-mode type and TaskMatchSession `+0xa8`
- Type writers [static, sweep.md]: GET_GAMERELAY handler 0x141092240 (`game_sycom` "P2P"→1, "SC"→2, else 0);
  TaskQuickMatching 0x14111a690 forces `type = 1` unless it is 2; debug direct-IP 0x141368db0 sets 3.
- SetConfig 0x141144d80 [static, re-read]:
  - type 1 → task+0xa8 = 1 (0x14114512d). Uses the peer list. Sets `+0xac = (ping limit +0x88 != 0)`
    (0x14114537c).
  - type 2 → +0xa8 = 0 (0x141145391). Parses relay "host:port". States 12/13. Retry.
  - type 3 → +0xa8 = 3 (0x1411454d0). Target string +0x78 default "0".
  - type 0 → +0xa8 keeps its ctor value.
- Task ctor 0x141142e20 [static]:
  - +0x8c = (type == 3), meaning skip GET_MATCHID.
  - +0x88 ping limit = 0.
  - +0x78 retry counter = 2 (no retry) unless game mode `0x14149f4f0() == 0x10`.
- Ping limit is set only by TaskQuickMatching (0x14111aa78), from a player setting via
  0x14100bdf0 → 0x140dd1510 (-1 when a flag is set) [static; the setting is probably the connection filter, guess].
- **Effect on players:**
  - Type 1 (us): SYNC_NOTICE barrier (≤10 s, never an error) and TURN list.
  - Ping check (MXNV_0022 "Ping range over") only in Quick Match when the player set a limit. The lobby path
    0x141133d00 never sets +0x88, so friendly matches have no ping check [inferred].
  - No reconnect retry for P2P (retry only for SC).
  - Type 2 would need a relay server; never send `game_sycom:"SC"`.
  - Type 3 is developer-only.

### Consumer of the peer `time=` (obj+0x8a0)
- 0x1411d81d0 builds `"<pid>-<pid>…-<min(own START time obj+0x89c, peer time obj+0x8a0)>"` (unsigned min at
  0x1411d8586..0x1411d8594). It passes it (≤64 bytes) to `[obj+0x8e8]->vtbl+0xc0(peer, tag)` once per peer
  (0x1411d8889), then clears +0x89c (0x1411d86cb). The writer of +0x8a0 is 0x1411d9f9f, the SYSTEM_NOTICE
  parser [static].
- **Effect:** both sides must build the same tag. The server must relay each START notice's `time=` unchanged and
  to the right peer. A missing peer START (+0x8a0 = 0) gives `…-0` on one side only → P2P connect fails →
  MXNV_0013 (session failed) [inferred].

### `trigger_keep_alive` (env+0x398)
- Copied at 0x14121110b. No reader found [static]:
  - I intersected all 297 `+0x398` operand sites with the 314 callers of the OnlineSystem getter 0x14114a910.
    Only the GET_SERVER_ENV copier 0x141210dd0 and an unrelated `lea` in 0x1410bda80 match.
  - No tiny accessor function reads it either.
- **Effect:** none in 1.01. Keep sending 1.

### Other timing values seen on the way [static]
- Revision-check deadline 60 000 ms (0x141003351).
- SYNC_NOTICE wait 10 000 ms (match.md).
- Pending-request timeout 0xc350 = 50 000 ms in the gate client (0x1412162a2, `ERR_CLIENT_TIMEOUT` path in
  0x1412160b0) [static; I did not trace which commands use it].
