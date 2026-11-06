# DB / Live Update gaps (db.md "Still unknown", live.md §6), static pass 2026-11-06

> **Live checks (2026-11-06, livetests.md):** the Live Update recipe with Player.bin + Team.bin + PlayerAssignment.bin edits was built and served; the measured CHECK_CHEAT sums matched pesdb.py only while the server kept listing the update (new rule, see live.md corrections). The Team.bin short-name check (BAR → BRX) was not reached on screen. The save decrypter mentioned below stays in the working folder (not published).


PES2021.exe 1.01.00 (Steam), static reading only. Addresses are VAs.
Tags: **[static]** read in code, **[data]** checked against the shipped pesdb files,
**[verified]** checked against a real file on disk, **[inferred]**, **[guess]**.
Scratch tools (not published): `savedec.py` (save decrypter), `ftab.py` (edit field table dump), dumps in `dumps/`.

## Summary
| question | status |
|---|---|
| p48 / p49 names | **settled**: p48 = STAR (0..7), p49 = COOL (0..2) [static] |
| p43 / p50 names | not named in the exe. p43 is probably "stronger hand" [guess]; p50 is a hidden outfield dribble-style value [guess] |
| motion order 51–58, celebration 2 | **settled** [static] |
| Player.bin 0x14 / 0x17 / 0x18 | **settled**: market value, international caps, fixed salary [static + data] |
| 0x3c bit 18 / bit 28 | effect is settled [static]. Meaning is [inferred] |
| PA bits 16..20 set-piece roles | **settled** [static] |
| Team.bin 0x20/0x34/0x38/0x40..0x57 | mapped to the in-memory team. Meanings are [guess]. Nothing here affects any checksum |
| save master key | **settled and verified**: SHA-512 of the game title. The save container is fully decoded |
| edits before CHECK_CHEAT | **settled**: yes, edited stats change param_check_sum [static]. Live test below |
| `*4.bin`, InstallVersionPlayer, PlayerDeleteList | **settled** as far as the server needs |
| live.md §6 | all items settled statically. Two optional confirmation tests below |

---------------------------------------------------------------------------------------------------
## 1. Player.bin

### 1a. The edit-player field table (new, the key to most answers)
- `0x141402570(rec, &desc, field_id)` looks up a 122-entry descriptor table at **0x1434f2b20** (0x30 bytes each:
  id, getter, setter, min, max, bit width) [static].
- The records it describes are the **edit-data player record** (0x138 bytes). Its layout is **not** the Player.bin
  layout. Examples: id 0 = dword +0, height = byte +0xa, abilities are 7-bit fields [static].
- `0x141f10ce0` copies one edit field into the DB player (jump table at 0x141f11400). `0x141ef8540` calls it for
  field ids 0..0x7c [static]. The edit field → param mapping:
  - 12 → nationality (rec+0x144); 13 → p45 age; 14 → p0 position; 15 → p42 foot; 16/17 → height/weight;
  - 18..46 → the 29 abilities; 51..63 → position ratings; 64 → playing style; 65..71 → COM styles; 72..112 → skills;
  - **47..50 → accessor 0x1414c0240(0..3) = p48, p49, p43, p50** (table 0x142979048 = [48, 49, 43, 50]);
  - **113..121 → motions** through 0x141f114e0(ui 0..8);
  - 122..124 → names.

### 1b. Motion order and celebration 2 — settled [static]
- The edit motion screen 0x140c42600 walks row table 0x142707a70 (field ids) beside label table 0x142707b90
  (message ids, `eng/string/all.str`):

  | edit field | label | param |
  |---|---|---|
  | 0x71 (113) | Dribbling → Hunching | **p51** (0..4) |
  | 0x72 | Dribbling → Arm Movement | **p52** (0..9) |
  | 0x73 | Running → Hunching | **p53** (0..4) |
  | 0x74 | Running → Arm Movement | **p54** (0..9) |
  | 0x75 | Kick Motion → Corner Kick | **p57** (0..9) |
  | 0x76 | Free Kick | **p55** (0..19) |
  | 0x77 | Penalty Kick | **p56** (0..6) |
  | 0x78 | Goal Celebration → Full Celebration 1 | **p58** (0..162) |
  | 0x79 | Full Celebration 2 | **none in Player.bin** |

