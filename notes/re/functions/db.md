# pesdb: the PES 2021 player/team database (Player.bin, Team.bin, PlayerAssignment.bin, …)

Group "db". Static analysis of PES2021.exe 1.01.00 plus the pesdb files in the CPKs. Read-only: nothing was launched.
Parser: [`tools/pesdb.py`](tools/pesdb.py) (`python3 pesdb.py checksum|player <id>|team <id>|dump out.json`). Field-to-bit map of the
108 parameters: [`data/player_param_map.json`](data/player_param_map.json). The full JSON dump, the x86 bit-probe emulator and the disassembly
dumps are not published.

## Headline results
* **Both CHECK_CHEAT sums are reproduced exactly from the shipped data.** Using dt80_100E Player.bin, Team.bin and
  PlayerAssignment.bin with no edits and no live data: `param_check_sum = 25D6DFBB091E75D525E090F993DBAE73` and
  `entry_check_sum = 1E21A82F1DFF00C9DBE591735FF8746A`. These are exactly what our logs show [log + static]. This proves
  the whole field mapping: the 108 parameters, player id, height/weight, team id, roster and shirt numbers.
  * So both test clients ran the **unmodified dt80_100E database**: no option-file stat edits and no live update.
  * The base game alone (dt10, no update pack) gives `25BC682809B204E52260115093BA84FE` /
    `1E8FB88B1EA9DB9EDC059C0560E67AE3`. A client without the update pack would fail the P2P revision check against an
    updated client.
* **Correction to menus.md.** The per-player term "regstate(id)" (0x1415106b0) is **not** a registration flag. It is
  the player's **weekly condition (form arrow)** from **PlayerWeekly.bin**. Loader 0x1414f3640 fills the 30000 ×
  {id, cond&7} table at DB+0xe63de4. It is only used when the DB manager is in **live-data mode 1** ([mgr+0x38]==1).
  That mode is set by `LiveDataSetFlow` (0x1420ac3f0 → 0x1414b7560) after CmdGetLivedataList/CmdGetDlcFilelist.
  Otherwise the term is the constant 5, which is what our clients sent. [static]
  * In mode 1 the term is `cond` (default 2 if the id is missing; 5 is mapped to 2).
  * **If our server ever delivers live update data, every client's param_check_sum changes.** All clients must then get
    identical live data, or matchmaking by sum or revision check will split them.
  * pesdb.py also computes the mode-1 variant with the shipped PlayerWeekly: `25D6E9E6091EAC7425E05AF193DA6095`.
* The checksum uses only the **low 16 bits of the player id** (`movzx r13d, bx` at 0x141128f74): s = (id&0xffff) +
  Σparam[0..107] + (height&0xff) + (weight&0xff) + cond. The entry sum uses the full 32-bit player id and
  `shirt = (PA.flags & 0x3ff) + 1`. [static, verified]

## How the database is loaded (flow)
* **File table.** 0x1434d6630 holds 28 filename pointers (index → file). 0x1428bb100 holds the record size of each
  file. [static]

  | idx | file | rec | idx | file | rec | idx | file | rec |
  |---|---|---|---|---|---|---|---|---|
  | 0 | Ball | 0x8c | 10 | Derby | 0xc | 20 | StadiumOrder | 8 |
  | 1 | BallCondition | 8 | 11 | Glove | 0xcc | 21 | StadiumOrderInConfederation | 8 |
  | 2 | Boots | 0x130 | 12 | MyclubCoach | 0xc0 | 22 | StadiumWeight | 8 |
  | 3 | Coach | 0x64 | 13 | MyclubTactics | 0xc | 23 | Tactics | 0xc |
  | 4 | CoachDeleteList | 4 | 14 | MyclubTacticsFormation | 0xc | 24 | TacticsFormation | 0xc |
  | 5 | Competition | 0x24 | 15 | **Player** | **0x138** | 25 | **Team** | **0x5fc** |
  | 6 | CompetitionEntry | 0xc | 16 | **PlayerAssignment** | **0x10** | 26 | PlayerWeekly | 0xc |
  | 7 | CompetitionKind | 0x58 | 17 | PlayerDeleteList | 4 | 27 | TeamWeekly | 8 |
  | 8 | CompetitionRegulation | 0x930 | 18 | SpecialPlayerAssignment | 0x10 | | | |
  | 9 | Country | 0x58c | 19 | Stadium | 0x188 | | | |

