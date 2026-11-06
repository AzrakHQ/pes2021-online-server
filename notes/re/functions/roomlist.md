# Room list shows "Number of rooms 0": REQWATCHL_ROOMLIST entry vs. parser, model and list filter

> **Status:** static analysis plus our logs (2026-11-05). **Confirmed live on 2026-11-06:** with the real `game_env` (limitTime MIDDLE) and a room-list UPDATE after SET_GAMEENV, the second player sees "Number of rooms 1" and the room row, and can join (livetests.md).

Tags: [static] = read in code at the address, [log] = our server logs from the 2026-11-05 live test, [inferred], [guess].
Disassembly dumps stay in the working folder (not published).

## Answer in one paragraph
The room is parsed and stored correctly, and then **the list builder's filter hides it, because its
`gameenv.limitTime` is `"NOSET"`**. GetRoomList 0x14104c730 always drops a room when the limitTime enum index is 0
(0x14104c7c9). The enum is 0x141b99ea0 = {0 NOSET, 1 SHORT, 2 MIDDLE, 3 LONG}, and an unknown or missing string
also gives 0. The same function writes the count of the rooms it keeps to model+8 (0x14104c90b). That is the
"Number of rooms" value, so the screen shows 0.

Our test server (the test-only lobby handler's `set_gameenv`) looks for cpuLevel/limitTime/… as top-level request keys.
The game sends them inside the `game_env` msgpack blob [log 21:17:07,531: `game_env` = {…, limitTime "MIDDLE"}].
So the room keeps DEFAULT_ENV, where limitTime is "NOSET", and every copy of the room-list entry (ADD and RELOAD)
carries NOSET [log 21:17:07,365 and 21:19:28,186].

## Path [static]
REQWATCHL_ROOMLIST message → merger 0x1411c1340 (key `room_id` must be an int; RELOAD/ADD/UPDATE/DEL) →
parser 0x141d03090 (CmdReqwatchlRoomlistResult vt@+280 slot2) → 0x338-byte element vector →
consumer **0x1410e06e0** = slot 27 of `CommandObjectCmdReqwatchlRoomlist` (vtable 0x14284a318, slot at 0x14284a3f0).
The consumer clears the model map (0x14104d570) and, for each element with room_id ≠ 0 (0x1410e07a9), builds a
0xf8-byte RoomInfo and inserts it into the map (model+0x10) with 0x1410e22d0. The model is [[[0x1436fced8]+0x20]+0x20].
Menu `menu::OnlineLobbyRoomSelect` (vt 0x1426c6360) slot 76 **0x140bdc580** calls **GetRoomList 0x14104c730**. That
function filters and sorts the rooms (comparator 0x1410563e0), sets model+8 = count, and returns a vector. Each room_id
in the vector is then appended as a row (0x140ced480), with no further checks.
The Multi lobby has its own twin: 0x1410e1480, slot 27 of `…RoomlistForLobbyMulti`.

## Element fields: wire type, where it is stored, what reads it
Element = msgpack **bin/str blob** of a map. It is unpacked by 0x141c190a0 at 0x141d03855; the element value type is
not checked. Element keys are compared with memcmp; inner keys are not type-checked. Top-level message keys must be
str (0x141d03130). String fields: only nil is rejected (0x141d039b9). Any other value is read through the
{len @+8, ptr @+0x10} union, so **an int sent in a string field = garbage pointer**. Number fields: a double is
truncated with cvttsd2si; positive int (type 2) is used; anything else → 0.
Offsets are element offset → RoomInfo offset (b).

| key | wire type | elem → b | used by |
|---|---|---|---|
| room_id | uint | +0 → +0 | 0 → element skipped (0x1410e07a9); map key |
| status | str | +8 → +0x60 (enum 0x141b9a1b0 NA,WAITING,SETENV,GAME,RESULT,DISCONWAIT,RESULTWAIT,FORFEITEDGAME,RESULTRETURNWAIT; unknown → 0) | "in progress" flag |
| game_phase | str | +0x28 → +0x64 (enum 0x141b99e40 DATASYNC…END; "" → 0) | "in progress" flag |
| name | str | +0x48 → +0x08 | row text |
| gameenv | **bin blob** (0x141d03cd6) of map {cpuLevel S, gametime N, injury S, ball_type N, exGame S, pkOnOff S, substitution N, exSubstitution S, **limitTime S**} | +0x68…+0x120 → +0x90…+0xac | **limitTime idx ≠ 0 required** (b+0xac) |
| gamer_num | uint | +0x140 → +0x50 | refine filter "full" |
| gamer | **array of bin blobs** (0x141d044e6), each {pid N, is_room_owner S, side S, enter_no N, game_no N} | +0x148 → map b+0xb0 (key pid) | **map must be non-empty** (b+0xb8); owner pid = gamer with is_room_owner "YES" → b+0xe8 (0 if none, 0x1410e1121) |
| is_passwd | str YES/NO | +0x158 → +0x68 | refine filter (password) |
| max_players | uint | +0x178 → +0x54 | refine filter "full" |
| is_invite_limit | str YES/NO | +0x180 → +0x69 | **"YES" → always hidden** |
| dlcontent | str | +0x1a0 → +0x70 (copied as a string) | display only; not filtered |
| is_original_player, vs_com, room_user_count, is_anyplayers, is_select_gk, auto_start_num, game_rating_limit | S/S/N/S/S/N/S | parsed (+0x1c0, +0x1e0, +0x2c8, +0x2d0, +0x2f0, +0x310, +0x318) | **not read by the consumer**; no effect on the list |
| owner_pname | **bin blob** (0x141d04cb7) {name S, xuid_one S, psn_user_id **bin blob** (0x141d04ee8) {account_id N(u64), online_id S, is_valid_cache S}, is_deleted S} | +0x200…+0x288 → b+0xc0 (0x141059ad0) | row owner name |
| owner_lang | str | +0x2a8 → +0x58 (0x1410526a0, lang code) | display |
| room_number | uint | +0x314 → +0x28 | refine "room number" search |

Nesting is proven by where each sub-map's unpack zone is freed (0x1421a5be0 at 0x141d0518e, 0x141d05287 and
0x141d043ea): `is_valid_cache` belongs **inside psn_user_id**, and `is_deleted` inside owner_pname. An
`is_valid_cache` at the owner_pname level, as we send it now, is ignored but harmless. [static]