- The ui → param pairing comes from 0x1414c01c0 (pairs at 0x1429791b0). Edit field = 0x71 + ui index.
- **Celebration 2** has no Player.bin field. For ui 8, 0x1414c01c0 returns 108 (out of range). The setter
  0x141f114e0 writes it to **rec+0x148 bits 20..27**, and 0x141fe9e50 reads it from there [static]. It comes only
  from edit data (edit byte +0xd). It is not one of the 108 parameters, so it is **not in param_check_sum**.
- So db.md's [inferred] motion names are now [static].

### 1c. p48 = STAR, p49 = COOL [static]
- Match-player struct (60 bytes): built by 0x141fe9b00. Min/max per byte offset at 0x142613c60.
  - 0x141fea170 writes **p48 → offset 0x37** and **p49 → 0x36** (table 0x14352abf8).
  - 0x141fe9ba0 writes **p50 → offset 0x10**.
- AI/Lua attribute lookup 0x1414776e0 maps its enum through **table 0x142960960** to those offsets. Its names are
  the pointer array 0x1434f8c90:
  - enum 34 **STAR → 0x37 (p48)**;
  - enum 33 **COOL → 0x36 (p49)**;
  - enum 30 STRONGER_HAND → 0x33;
  - enum 31 STRONGER_FOOT → 0x34; enum 32 WEAK_FOOT_FREQUENCY → 0x35.
- The whole chain checks out: AGE → 0x30 ← p45, HEIGHT → 0x31, WEIGHT → 0x32, all 26 abilities → their offsets.
- **p48 use [static].**
  - 0x140c542a0 gives each starting outfielder a weight `{90, 180, 360, 720, 1500, 3000, 6000, 18000}[p48]`
    (0x142710248) and picks one at random by weight. This is the MatchMenuModelViewManager feature-player pick.
  - 0x140c39690 picks the non-GK player with the highest p48.
  - 0x141567300 (Master League value) uses it through 0x141517bc0.
  - Data: stars are 7 [data].
- **p49 (COOL).** Composure-type value [inferred from the name].
  - Read once by the match engine (0x1407e28fa).
  - The edit apply 0x141ef8540 **forces p49 = 1 for created players** (ids 0x80000000..0x8000009f) [static].
  - Data: 0 = 7988, 1 = 8731, 2 = 1733 players. Level 2 = Totti, Buffon, Kahn, Batistuta, Puyol [data].

### 1d. p43 and p50 — not settled
- **No code reads p43 by a constant index.** Its only users are the clamp, the checksum and the edit copy.
  Struct offset 0x33 (STRONGER_HAND) is never filled from p43.
  - Data: 44 players; 25 are GKs; **37/44 are left-footed** (baseline 22 %) [data].
  - **[guess] "stronger hand = left"**, a leftover hidden value.
- **p50** sits at struct offset 0x10, between the PK motion (0x0f) and the CK motion (0x11).
  - The match engine reads it at 0x1407b49b9, 0x1407b4dba, 0x1407b62f5 and 0x1407bc495, next to dribbling (0x17)
    and tight possession (0x19).
  - Data: 0 GKs at 1 or 2; mean dribbling 75 vs 66; examples Ronaldinho, Rivaldo, Cruyff, CR7, Quaresma.
  - **[guess] a hidden dribble/feint style.**
- No label exists in all.str and no edit row uses fields 49/50, so **no screen can show them**. No useful live
  test; the server only needs them as numbers (they enter param_check_sum).