* **Reading a file.** `0x14125f310(mgr, idx)` reads `cpk_dat/common/etc/pesdb/<name>` asynchronously ("%s/%s").
  `0x14125f290(mgr, idx)` returns the buffer and `0x14125f2c0` returns the record count. Init 0x1414f23d0 loads all 28
  files. The game uses the CPK priority order, so dt80_100E overrides dt10, which overrides dt00. The files use the
  WESYS wrapper: `ff 10 <flag>` 'WESYS', then u32 compressed size, u32 size, then the data. flag 0x81 means zlib;
  flag 0x00 means stored (CoachDeleteList). [static/data]
* **The in-memory DB** is `[0x1414b6a60()+0x48]`:
  * players: 30001 × 0x17c at DB+0;
  * teams: 750 × 0x690 at DB+0xadf4bc;
  * coaches?: 1300 × 0x258 at +0xc4ca0c;
  * counts at +0xd0bce8 (players), +0xd0bcec (teams), +0xd0bcf0, +0xd0bcf4;
  * PlayerWeekly table at +0xe63de4 (count +0xe9fed4);
  * TeamWeekly table at +0xe9e764 (count +0xe9fed8);
  * clamp records MIN at +0x184c240 and MAX at +0x184c0c4. [static]
* **Players** (0x1414f4500):
  1. 0x1414f00c0 builds {ptr, id} pairs and sorts them by id (0x1414ed8f0).
  2. For each player, `0x1414f8420(conv, rec, file)` fills names, ids, dates and clubs, then calls the bit-unpacker
     `0x1414c78e0(rec, file, mode 0)`. The unpacker calls setter 0x1414c6e90 108 times.
  3. `0x1414f79d0` then clamps every parameter between the MIN and MAX records. The MIN/MAX records are
     0x1414c78e0 run in mode 1 and mode 2; values are in `param_minmax.json`.
  * The 108 values can be read back with getter 0x1414c66c0 (a jump table at 0x1414c6cdc).
  * I emulated the real unpack + setter + getter for 150 random players. It agrees 100 % with the linear bit map
    used in pesdb.py.
* **Teams + rosters**: 0x1414f5120. For each Team.bin record it builds the 0x690 team, then appends that team's
  PlayerAssignment rows (sorted by team id, then file order) through 0x141317e50(team, {0,pid}, shirt).
  The team builder also reads CompetitionEntry (idx 6), Coach (idx 3) and others.
* **Live update** (`LiveDataSetFlow.cpp`, CmdGetLivedataList/CmdGetDlcFilelist, "ExtraUpdate.bin", saved as save type
  `LIVE`) can replace these files: 0x142bd0ec0 lists PlayerWeekly, Tactics, TacticsFormation, PlayerAssignment,
  Player, PlayerAppearance, BootsList, PlayerDeleteList, Coach, CoachDeleteList and TeamWeekly. [static/inferred]