## Every filter in GetRoomList 0x14104c730 [static]
Always applied:
1. `b+0x69` is_invite_limit == "YES" → hidden (0x14104c7bf).
2. `b+0xac` limitTime index == 0 (NOSET/unknown/missing) → hidden (0x14104c7c9). **← our bug**
3. `b+0xb8` gamer map size == 0 (no or empty `gamer`) → hidden (0x14104c7d3).

Applied only when the player set them in "Refine Search" (menu::OnlineLobbyRefineSearch 0x140beb330 writes
lobby+0x48 and +0x4c; 0x14103d820 writes +0x50 and +0x58). Off by default [inferred]:
4. lobby+0x50 room-number search: the room is hidden unless its zero-padded room_number contains the typed text (0x141041660).
5. lobby+0x48 "available only": hidden if gamer_num ≥ max_players, or in progress (0x141041ba0: status SETENV/GAME
   with game_phase ≠ DATASYNC, or status RESULT/DISCONWAIT).
6. lobby+0x4c password mode: 1 → hide rooms with a password; 2 → hide rooms without one.

Not filtered: own pid / own room, block id, owner_lang vs. client lang, match_type (not in the entry), vs_com,
dlcontent, is_original_player, game_rating_limit, room_user_count, and status in itself (only via filter 5).

## Server must send (corrected)
1. **CMD_SET_GAMEENV**: unpack `req["game_env"]`. It is a msgpack map sent as raw bytes; our decoder shows it as a
   surrogate-escaped str. Store it as the room's env, and put it back verbatim as `gameenv` (bin). Then push
   `REQWATCHL_ROOMLIST UPDATE` with the room to every block subscriber. The ADD push goes out before SET_GAMEENV
   arrives (≈200 ms later).
2. Default env, in case SET_GAMEENV is late or missing: limitTime **"MIDDLE"**, never "NOSET".
3. `is_invite_limit` "NO" for rooms that should be listed. `gamer` must be a non-empty array of bin blobs. `gamer_num`
   is the number of members; `max_players` = 2.
4. Cosmetic fields: echo them from CREATEJOIN (`use_original_player` → is_original_player, is_anyplayers,
   is_select_gk, auto_start_num, game_rating_limit, vs_com). Set owner_lang = the CREATEJOIN `lang` ("US") [inferred].