### 1e. Player.bin 0x14, 0x17, 0x18 — settled
- **u32@0x14 & 0xffffff (rec+0x174) = market value in units of €100** [static + data].
  - 0x141569090 (Master League valuation; used by the "renegociation"/"createOffer" screens) returns rec+0x174
    directly when it is non-zero. Otherwise it computes a value (capped at 99,999,900).
  - Data: Messi 1,120,000 = €112m, Neymar €128m, Mbappé €180m (the Transfermarkt values of 2020).
- **byte@0x17 (rec+0x149) = international caps** [static + data].
  - 0x14134aa50 does `inc byte [player+0x149]` after a match in game modes 9/10 when the team kind (team+0x420
    bits 26..28) is 1, i.e. a national team.
  - Data: Buffon 175, Ramos 170, Casillas 167, CR7 164.
- **u32@0x18 & 0xffffff (rec+0x170) = fixed annual salary in €100 units** [inferred + data].
  - 0x1415681c0, the sibling of the value function, reads rec+0x170 the same way.
  - Data: 24 players are non-zero (Falcao €9.0m, Fàbregas €7.2m).

### 1f. 0x3c flags [static effect]
- **bit 28 → rec+0x148 bit 19.** Read at 0x140fac2f2.
  - When the bit is clear and the player's nationality equals the team's country (team+0x418), the player counts as
    an eligible national.
  - The 2248 holders are all legends plus the fictional players (ids 52xxx–58xxx).
  - [inferred] "exclude from national-team call-ups".
- **bit 18 → rec+0x150 = 0x13.** Only Messi and CR7 have it (4 records, including the young and legend copies).
  [guess] a special-player class or unique asset id.

## 2. PlayerAssignment set pieces and Team.bin

### 2a. Set-piece bits — settled [static]
- 0x1414ff320 copies `[captain +0x5d1, slots +0x5c8..+0x5cd]` into a 7-entry role array.
- 0x1408eb510 labels role 0..6 as Captain / **Long FK Taker** / **Short FK Taker** / **FK Taker 2** /
  **Left CK Taker** / **Right CK Taker** / **PK Taker** (message ids 0x3e0018, 0x14000a, b, 0x14001e, c, d, e).
- So slot k = role k+1. With 0x1414f06a0 (slot → PA bit):

  | role | PA bit |
  |---|---|
  | Long FK | 19 |
  | Short FK | 17 |
  | FK Taker 2 | 17 (same player as Short FK) |
  | Left CK | 18 |
  | Right CK | 16 |
  | PK | 20 |
  | Captain | 21 |

- This confirms sweep.md's inference.

### 2b. Team.bin flag fields → in-memory team (team builder 0x1414f5c43..0x1414f6111) [static]; meanings are [guess]
- **0x20**: always 0 in the data. If set, it is a team reference → team+0x628.
- **0x34 & 0xffffff → team+0x644.** Clubs only: FCB 2,335,700, MUN 1,454,633; national teams 0.
  [guess] club budget or revenue (€100 units).
- **0x38 & 0x7fffff → team+0x648.** Round numbers: FCB 5,000,000, MUN 1,200,000.
  [guess] supporter count.
- **9-bit country ids** (stored only when non-zero):
  - 0x38>>23 → +0x650; 0x40&0x1ff → +0x64e; 0x40>>9 → +0x64c (own country);
  - 0x40>>18 → +0x666; 0x44&0x1ff → +0x664; 0x44>>9 → +0x668;
  - 0x44>>18 → +0x418 (team country, already known).
  - [guess] Master League market/scouting regions.
- **7-bit values:** 0x4c&0x7f → +0x652 (national teams = 100), 0x4c>>7 → +0x653, 0x48>>23 → +0x654,
  0x4a&0x7f → +0x655, 0x4c>>21 → +0x656, 0x4c>>14 → +0x657.