## Player.bin — 312 (0x138) bytes per record, 18,452 records (dt80), little-endian, names UTF-8 NUL-padded
| off | bits | field | → memory (0x17c record) | evidence |
|---|---|---|---|---|
| 0x00 | u32 | **associated club id** (the legend/home club; Messi→108 FCB, Totti→Roma; 0 for half the players). NOT the current team; use PlayerAssignment | rec+0x14c = id<<14 (or −1 if > 0x3ffff) | static 0x1414f8587, data |
| 0x04 | u32 | **loan parent club** (0 if not on loan; Reina: Milan, Yoshida: Southampton) | rec+0x160 = id<<14 | static 0x1414f8798, data |
| 0x08 | u32 | **player id**. Bits 0..17 are the id; bit 18 is a flag | rec+0x30 = id; rec+0x34 = (id&0xbffff) with bit18→bit31 | static 0x1414f8445 |
| 0x0c | 0..26 | contract until, yyyymmdd as a decimal (20210630) | rec+0x138 packed ((mm<<8\|dd)<<16)\|yyyy, day clamped | static 0x1414f8628 |
| 0x0c | 27..31 | p55 free-kick motion (0..19) | | |
| 0x10 | 0..26 | loan until, yyyymmdd | rec+0x16c | static |
| 0x10 | 27..31 | **p59 playing style** (stored value 0..21; see the list below) | | |
| 0x14 | 0..23 | value_f14 (1,120,000 for Messi) [guess: market value or salary] | rec+0x174 | static 0x1414f8613 |
| 0x17 | byte | byte17 (0..133; Messi 129) [unknown] | rec+0x149 | static 0x1414f87c1 |
| 0x18 | 0..23 | value_f18 (24 non-zero) [unknown; fee?] | rec+0x170 | static |
| 0x1b | byte | p46 **height** = v+100 (156..207) | | Messi 170, CR7 187 |
| 0x1c | 0..8 | **2nd nationality** (country id; Messi → Spain) | (raw) | data [inferred] |
| 0x1c | 9..17 | **nationality** = Country id | rec+0x144 (validated by 0x1414e3520) | static 0x1414f8516 |
| 0x1c | 18..25 | p58 goal celebration (0..162) | | |
| 0x1c | 26..31 | p26 place kicking (+40) | | |
| 0x20..0x43 | bitfields | the remaining parameters (table below) | rec+0x00..0x2b | static (bit-probe) |
| 0x3c | 18 | flag → rec+0x150=0x13 (4 players) | | static 0x1414f85e9 |
| 0x3c | 28 | flag → rec+0x148 bit 19 (2248 players) | | static 0x1414f8569 |
| 0x40 | 20..31 | unused (always 0) | | |
| 0x44 | 61 B | **Japanese name**. Used as the main name only when the game language is 5 | rec+0x38 | static 0x1414f8481 |
| 0x81 | 61 B | **shirt name** ("MESSI") | rec+0x75 | static |
| 0xbe | 61 B | second short name (almost always the same as 0x81) [unknown use] | rec+0xb2 | static |
| 0xfb | 61 B | **name** ("L. MESSI"), the main name. Empty → "NoName%.06d" | rec+0x38 | static 0x1414f84b3 |

In-memory extras:
* rec+0x144 bit 31: if set, the name getters 0x1414c0280/c02c0/c02e0 return a generic placeholder (DB+0x98)
  instead of the record's names.
* rec+0x148 bits 20..27: a 9th "motion" value (celebration 2?). It is not set from Player.bin; probably PlayerAppearance
  or edit data. [inferred]

### The 108 parameters (getter index = order used by param_check_sum)
Formula: `value = ((u32 at file+off) >> shift & (2^width−1)) + add`.

Name sources [static]:
* The game's own **UI-order tables**: abilities 0x142978fd0, positions 0x142978f90, COM styles 0x1429790d8, skills
  0x142979100, motions 0x1429791b0, playing-style map 0x142979080.
* The name order of the Lua constant table 0x1434f8c90 (OFFENCE_JUDGMENT, DEFENCE_JUDGMENT, GK_JUDGMENT, DRIBBLE,
  BALL_CONTROL, …).
* Per-position statistics and well-known players.