Corrected element. This is the blob inside list_data; send it as bin. 596 bytes, generated with msgpack
use_bin_type=True from the 21:17 room and the owner's real game_env:
```
{'room_id':1,'status':'WAITING','game_phase':'','name':'Room Mohamed Rashad',
 'gameenv': bin({'cpuLevel':'HARDEST','gametime':10,'injury':'YES','ball_type':22,'exGame':'NO','pkOnOff':'NO',
                 'substitution':3,'exSubstitution':'YES','limitTime':'MIDDLE'}),
 'gamer_num':1,'gamer':[bin({'pid':1,'is_room_owner':'YES','side':'HOME','enter_no':1,'game_no':0})],
 'is_passwd':'NO','max_players':2,'is_invite_limit':'NO','dlcontent':'NO','is_original_player':'NO',
 'vs_com':'PL_VS_PL','owner_pname': OWNER_PNAME,'owner_lang':'US','room_user_count':1,'is_anyplayers':'NO',
 'is_select_gk':'NO','auto_start_num':0,'room_number':1,'game_rating_limit':'NONE'}
```
Hex: `de0015a7726f6f6d5f696401a6737461747573a757414954494e47aa67616d655f7068617365a0a46e616d65b3526f6f6d204d6f68616d656420526173686164a767616d65656e76c47989a86370754c6576656ca748415244455354a867616d6574696d650aa6696e6a757279a3594553a962616c6c5f7479706516a6657847616d65a24e4fa7706b4f6e4f6666a24e4fac737562737469747574696f6e03ae6578537562737469747574696f6ea3594553a96c696d697454696d65a64d4944444c45a967616d65725f6e756d01a567616d657291c43585a370696401ad69735f726f6f6d5f6f776e6572a3594553a473696465a4484f4d45a8656e7465725f6e6f01a767616d655f6e6f00a969735f706173737764a24e4fab6d61785f706c617965727302af69735f696e766974655f6c696d6974a24e4fa9646c636f6e74656e74a24e4fb269735f6f726967696e616c5f706c61796572a24e4fa676735f636f6da8504c5f56535f504cab6f776e65725f706e616d65c46684a46e616d65ae4d6f68616d656420526173686164a8787569645f6f6e65a0ab70736e5f757365725f6964c42b83aa6163636f756e745f696401a96f6e6c696e655f6964a0ae69735f76616c69645f6361636865a3594553aa69735f64656c65746564a24e4faa6f776e65725f6c616e67a25553af726f6f6d5f757365725f636f756e7401ad69735f616e79706c6179657273a24e4fac69735f73656c6563745f676ba24e4fae6175746f5f73746172745f6e756d00ab726f6f6d5f6e756d62657201b167616d655f726174696e675f6c696d6974a44e4f4e45`

Compared with what we sent at 21:19:28, the only change that matters is limitTime NOSET → MIDDLE (the real env).
The other changes (lang, echoed options) are cosmetic.

**Minimal safe OWNER_PNAME**:
`bin(packb({'name': <name>, 'xuid_one': '', 'psn_user_id': bin(packb({'account_id': <pid>, 'online_id': '', 'is_valid_cache': 'YES'})), 'is_deleted': 'NO'}))`
This is the form we already send; it parsed without trouble on both copies [log].
- psn_user_id must be bin or str bytes of a valid msgpack map. Never send an inline `[]` or `{}`: 0x141c190a0 then
  fails and leaves the output count and pointer as uninitialised stack. Only the out-struct's +0 and +0x18 are
  zeroed before the call (0x141d04ea4 area), so the loop runs over garbage. That explains the crash of both games
  with `psn_user_id: []` [static + log 21:19:48].
- Leaving the psn_user_id key out entirely is also safe: no unpack is attempted, and account_id defaults to 0.
  But keep the proven form.

## Bonus: second player-list push 50 ms after the reply → process exit
Not settled. The ROOMPLAYERLIST consumer 0x141096320 rebuilds the room's player list on every message: it starts
with 0x14103d790 / 0x141095760 and ends by updating the tick that PostCreateJoinRoom st1 waits on. PostCreateJoinRoom
st2/st3 (0x14103fcf0 → 0x14103fb80/0x14103e630, then CmdEntryGame) works with that list. A rebuild in the middle of
that window probably invalidates pointers st2 is still holding [guess]. The later UPDATE push, sent after ENTRY_GAME
(21:17:08,222), did no harm [log]. Rule for now: no player-list push between the REQWATCHL_ROOMPLAYERLIST_NTL reply
and the owner's ENTRY_GAME.

## Still unknown
- The default values of the refine-search flags lobby+0x48/+0x4c/+0x50 (assumed 0). They are irrelevant unless the
  player opened Refine Search.
- What the owner_lang code (0x1410526a0 → enum function 0x141b99e80, an obfuscated jump) maps "en" vs. "US" to.
  It is display only.
- Live check: send the corrected UPDATE after SET_GAMEENV. Copy 2 should then show "Number of rooms 1" and the row.
  That is the first proving signal; stop the test there.