- **bytes:** 0x49 → +0x66c, 0x48 → +0x670, 0x37 → +0x674.
- **Packed into team+0x63c:**
  - 0x40>>27 (≤18) → bits 0..4; 0x44>>27 (<11) → bits 5..8;
  - 0x50&0x3f (≤40) → bits 9..14; 0x50>>6 (≤40) → bits 15..20; 0x50>>12 (≤10) → bits 21..24;
  - 0x50>>19 → bits 25..26; 0x50>>27 → bits 27..28; 0x50>>21 → bits 29..30.
  - 0x50>>23 → team+0x640 bits 0..1.
- **Other flags:**
  - 0x52&7 → team+0x420 bits 29..31 (through table 0x14297d250);
  - 0x48>>30==2 → team+0x424 bit 1; 0x50>>25==2 → bit 0; 0x44>>31 → bit 2;
  - 0x4c>>28 = team kind (known).
- entry_check_sum uses only the team id and the roster, so **none of these matters to the server**. Naming them
  would need the Master League screens; not worth a live test.

## 3. Save key and edit data

### 3a. Master key — settled and verified
- The save-system ctor 0x14140b7e0 puts two **SHA-512** states at obj+0xf0 and obj+0x1b8 (IV at 0x14140b90b;
  K table at 0x142943fe0; compression 0x1414122d0).
- 0x14140ea40 hashes, as UTF-16LE without a NUL:
  - obj+0xf0: the title from 0x1414a18c0 → 0x1449eb9e0 = **"eFootball PES 2021 SEASON UPDATE"**
    ("eFootball Winning Eleven 2021 SEASON UPDATE" when [0x1434fce54]==1, the Japanese build);
  - obj+0x1b8: 0x1449eb3f0 = **"eFootball PES 2020"**. It is used when the save kind is 3 (only BEDIT has
    kind 3; [inferred] the PES 2020 edit import) [static 0x141410c9e].
- 0x1414133e0 serialises the 8×u64 digest big-endian; it is not a key input [static].
- **So the key is fixed per region, not bound to the account.**

### 3b. Save container format [static, verified on `…/Documents/KONAMI/…/292733975847239680/save/SYSTEM00000000`]
1. **Header, bytes 0x000..0x13f.**
   - k0 = raw[0x100:0x140] XOR SHA512(title).
   - Decrypt raw[0:0x140] with the MT stream keyed by k0 (16 LE u32 → init_by_array; the 0x14140e0f0 XOR recipe
     from db.md). Keep raw[0x100:0x140] as is.
   - The decrypted bytes 0..0xff are **four SHA-512 digests**, one per section.
2. **Section key.** k = XOR of the five 64-byte blocks of that header.
3. **Info block, 0x140..0x20f (0xd0 bytes).**
   - Decrypt with k, each u64 XOR 0xd0.
   - Fields: u32 sizes at +0x40 (section 2), +0x44 (section 1), +0x48 (section 0), +0x4c (section 3 length in
     UTF-16 chars).
   - Also the type name ("SYSTEM") and the title.
4. **Four sections from 0x210, in order 0..3.** Section i is decrypted with k, each u64 XOR i. Its SHA-512 must
   equal header digest i (0x14140f470: 0x14140fd93..0x14140fe05).
   - 0 = 384-byte display title ("System Data");
   - 1 = PNG thumbnail;
   - 2 = the data;
   - 3 = the **owner's Windows SID** as UTF-16 ("S-1-5-21-…-1000"; on Wine/Proton this is the prefix's SID).
   - All four digests matched on SYSTEM00000000 (235,970 bytes = 0x210 + 384 + 15412 + 219568 + 78).
5. **LIVE00000000** (CPK + 990 bytes) fits exactly: 990 = 0x140 + 0xd0 + 384 + 0 (no PNG) + 78 (SID). The CPK is
   section 2, byte for byte.
6. Whether the reader rejects a foreign SID was not traced.

