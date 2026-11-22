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
- **Graphics DO reach the game through the Extra Update [live 2026-11-22].** The Extra Update
  (`download/ExtraUpdate.bin`, a CPK with `common/etc/version/version_xu.bin`) is bound at every boot with
  priority 6 (above the data packs and the Live Update) and right after a download:
  1. A CPK with 2,910 magenta flags (`common/render/symbol/flag/*.png`) + an edited `eng/string/all.str` put by
     hand at copy 2's `download/ExtraUpdate.bin` → after boot the version table had xu 101 / required DP 100
     (probe: 0x141989390(0x1414a0b00(), 0x13), 0x1414a0be0(…, 0x13)), and Kick Off → Local Match team select
     showed **magenta flags**. The menu text did not change (all.str is loaded before, or from another place).
  2. The test server listed a second one (1.02, green flags) as `extra_file_list` in CMD_GET_DLC_FILELIST: the
     game fetched it at login — **only over HTTPS** (a plain http:// address got a TLS handshake and "New data
     asset download unsuccessful"); with `https://ntl.service.konami.net:8080/liveupdate/xu2.cpk` (the mod sends
     the NTL names to our server, whose certificate is for them) it sent `GET /liveupdate/xu2.cpk` with
     `Range: bytes=0-6094847`, saved the file byte for byte as `download/ExtraUpdate.bin`, version table xu 102,
     and the team select showed **green flags without a restart**. Screenshots: scratchpad
     extra-update-magenta-flags-boot.png, extra-update-green-flags-proof.png.
  - version_xu.bin (40 B, four 10-byte fields): content version ("1.02" → compared with the entry's
    `dlc_version` "102"), program version "1.01.00", **required data pack version** (must be ≤ the installed one
    and the same major: "1.00"; else unbound at boot, 0x140404449 / 0x1404068e6), build label.
  - **No overall time limit** [static]: the Extra Update downloader (UtilHttpDownloadFile → its own thread
    "HttpDLThread", 0x1421b05b0) sets only WinHTTP per-step timeouts (0x1421b1e00: WinHttpSetTimeouts(resolve 0,
    connect [+0x10], send 60 s, receive [+0xc])), unlike the Live Update's HttpsTransaction with one 120 s limit
    for the whole transfer (default 60 s, 0x14116fa3b; checked at 0x141170480). So a big graphics pack can
    download as long as data keeps coming. It asks with `Range: bytes=0-<size-1>` and resumes with
    `bytes=<n>-` (dlData.bin + ".unverified"): the server answers 200 for the whole file (as seen live) and 206
    for the rest (httpgate._range).
- **Kits change in a match through the Extra Update [live 2026-11-22].** An Extra Update (1.03) put at copy 2's
  `download/ExtraUpdate.bin` with all 836 other `Asset/model/character/uniform/texture/#windx11/u<team>p1/p2.ftex`
  holding Arsenal's u0101p1.ftex (1 GB, every entry a copy): offline Local Match Anderlecht v Anderlecht → at kick
  off all 20 outfield players wore Arsenal's red home kit (goalkeepers, not swapped, as usual); fans in the stands
  wore it too. Screenshot: scratchpad extra-update-kits-proof.png. So in-match 3D textures (and very likely faces,
  boots, balls, stadium files) come from the Extra Update. Audio (common/sound/menu, eng/sound/acb|awb commentary,
  common/sound/match) not heard, but:
- **The menu music list comes from the Extra Update [live 2026-11-22].** `common/sound/menu/playlist/Playlist.xml`
  (dt40; `<CueList Num="25">` of `<Label CueName="BGM0_LIC_MENU_01" Id="19000" Title=… Artist=… Offset=…/>`, cues
  in `common/sound/menu/acb|awb/BGM_P2P` 191 MB and `BGM` 35 MB) with every Title set to "XU TEST n", put in an
  Extra Update → Option/OpSoundTrack ("Menu BGM", flow jump 0x14149b790(mgr, 0x1030008, 0x14265a730)) listed
  "XU TEST 1…". So new menu music = a Playlist.xml entry + its cue in a rebuilt ACB/AWB (CRI ADX2: HCA audio;
  community tools exist, none in the repo). Commentary banks: `eng/sound/acb|awb` (dt41_eng_all, 861 MB); other
  languages' sound configs ship in dt80 (ara, bra, can…), their audio isn't installed here.
- **The Live Update applies offline too [live 2026-11-22].** Kick Off → Local Match runs LiveDataSetFlow
  (CMD_GET_DLC_FILELIST; with no update: "No new Live Update available"). With the converted option file
  (EDIT.test2) offered by the test server and downloaded at login: Messi was "L. MESSI" in the DB at the main menu,
  then after entering Local Match the dialog read "No Live Update changes to Condition Rating…" and the DB had
  **"EDIT MESSI"**. So the server's squads also reach offline Local Match (the team select's RS button switches
  "Live Update Data/Uniform Ratings"). The earlier "online modes only" holds for the title/main menu.