| idx | off:shift:width+add | name | range | notes |
|---|---|---|---|---|
| 0 | 52:18:4 | registered position 0..12 = GK CB LB RB DMF CMF LMF RMF AMF LWF RWF SS CF | 0..12 | Messi 10=RWF |
| 1..13 | 2-bit | position proficiency CF,SS,LWF,RWF,AMF,DMF,CMF,LMF,RMF,CB,LB,RB,GK (0 none, 1 B, 2 A) | 0..2 | the registered position is always 2 |
| | | offsets: 1=56:22, 2=56:30, 3=56:24, 4=56:28, 5=56:16, 6=48:30, 7=56:8, 8=56:18, 9=56:12, 10=56:20, 11=36:30, 12=56:26, 13=40:30 | | map 0x142978f90 |
| 14 | 44:18:6+40 | Offensive Awareness | 40..99 | |
| 15 | 32:19:6+40 | Defensive Awareness | | |
| 16 | 40:6:6+40 | GK Awareness | | |
| 17 | 44:0:6+40 | Dribbling | | |
| 18 | 32:25:6+40 | Ball Control | | |
| 19 | 52:0:6+40 | Tight Possession | | |
| 20 | 48:12:6+40 | Finishing | | |
| 21 | 32:7:6+40 | Low Pass | | |
| 22 | 48:18:6+40 | Lofted Pass | | |
| 23 | 36:0:6+40 | Heading | | |
| 24 | 36:24:6+40 | Ball Winning | | |
| 25 | 48:0:6+40 | Aggression | | |
| 26 | 28:26:6+40 | Place Kicking | | |
| 27 | 40:12:6+40 | Curl | | |
| 28 | 44:12:6+40 | GK Catching | | |
| 29 | 32:13:6+40 | GK Parrying (Clearing) | | |
| 30 | 40:0:6+40 | GK Reflexes | | |
| 31 | 36:12:6+40 | GK Reach | | |
| 32 | 56:14:2 | Weak Foot Accuracy (shown +1) | 0..3 | |
| 33 | 36:18:6+40 | Speed | | |
| 34 | 48:6:6+40 | Physical Contact | | VVD 94 |
| 35 | 44:24:6+40 | Balance | | |
| 36 | 44:6:6+40 | Kicking Power | | |
| 37 | 40:24:6+40 | Acceleration | | |
| 38 | 36:6:6+40 | Jump | | CR7 96 |
| 39 | 40:18:6+40 | Stamina | | |
| 40 | 52:22:3 | Form (shown +1) | 0..7 | |
| 41 | 56:10:2 | Injury Resistance (shown +1) | 0..2 | |
| 42 | 64:2:1 | stronger foot (1 = left) | | Messi, Salah |
| 43 | 60:29:1 | unknown flag (44 players, 25 of them GKs) [guess: stronger hand] | | in group 0x142979048 with 48,49,50 |
| 44 | 56:6:2 | Weak Foot Usage (shown +1) | 0..3 | |
| 45 | 48:24:6+15 | Age | 15..63 | Messi 33 |
| 46 | 24:24:8+100 | Height (cm) | 100..250 | |
| 47 | 32:0:7+30 | Weight (kg) | 30..150 | |
| 48 | 56:3:3 | unknown 0..7. Stars are 7 (Messi, CR7, Neuer, VVD) [guess: star/reputation rating] | | |
| 49 | 44:30:2 | unknown 0..2 | | |
| 50 | 60:0:2 | unknown 0..2 | | |
| 51 | 52:25:3 | motion 1 [inferred: dribble hunching] | 0..4 | motion group 0x1429791b0 in UI order 51,52,53,54,57,55,56,58 |
| 52 | 52:10:4 | motion 2 [inferred: dribble arm movement] | 0..9 | |
| 53 | 56:0:3 | motion 3 [inferred: running hunching] | 0..4 | |
| 54 | 52:14:4 | motion 4 [inferred: running arm movement] | 0..9 | |
| 55 | 12:27:5 | motion 6 [inferred: free kick] | 0..19 | |
| 56 | 52:28:3 | motion 7 [inferred: penalty kick] | 0..6 | |
| 57 | 52:6:4 | motion 5 [inferred: corner kick] | 0..9 | |
| 58 | 28:18:8 | motion 8 [inferred: goal celebration] | 0..162 | |
| 59 | 16:27:5 | **playing style** (stored order; getter 0x1414c0300 maps to the UI enum) | 0..21 | |
| 60..66 | 1-bit | COM styles (param idx: file dword:bit): Trickster 60 (60:9), Mazing Run 61 (64:19), Speeding Bullet 62 (64:14), Incisive Run 63 (60:30), Long Ball Expert 64 (64:17), Early Cross 65 (52:31), Long Ranger 66 (64:8) | | table 0x1429790d8 |
| 67..107 | 1-bit | the 41 player skills (below) | | table 0x142979100 |

**Playing style, stored values** (verified on GK/CB/CF groups and stars, e.g. Neuer 16, Oblak 17, VVD 15, CR7 4):
0 None, 1 Goal Poacher, 2 Dummy Runner, 3 Fox in the Box, 4 Prolific Winger, 5 Classic No.10, 6 Hole Player,
7 Box-to-Box, 8 Anchor Man, 9 The Destroyer, 10 Extra Frontman, 11 Offensive FB, 12 Defensive FB, 13 Target Man,
14 Creative Playmaker, 15 Build Up, 16 Offensive GK, 17 Defensive GK, 18 Roaming Flank, 19 Cross Specialist,
20 Orchestrator, 21 Full-back Finisher. Table 0x142979080 maps these to the in-game UI order (forwards → wingers →
mids → FBs → CBs → GKs).

**Skills: UI order → param (file dword:bit).**
* The order comes from table 0x142979100 [static]. The names follow from that order and match players/positions
  [inferred]: GK skills have only GK holders, Penalty Specialist = CR7/Messi/Lewandowski/Neymar/Salah, etc.
