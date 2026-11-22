# Updated game data (squads, ratings, option files): what reaches an online match — 2026-11-22

Goal: friends on our server play with current squads. Findings from static RE plus live tests with copy 2 on the
test server (probe), 2026-11-22.

## Headline results
- **Only the Live Update reaches online play.** Entering any online mode reloads the whole player database from
  the game's files plus the bound Live Update (LiveDataSetFlow → 0x1414b7560 → full loader). Edit data (the option
  file, EDIT00000000) is **not** applied online, and **not re-applied after a Live Update bind** either:
  [live] an edit that added a player was in the DB after entering Edit, gone after entering Online Tournament; with
  a Live Update offered, CHECK_CHEAT gave exactly the pesdb.py live-mode value (25D6E9E6…6095), DB live mode 1,
  no edit player. (dbgaps.md §3c "edits are re-applied after the live data" is wrong for these paths.)
  - So community **option files** change only offline modes. For online, their content must be turned into a
    Live Update (Player.bin / Team.bin / PlayerAssignment.bin …).
  - Community **patches** that ship `common/etc/pesdb/*.bin` in a CPK (DpFileList) already are Live Update
    material: copy those tables into a Live Update CPK with version_wu.bin; no conversion.
- **Option file → Live Update works end to end [live 2026-11-22].** `tools/pesedit.py livedata EDIT OUT.cpk`
  applies the option file's edited players (params, names, nationality), team names / coach and squads (ids,
  shirt numbers, order) to the game's own tables and writes a Live Update CPK. Proof: an option file in the game's
  own edit format (Messi Speed 81→82 and renamed; Messi moved FCB → Arsenal as no. 30) converted, served by the test
  server, downloaded by copy 2: CHECK_CHEAT `25D6E9E6091EAC7425E05AF193DA6094` /
  `1E218ACE1DFF1E3EDBE591735FF8746A`, **exactly** the converter's prediction. Self-check: a fresh option file
  converts to tables with the base sums (…6095 / …746A); its 669 squads equal PlayerAssignment.bin.
- **The server does it by itself [live 2026-11-22, branch option-liveupdate]:** `EDIT00000000` dropped into the
  test server's `<data>/liveupdate/` → `pesoption.py` built `option-EDIT00000000.cpk` in 2.9 s at startup (version
  165.62 = hours since 2025) → offered → copy 2 downloaded it on entering Online Tournament → CHECK_CHEAT
  …6094 / 1E218ACE… as predicted, and the team select's Argentina squad list showed **"EDIT MESSI"**.