- **Save files can be read and written** (tools/pessave.py): every save type round-trips byte for byte
  (SYSTEM00000000, LIVE00000000, EDIT00000000), the game accepted our re-packed EDIT00000000.
- **Live Update limits** (live.md): one Live Update + 16 Variation files + the Extra Update, 120 s per download,
  `size` = exact file length; the server already serves `<data>/liveupdate/*.cpk` (liveupdate.py).
- **Graphics through a Live Update (not the Extra one): not shown** [live]. A Live Update replacing all 2,911
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
- +0 team id; three tactics of 0xa0 bytes from +4 = the team's game plans: **tactic 1 = the Tactics.bin entry
  without bit 24, tactic 2 = the one with it** (37 teams list the bit-24 plan first in Tactics.bin; their two
  formations happen to be equal in the stock data, so file order looked right at first); tactic 3 = a copy of 1.
  Each tactic = 3 formation sets × (11 role codes + 11 x/y byte pairs) = TacticsFormation.bin rows
  `{tactic, role, set<<20 | slot<<16 | y<<8 | x}`, then 61 settings bytes (+0x63) = Tactics.bin's packed word
  (`{tactic id, team id, word}`, 12 B; 2 per team), **all 1,338 match**:
  - toggles (0/1) byte 0 → bit 25, 1 → 30, 2 → 28, 3 → 23, 4 → 29, 5 → 27, 6 → 26 (the 7 two-way settings:
    attacking style, build up, attacking area, positioning, defensive style, containment area, pressuring; which
    is which not checked in the menu);
  - levels, stored minus 1: byte 44 → bits 0..3, 41 → 4..7, 43 → 8..11 (1..10: support range, defensive line,
    compactness by range; names unchecked), 42 → bits 19..20 and 45 → 21..22 (1..3);
  - bits 12..18 aren't in the option file (no byte or Team.bin field matches; kept from the game), bit 31 never set;
  - bytes 9, 14, 25, 30 are per team (equal in all 3 tactics): 9 = 0..9, 25 = 0/11/12/13/16, 14/30 = 0/127
    (advanced instructions + targets?); not in Tactics.bin or Team.bin [unknown source].
- +0x1e4: 40 bytes 1..39 (an order list, unused here); +0x20c: 10 squad-list indexes (0xff = none) = the
  PlayerAssignment role bits: index 0 → bit 19, 1 → bit 17, 3 → bit 18, 4 → bit 16, 5 → bit 20, **9 → bit 21
  captain** (2 has no PA bit).
- Proof: from a fresh option file Player.bin, Team.bin, PlayerAssignment.bin (row ids and order kept),
  Tactics.bin and TacticsFormation.bin all rebuild **byte for byte** equal to the game's files (2026-11-22).
- Team names: Team.bin has the name in several fields (languages; +0x5b6 holds e.g. "UNITED STATES" where +0x170
  has "USA"; +0x566 = the unlicensed short name "WHA" where +0x372 has "WHU"). The game fills the option file from
  one of them, so a name found in any of these fields is not a rename.
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

## Game data editor (2026-11-22, gamedb.py)
- The admin page edits Player.bin (PMAP parameters, names +0xfb / shirt +0x81 and +0xbe, nationality +0x1c bits
  9..17), Team.bin (names +0x170 and +0x5b6, short +0x372 / +0x566 when set) and PlayerAssignment.bin (row = shirt-1
  | slot << 10 | role bits 16..21; a kept player keeps row id and roles) on top of the offered Live Update and sends
  edits.cpk (that update's other files copied raw, version above all). **Proven live 2026-11-22:** Messi renamed
  "EDITOR MESSI", Speed 99, moved FCB → Arsenal no. 4 with the editor → copy 2 downloaded edits.cpk (165.75) at
  login → CHECK_CHEAT 25D6E9E6…60E7 / 1E218ACE1DFF1E24… on entering Online Tournament, exactly pesdb.py's
  prediction for the edited tables.
- **Player ids are full u32s.** 637 pairs share the low 18 bits (0x32f D. BECKHAM, 0x4032f R. MEALE); 1000 ids have
  bit 30 set (0x40021727). Masking with 0x3ffff (as db.md's "bit 18 is a flag" suggested) mixes players up; the
  option-file conversion did that and now uses the full id.
- Real data: 18452 players, 669 teams, squads 20..40 (national teams 23), 2093 players without a team (free agents,
  legends), 906 in two teams (club + national).