* Scissors Feint 67(64:7), Double Touch 104(64:11), Flip Flap 68(60:5), Marseille Turn 69(60:17), Sombrero 70(60:2), Chop Turn 105(32:31), Cut Behind & Turn 71(64:16), Scotch Move 72(64:13), Sole Control 99(60:20), Heading 73(60:15), Long Range Curler 74(64:15), Chip Shot Control 98(60:26), Long Range Drive 106(64:6), Knuckle Shot 75(64:1), Dipping Shot 100(60:14), Rising Shot 101(60:19), Acrobatic Finishing 76(64:12), Heel Trick 77(60:25), First-time Shot 78(60:31), One-touch Pass 79(60:27), Through Passing 107(60:7), Weighted Pass 80(60:4), Pinpoint Crossing 81(60:3), Outside Curler 82(60:13), Rabona 83(64:3), No Look Pass 95(64:0), Low Lofted Pass 84(60:8), GK Low Punt 85(60:10), GK High Punt 102(60:16), Long Throw 86(64:9), GK Long Throw 87(64:10), Penalty Specialist 96(60:21), GK Penalty Saver 97(60:22), Gamesmanship 88(60:11), Man Marking 89(60:24), Track Back 90(64:5), Interception 103(60:23), Acrobatic Clear 91(64:18), Captaincy 92(60:12), Super-sub 93(64:4), Fighting Spirit 94(60:6).
* Exact bits for every index are in `PMAP` in pesdb.py.
* The 10/11/13 names (Long Range Curler / Long Range Drive) are the least certain.

Spot checks against real-world data:
* Messi: id 7511, 170/72, 33 y, left foot, RWF (CF/SS/RWF A), Argentina + 2nd nationality Spain, contract
  2021-06-30, #10 + captain for FCB and Argentina.
* CR7 #7, 187 cm, Prolific Winger. VVD CB, Build Up, #4 at Liverpool. Oblak GK #13 at Atlético.
* The Barcelona roster lists the correct 2019/20 numbers in lineup order (ter Stegen 1, Piqué 3, … Suárez 9).

## Team.bin — 0x5fc (1532) bytes, 669 records
| off | field | evidence |
|---|---|---|
| 0x00 u32 | **coach id** (Coach.bin; FCB→380 "QUIQUE SETIÉN") | data; team build 0x1414f5a89 → team+0x28c |
| 0x08 u32 | **team id** (FCB 108, Argentina 50, Man Utd 100, Arsenal 101). In memory: team+0 = id<<14 (−1 if > 0x3ffff) | static 0x1414f5a61 |
| 0x0c,0x10,0x14,0x18,0x1c,0x24,0x28,0x2c,0x30 u32 | team references, looked up via 0x1414e3680 → team+0x658..0x68c. 0x1c = main rival (FCB→Real, MUN→City), 0x30/0x14/0x28 = further rivals | static 0x1414f6014..61e8, data |
| 0x20 u32 | id (≤0x3ffff) → team+0x628, looked up with 0x1414e3680 [unknown kind] | static 0x1414f5d1d |
| 0x34,0x38 u32 | copied raw to team+0x644/0x648 (+0x650) [unknown: colors?] | static |
| 0x3c u16 | **home stadium id** (Stadium.bin u16@4; FCB 2 = Camp Nou) | static 0x1414f5c43 → team+0x294 |
| 0x40..0x57 | packed bit flags/settings, copied to team+0x418..0x424, +0x63c..0x674. 0x44>>18 & 0x1ff = **country id** (team+0x418); 0x4c>>28 = team kind (via table 0x14297d090); 0x50 bit31/30 and 0x54 bit3 select the short-name variant and the Chinese name | static |
| 0x58, 0x9e, 0xe4, 0x12a, **0x170 (English, default)**, 0x1b6, 0x1fc, 0x242, 0x288, 0x2ce, 0x32c, 0x37c, 0x3c2, 0x408, 0x44e, 0x494, 0x4da, 0x520, 0x570, 0x5b6 | **team names**, 70 bytes each, by language. Selected by 0x1414efd00 (language id 1..19 → offset; empty → 0x170). Japanese 0x58, Greek 0x12a, French 0x1fc, Turkish 0x242, German 0x32c, Chinese 0x3c2, Russian 0x4da, Dutch 0x520 | static/data |
| 0x372 (10 B) | **short name** ("BAR", "ARG") → team+0x4a | static 0x1414f5afc |
| 0x566 (10 B) | alternate short name ("BRC"), used when flag 0x50 bit 30 is set | static |