- **A Live Update changes online modes only [live 2026-11-22].** With the converted update saved
  (LIVE00000000), Messi was "L. MESSI" in the database at the title screen after a restart and still at the main
  menu after login; "EDIT MESSI" only inside Online Tournament. The stored update is bound and the DB reloaded
  with it only on entering an online mode (LiveDataSetFlow); offline modes keep the game data + the player's own
  option file. (The handler's bind is vfunc +0x10 of the vtable at 0x1429429c8, 0x1413f8e10.)
- **Save files can be read and written** (tools/pessave.py): every save type round-trips byte for byte
  (SYSTEM00000000, LIVE00000000, EDIT00000000), the game accepted our re-packed EDIT00000000.
- **Live Update limits** (live.md): one Live Update + 16 Variation files + the Extra Update, 120 s per download,
  `size` = exact file length; the server already serves `<data>/liveupdate/*.cpk` (liveupdate.py).
- **Graphics through a Live Update: not shown so far** [live, inconclusive]. A Live Update replacing all 2,911
  team emblems / flags (`common/render/symbol/flag/*.png`, every one in dt15 + dt80) downloaded and bound, but the
  Online Tournament team select still showed the real flags, also after a restart. The stored Live Update is bound
  only when entering online mode (LiveDataSetFlow), after boot; flags may be preloaded at boot. A text change in
  `eng/string/all.str` also did not show (text is loaded at boot). Untested: files read only in a match (faces
  `Asset/model/character/face/real/<player id>/…`, kits `…/uniform/…`).

## Save container (pessave.py)
As dbgaps.md §3b, plus:
- an **empty section has an all-zero digest** (LIVE has no PNG): 64 zero bytes instead of SHA-512("").
- section 2 (the data) starts with an 80-byte header: u32 type (0x0e EDIT, 0x10 SYSTEM), u32 0x50, u32 payload
  size (file data − 0x50), u32 10100 (game version 1.01.00), u32 100, u32 100 (data pack versions), …, u32 at +0x20
  (0x38 EDIT, 0x46 SYSTEM), 32 bytes at +0x30 (a digest; **not** SHA-256/512/MD5 of the payload; the game does not
  reject a stale one: it loaded our file and wrote a new value on save).

## EDIT00000000 (option file) data layout
File data = 80-byte header + a 0x2c-byte count header at 0x50 + fixed-size tables (11,011,025 bytes for a fresh
file; 10,995,800 bytes of data). Count header (field table 0x1434f4b90, getters `movzx eax, word [rcx+N]`):

| data off | field | meaning |
|---|---|---|
| 0x54 | f1 | 10100 |
| 0x58 | f2 | 100 |
| 0x60 u16 | f7 | **edited players** (kind 0) |
| 0x62 u16 | f8 | ? |
| 0x64 u16 | f9 | teams (kind 1), 669 |
| 0x66 u16 | f10 | coaches (kind 2), 698 |
| 0x68 u16 | f11 | kind 3 (0x2f8 records), 55 |
| 0x6a u16 | f12 | kind 4 (0xbc records), 46 |
| 0x6c u16 | f13 | kind 5 (0 fresh, 8 after entering Edit: the game made 8 records) |
| 0x6e u16 | f14 | ? |
| 0x70 u16 | f15 | squads (kind 6), 669 |
| 0x72 u16 | f16 | kind 7 |
| 0x74 u16 | f17 | kind 8 (0x274 records), 669 |
| 0x76 u16 | f18 | kind 9 |
| 0x78 byte | f4/f5/f6 | flag bits 0..2 |

Kind table 0x1434f45a0 (kind → field): (0,7) (1,9) (2,10) (3,11) (4,12) (5,13) (6,15) (7,16) (8,17) (9,18).
Apply loop 0x141ef7c60 (record sizes): players 0x138 → 0x141ef8660; teams 0x24c → 0x141ef89c0; coaches 0x58 →
0x141ef8120; kind 3 0x2f8 → 0x141ef8380; kind 4 0xbc → 0x141ef8890; kind 5 → 0x141ef7f70; squads 0x11c →
0x141ef8b00; kind 7 → 0x141ef8d40; kind 8 0x274 → 0x141ef8c00.

- **Players**: 30,000 slots of 0x138 from data 0x7c (ends exactly at 0x8ed2fc, the team table). Count = f7.
  **Not the Player.bin layout** (our test copied a Player.bin record: the game read +0 as the player id, made a new
  player 108 with a garbage name). The edit player record is described by the game's field table 0x141402576
  (+0x20f05a3) = 122 fields `{id, getter, setter, …, max, bits}`; dump in
  `data/edit_player_fields.txt`. Key fields: 0 = u32 +0 **player id**; 11 = u32 +4; 12 = u16 +8 (→ DB rec+0x144,
  nationality); 16/17 = bytes +0xa / +0xb (max 250 / 150: height? weight?); abilities = 7-bit values 0..99,
  four per dword at +0x10, +0x14, +0x18, +0x1c, +0x24 (and +0x20, +0x28, +0x2c), stored **as shown** (no +40);
  fields 51..63 = 13 × 2-bit position proficiencies (+0x28 / +0x2c); 66..112 = 1-bit skills / COM styles at +0x30
  and +0x34; 120/121 = bytes +0xc / +0xd. Names follow the 0x38-byte packed block [not mapped yet].
  Edit → DB copy: 0x141ef8540 loops field 0..0x7d through 0x141f10ce0 (switch at 0x141f10d53: fields 11..17, 64,
  122..124 special; 18..46 → setter 0x1412dc920-style group with index field−0x12; 51..63 → 0x1412dc920(rec,
  field−0x33, v), …). An edit player whose id is unknown to the DB is **added** as a new player.
- **Teams**: 0x24c records from 0x8ed2fc, sorted by team id: +0 team id, +4 coach id (FCB 380 = Quique Setién),
  +0x68 name (70 B, English), +0xae short name ("BAR"); no roster here.
- **Squads**: 0x11c records (base ≈ 0x9d4648, same order as the teams): +0 team id, +4 40 × u32 player ids,
  +0xa4 40 × u16 shirt numbers, +0xf4 40 bytes [unknown, zero in a fresh file]. Messi: ARG index 8, shirt 10.

## Tools
- `tools/pessave.py info|data|pack` — save container (all types).
- Test recipe used: copy 2 via probesession (`up 2`), jump flow `Edit/EditDataCheckProcess` (string 0x1428e14c8)
  → agreement dialog (Right, Return) creates EDIT00000000; `Edit/Save/EditDataSaveProcess` (0x1428e13b0) saves.
  In-memory DB: `[0x1414b6a60()+0x48]` = 0x17c-byte player records sorted by id (+0x30 id, +0x38 name,
  +0x75 shirt name); its packing differs from Player.bin.

## Formations, captain and set-piece takers (kind 8, 0x274 per team)
After the squads (fresh file: 0xa09880, count at data 0x74), records in team order:
- +0 team id; three tactics of 0xa0 bytes from +4 (the team's Tactics.bin entries in file order take 1, 2, …);
  each tactic = 3 formation sets × (11 role codes + 11 x/y byte pairs) = TacticsFormation.bin rows
  `{tactic, role, set<<20 | slot<<16 | y<<8 | x}`, then settings bytes (Tactics.bin's packed word: not mapped).
- +0x1e4: 40 bytes 1..39 (an order list, unused here); +0x20c: 10 squad-list indexes (0xff = none) = the
  PlayerAssignment role bits: index 0 → bit 19, 1 → bit 17, 3 → bit 18, 4 → bit 16, 5 → bit 20, **9 → bit 21
  captain** (2 has no PA bit).
- Proof: from a fresh option file all 18,582 PlayerAssignment rows (shirt, order, roles, captain) and all 1,338
  tactics' TacticsFormation rows rebuild **byte for byte** equal to the game's files.
- Player ids in squads can exceed 0x3ffff (bit 18 = a flag): keep them (dropping them changed entry_check_sum).

## Edit player field → pesdb parameter (proven on 60 players)
Obtained by running the game's DB→edit converter field by field through the probe (`0x141f10770(ctx → zeroed
dword, DB record, edit record, field)` for field 0..0x7c; the loop is 0x141ef72c0, whose 5th argument must be 0)
on 60 random players and matching every field against pesdb.py's decode of the same players: all exact, values
as shown in the game (no +40). Table `EDIT_TO_PARAM` in pesedit.py:
- 11 = player id (u32 +4), 0 = the DB id the edit applies to (0 = field 11; an unknown id adds a new player),
  12 = nationality (u16 +8), 13 age, 14 registered position, 15 stronger foot left, 16 height (byte +0xa), 17
  weight (byte +0xb), 18..42 the 25 abilities, 43 weak foot usage, 44 weak foot accuracy, 45 form, 46 injury
  resistance, 47 p48, 48 p49, 50 p50, 49 = p43 [order only: all 0 in the sample], 51..63 position ratings GK, CB,
  LB, RB, DMF, CMF, LMF, RMF, AMF, LWF, RWF, SS, CF, 64 playing style in **UI order** (table 0x142979080 maps
  stored → UI), 65..71 COM styles, 72..112 skills in the skill-menu order (pesdb SKILL_UI_ORDER), 113..119 motions
  (dribble hunching/arms, running hunching/arms, corner, free kick, penalty), 120 goal celebration.
- 1..10, 49, 121: flag bits, all 0 in the sample [unknown: "edited" marks?].
- Names: +0x36 name, +0x73 shirt name, +0xb0 second shirt name, +0xed Japanese name (61 B each).
- Not in the edit record: associated club, loan, contract, value / caps / salary, second nationality (Player.bin
  keeps the game's values; none is in CHECK_CHEAT).

## Next
1. Server feature: the host drops an option file (or a patch's pesdb tables) into the admin page; the server
   builds the Live Update with pesedit.py from the host's own game data (dt80) and offers it to every friend.
2. Patch CPK → Live Update: CRILAYLA decompression in cpkread (patches compress), copy common/etc/pesdb/*.bin.
3. Faces / kits through a Live Update: test with a file read only in a match.