### 3c. Edit data and CHECK_CHEAT — settled [static]
- The edit apply writes into the same DB that the checksum reads.
  - Edit apply: 0x141ef7c60 → 0x141ef8660 → 0x141ef8540 → 0x141f10ce0 → setter 0x1414c6e90. It writes into
    `[0x1414b6a60()+0x48]`.
  - param_check_sum 0x141128e90 sums the same `[0x1414b6a60()+0x48]` players.
- **So edited abilities, positions, skills, height, weight and age change param_check_sum.** Transfers change
  entry_check_sum. Names, kits and celebration 2 do not.
- After a Live Update is bound, LiveDataSetFlow state 0x1a re-runs "editLoadDataInLiveDataSet" (0x141efb1f0)
  unless flow+0x22c == 2.
  - +0x22c is the 5th ctor argument. Every TaskApplyLiveUpdate variant (0x140fdf9d0) passes 0 or 1 [static].
  - So the edits are re-applied after the live data, before JOIN_ONLINE_MODE / CHECK_CHEAT.
- **Live test (confirms it):**
  1. In Edit mode, raise Messi (id 7511) Speed by 1 and save the edit data.
  2. Log in with the server's normal "no Live Update" reply.
  3. Expected CHECK_CHEAT `param_check_sum` = **25D6DFBB091E75D525E090F993DBAE72**: lane 3 + 1 against the stock
     …AE73 [computed with pesdb's formula]. `entry_check_sum` stays 1E21A82F1DFF00C9DBE591735FF8746A.
  4. Read it from the server's stdout log line for CMD_CHECK_CHEAT. Stop at that line.
  5. Undo the edit afterwards, or delete the test EDIT file.

## 4. `*4.bin`, InstallVersionPlayer, PlayerDeleteList
- **`*4.bin` = the AFC Champions League set.**
  - Contents [data]: Team4 holds 32 Asian clubs (Kashima, Urawa, Jeonbuk, Al Hilal, Guangzhou…); Competition4
    holds AFC_CHAMPIONS_LEAGUE; Player4 has 1077 players; PlayerAssignment4, Coach4, Tactics4 and others.
  - **The exe never loads them** [static]:
    - the 28-name table 0x1434d6630 has no "4" names;
    - the loader 0x14125f310 builds only "cpk_dat/common/etc/pesdb/%s/%s";
    - no "4.bin" pesdb string exists apart from InstallVersionPlayer4.bin.
  - [inferred] Data for a regional build or a licence this Steam build doesn't use. Ignore it.
- **InstallVersionPlayer{,1..6}.bin = players added by a data pack** {u32 id, u32 dp version (100)}.
  - Loader 0x1414d93e0 is driven by LoadIdleThread (0x140af3180). It tags each player reference with the file
    index (bits 28..31, 0x1414bda70) [static].
  - Data: none of the 670 ids exists in dt10's Player.bin; all exist in dt80; 648 are in rosters [data].
  - [inferred] Lets the game know which players' assets came with DP N.
- **PlayerDeleteList.bin = players dropped by the update.**
  - Loaded by 0x1414f4630 (0x1414f4788) into the id list at specials-mgr+0xd8, next to the
    SpecialPlayerAssignment kind lists [static].
  - Data: all 555 ids are in Player.bin (dt10 and dt80) but **in no PlayerAssignment row** (retired or free) [data].
  - [inferred] Used to purge them from edit/ML squads.
  - It is also in the Live Update table list, so a live update can extend it.
- **None of the three is in either CHECK_CHEAT sum.**

## 5. live.md §6
- **`[netmgr+0x68]`** = GET_SVRVERSION `dlc_version` (sweep.md, static). Unchanged.
- **GET_SVRVERSION `livedata_version`**: stored at netmgr+0xa8 and never read (sweep.md). Unchanged.
- **version_wu.bin +0x14**: stored, never read; no rejection possible (sweep.md). Unchanged.
- **Save names and folder** [static + verified].
  - Save-type table 0x142944500 (0x30-byte rows) gives type index = slot − 10:
    - LIVE = 17 (slot 0x1b);
    - VARIATION00..VARIATION15 = 18..33 (slots 0x1c..0x2b);
    - EXTRAUPDATE = 34 (slot 0x2c).
  - These rows share the flag word 0x101000100010000. SYSTEM/EDIT differ: they live in
    `Documents\KONAMI\…\<steamid>\save`.
  - Observed name: LIVE00000000. [inferred] the variations are "VARIATION00" + "00000000"; the exact
    number-padding code was not found.
  - Encoding: the container in §3b, with the CPK as section 2.
- **ExtraUpdate.bin is a plain file** at **`.\download\ExtraUpdate.bin`** (game/files/download, next to dt80) [static].
  - The dir-type thunk 0x1414af5e0 → 0x144a0ff80 maps 1 → ".\temp\", 2/3 → ".\download\", others → ".\".
  - The handler 0x1413f8c90 binds it by that path at priority 6. Downloads go to ExtraUpdate.bin.unverified first
    (live.md).
- **What is re-read after a Live Update is bound: all 28 pesdb files** [static].
  - The flow calls 0x1414b7560(mgr, 0, mode). That clears the DB (0x1414bb970) and runs the full loader
    0x1414f23d0, which requests every index 0..0x1b from table 0x14297c0b0.
  - So **Team.bin, Country, Coach, Tactics…** are re-read through the CPK binder too. A Team.bin inside the
    Live Update CPK overrides the base copy the same way Player.bin did live.
  - The livedata name list (0x142bd0ec0) only lists what Konami shipped. It is not a filter [inferred].
  - Then edit data is re-applied (§3c).
- **PlayerWeekly outside mode 1.**
  - 0x1415106b0 returns PlayerWeekly's condition only when [mgr+0x38]==1; otherwise it returns the constant 5.
  - So outside mode 1 **PlayerWeekly has no effect on any sum** [static].
  - The success path of the flow (0x1420ae529) passes r8d = esi = 1. esi is set to r13+1 = 1 at 0x1420ac462.
    That matches the live result with the form term.
  - The other call (0x1420ae705) passes mode 2.
- **Optional confirmation test (Team.bin override):**
  1. Build a Live Update CPK (version_wu "1.02", dlc_version "102") with an edited `common/etc/pesdb/Team.bin`:
     FC Barcelona (team id 108) short name at record +0x372 "BAR" → "BRX".
  2. Add an edited PlayerAssignment.bin: Messi's FCB row, flags 0x3f2009 → 0x3f200a (shirt 11).
  3. Expected: the team select shows "BRX". CHECK_CHEAT `entry_check_sum` = **1E21A8301DFF00C9DBE591735FF8746A**
     (FCB lane 0 + 1).
  4. `param_check_sum` = the mode-1 value pesdb.py gives for the stock Player.bin (25D6E9E6…6095 with the shipped
     PlayerWeekly).
  5. Stop at the CHECK_CHEAT log line. Delete LIVE00000000 afterwards.
- **Optional (variation / extra names):**
  1. List `variation_file_list = [{name:"v00", url, size, hash, dlc_version:"101"}]` with a tiny CPK holding
     `common/etc/version/version_vd00.bin` ("1.01").
  2. List the ProgramData save folder. Expected: VARIATION0000000000 [inferred].
  3. For the extra, expect `game/files/download/ExtraUpdate.bin`. Its version_xu.bin needs field +0x14 ≤ "1.00"
     with the same major, or it is unbound.

## Corrections to the KB
- db.md: param names 48 = STAR, 49 = COOL; motions confirmed; celebration 2 is edit-only.
- db.md: 0x14 / 0x17 / 0x18 = value / caps / salary. The set-piece bits are as in §2a.
- db.md: the save key is not account-bound (SHA-512 of the title). The container is as in §3b.
- live.md §3: the "exact file name unknown" note is resolved (LIVE00000000 in ProgramData). ExtraUpdate.bin is in
  `.\download\`.