In-memory team (0x690):
* +0x14c: 40 × qword {u16 0, u32 player id}.
* +0x29a: 40 × u16 shirt number.
* +0x426: u16 roster count (&0x7f).
* +0x5c8..0x5cd: set-piece slot indices. +0x5d1: captain slot.

## PlayerAssignment.bin — 16 bytes, 18,582 rows (team ↔ player)
| off | field |
|---|---|
| 0x00 u32 | entry id (sequential) |
| 0x04 u32 | player id |
| 0x08 u32 | team id |
| 0x0c u32 | bits 0..9 shirt−1; 10..15 order/slot (0..10 = starting XI order, then subs); 16..20 set-piece role bits (team slots 0..5 test bits 19,17,17,18,16,20, 0x1414f06a0) [role names guessed: FK long/short, corners L/R share bit 17, PK]; bit 21 captain (team+0x5d1) |

Messi: FCB 0x3f2009 (shirt 10, slot 8, captain + all set pieces), ARG 0x3a2009. Static 0x1414f628e..62bd.

## Other files (brief)
* **Coach.bin** (100 B, 677 rows): u32 id; u32 f4 [unknown]; 0x08 Japanese name (46 B); 0x36 name (46 B).
* **Country.bin** (0x58c B, 214 rows):
  * u32@0: bits 10..18 = **country id** (the player nationality value; verified 100 % coverage). Bits 0..9 are other
    data.
  * u32@4: region/confederation.
  * Names (70 B each): JP 0x4e, ES 0x94, IT 0xda, **EN 0x120**, FR 0x1ac, TR 0x1f2, PT 0x238, DE 0x27e, short
    0x2c4, ZH 0x314, NL 0x35a, GR 0x42c, RU 0x4b8, …
  * In memory: 0x4c-byte records keyed by dword@+0x48 & 0x1ff (lookup 0x1414e3520).
* **Stadium.bin** (0x188 B, 52 rows): u16@4 id; 0x08 JP name (181 B); 0xbd name ("Camp Nou"); 0x172 label
  ("CAMP_NOU").
* **Competition.bin** (36 B, 91 rows): byte@5 = competition id; 0x08 label ("UEFA_CHAMPIONS_LEAGUE", 28 B).
* **CompetitionEntry.bin** (12 B, 1283 rows): {u32 team id, u32 entry id, u32 (slot<<8)|competition id}.
  Example: Eredivisie 13 = ADO, Ajax, AZ….
* **Derby.bin** (12 B): {team A, team B, derby id} (MUN–MCI 1, EVE–LIV 2, ARS–TOT 3).
* **Tactics.bin** (12 B, 1338 = 2 per team): {u32 tactic id, u32 team id, u32 packed settings}.
* **TacticsFormation.bin** (12 B, 33 rows per tactic): {tactic id, u32 position/role index, u32 packed x/y + flags}.
  Not decoded deeply.
* **PlayerWeekly.bin** (12 B): {u32 player id, u32 seq, u8 condition (&7) @+8}.
  * Loaded at 0x1414f3640 into DB+0xe63de4 (max 30000).
  * Read by 0x1415106b0, which CHECK_CHEAT and presumably the match condition use, only in live mode 1.
  * The shipped copy has 86 legends, all with condition 3.
  * This is the **live "form arrow" carrier**: a live update replaces it and changes the checksum.
* **TeamWeekly.bin** (8 B): {u32 team id, u8 value @+4 (&7)} → DB+0xe9e764 (max 750). The shipped data has all 5
  [inferred: team condition/strength for weekly live update].
* **PlayerDeleteList.bin** (u32 ids, 555 in dt80): all 555 still exist in Player.bin. Probably the ids whose stale
  edit or live entries must be dropped. [guess] CoachDeleteList is the same for coaches (stored, not zlib).
* **InstallVersionPlayer.bin** (8 B: {u32 player id, u32 100}, 670 rows; names …Player1..6 in the exe at 0x14297ac28):
  a per-player data-version table [unknown purpose].
* **SpecialPlayerAssignment.bin** (16 B, 221 rows): {player id, 0, n, 1} for legend/special players.
  `*4.bin` variants (Player4/Team4/…) also exist in dt80, but the exe has no reference to them under these names
  [unknown].

## Option file / edit data (description only; no save was read)
* **Save directory**: `Documents\KONAMI\eFootball PES 2021 SEASON UPDATE\save\`.
  * Strings `\KONAMI\`, `\save`, `\mount`, `\WEPES` (fn 0x14140ea40); "%s\%S" (0x14142fd00).
  * Settings: `…\settings.dat` (0x140408c50).
* **Save types** (table 0x142944560…):
  * USERT, REPLAY, **EDIT** ("edit"), BEDIT, **SYSTEM**, TEMBLEM/CEMBLEM (emblems), COACH, UNIFORM(LOW), STADIUM,
    SPONSORLOGO, TEXPORT/CEXPORT/PDEXP;
  * **LIVE** (saved live update), VARIATION00..15, EXTRAUPDATE, FILELIST, VERSUS.
  * The file names are the prefix plus a number (the familiar `EDIT00000000`). [static strings, number format
    inferred]
* **Encryption** [static 0x14140e0f0]: an XOR stream built on MT19937.
  * `init_by_array(key16)` (0x1414136a0), then 4 MT outputs s0..s3.
  * Per dword: `out = in ^ s0^s1^s2^s3^mt()`, then `s0=ror(s1,15), s1=rol(s2,11), s2=rol(s3,7), s3=ror(mt,13)`.
    The tail bytes are handled the same way.
  * It is symmetric: the same routine encrypts and decrypts.
  * The key for the 0x140-byte header (0x14140e2b0) is the header's own 64 bytes at +0x100 XOR'd (0x14140c4b0) with
    a 64-byte value from 0x1414133e0(caller key). Where the caller key comes from (fixed key vs account-bound) was
    **not traced**.
  * Readers: 0x14140dd40/0x14140f470/0x141410d10 (SaveSystemBaseFileUtility::Read 0x141415ca0, Write 0x141417d40).
* **Effect on the DB**: edit data is applied on top of the in-memory DB (players, teams, rosters).
  * Edited stats or transfers change param/entry_check_sum.
  * Edited names, kits and emblems don't, because the sums contain no strings. [inferred from the checksum code]

## Server should
1. **Map ids → names** with pesdb.py/JSON:
   * Player id = Player.bin u32@8. Team id = Team.bin u32@8. English names: player 0xfb, team 0x170.
   * Current team and shirt number come from PlayerAssignment (not Player.bin @0, which is a legacy club).
   * Country names from Country.bin.
2. **Matchmaking key**: `param_check_sum + entry_check_sum` as already planned.
   * The values from our logs equal the stock dt80_100E database. Treat them as the "stock" fingerprint.
   * Any other value means a modded or old DB (dt10-only gives `25BC6828…/1E8FB88B…`), an edited option file, or
     live data.
   * To explain a mismatch, the server can recompute sums for known DB variants. pesdb.py `DB(cpks=[…])` does this
     for any CPK set.
3. **Do not serve live-update data** (CmdGetLivedataList) unless every client gets the identical set. Live mode 1
   adds PlayerWeekly conditions to param_check_sum.
4. **CPU squads / myClub cards**: roster order = PlayerAssignment slot order (0..10 = XI). Abilities, positions,
   skills, COM styles and playing style are all decoded. Tactics/TacticsFormation give team formations (not fully
   decoded).

## Still unknown
* Names of p43, p48, p49, p50; Player.bin 0x14/0x17/0x18 (value/salary?); 0x3c bits 18/28; the exact motion order
  (51–58) and celebration 2. A live edit-mode screenshot of a player with known values would settle all of these.
* The set-piece role names for PA bits 16..20, and the Team.bin 0x20/0x34/0x38/0x40..0x57 flag fields.
* Where the save master key comes from (0x1414133e0's input), and whether EDIT data is applied before CHECK_CHEAT.
  A test with an edited stat would show it.
* The purpose of the `*4.bin` files, InstallVersionPlayer and PlayerDeleteList.
* Which live-mode value (esi at 0x1420ae523) the real Konami live update set. Confirmed only: mode 2 (no conditions)
  is used on one path and mode 1 enables conditions.
