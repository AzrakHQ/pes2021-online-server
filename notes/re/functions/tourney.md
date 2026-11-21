# Online Tournament (PES League 1v1): friends' competitions (pestourney.py)

PES2021.exe 1.01.00. Built and checked live 2026-11-08 with two game copies (probe build, side-by-side test
server). Tags: **[live]** seen in the game, **[static]** read in code or the flow files, **[inferred]**.
Flow files: `Online/PesLg/*.json` and `Online/PesLg/1vs1/*.json` from `dt16_all.cpk` (not published).
The lobby-type competition (Match Replays) is in peslg_path.md / spectate2.md.

## The path [live]

1. eFootball → Online Tournament → login flow ("No new Live Update" dialog) → JOIN_PESLEAGUE_COMPE_MODE →
   GET_PESLEAGUE_MAINMENU_INFO → GET_PESLEAGUE_COMPE_LIST → the competition cards (name, "Ends in", "1 vs 1",
   "Eligible").
2. A on a card → "Enter this Competition. Proceed?" (cursor on No; `pesflow ok 1`) → GET_PESLEAGUE_COMPE_INFO →
   GET_ONLINE_AD_FILE → GET_PESLEAGUE_COMPE_TEAM_LIST → team select (regions, then the teams from the list).
3. A team → "Enter <TEAM> into Competition. Proceed?" → JOIN_PESLEAGUE_COMPE {compe_id, team_id} →
   GET_PESLEAGUE_1VS1_MAINMENU_INFO {compe_id} → **PesLg1vs1MainMenu** ("eFootball League", the competition
   name, matchmaking window, time left, the player and team, Points, W/D/L, Start Match).
   - match_type in COMPE_INFO (inside `compe_data`) picks the branch in PesLgProcCompeJoin [static]:
     PES_LEAGUE_MATCH_1VS1 / _1VS1_RANDOM → 1v1 menu; _3VS3 → Online/CoopMatch/CoopInit (join_coop);
     _1VS1_LOBBY → the lobby.
4. **Start Match → Online/Quick/QuickProcessInit** (flow file) → CMD_START_MATCHING {matching_type
   "PES_LEAGUE", no compe_id} → "Searching for an opponent" → the usual Quick Match push, match menu, match.
   No team select: each game uses the team it entered with (the teams come from each player's game, not the
   server). The server pairs only players of the same competition (the one the player opened last).
5. After the match: Full Time → Next → Online/Match/MenuRating **"Changes in rating"** (the verdict push's
   oldrating → rating) → Next → LEAVE_ROOM, JOIN_PESLEAGUE_COMPE again (same team), 1VS1_MAINMENU_INFO → the
   1v1 menu with the new Points and W/D/L.
6. When a competition has ended and the player has not seen it: MAINMENU_INFO `new_result:"YES"` →
   GET_PESLEAGUE_COMPE_RESULT_LIST {compe_id 0, is_unconfirm_list "YES"} → **"Competition Results"** (schedule,
   User Numbers, Final Position "1ST" and the name) → A → back to the list (MAINMENU_INFO, COMPE_LIST).

## Replies (layouts from the game's parsers; the live screens confirm them)

- **COMPE_LIST** (0x141bd6040): `compe_list` [blob{compe_id, compe_db_id, compe_name, is_myclub, match_type,
  start_date, end_date, entry_limit_date, match_end_date, entry_user_limit, entry_user_num, entry_user_max,
  entry_limit_division(_min/_max), is_end, banner_file_info blob, list_banner_file_info blob, is_entry,
  is_gm_account, is_no_check}], list_num, list_max. Request `is_end` "YES" asks for past competitions.
- **COMPE_INFO** (0x141bc8450): the same fields at the top level and in a `compe_data` blob, plus
  is_exist_overview_info, is_exist_participation_right, use_ball_id, is_e_sports, is_pesleague_stadium,
  is_efootball_pro_stadium, entry_compe, team_id, clan_info blob (co-op/clan competitions) and eula_url.
  - **`compe_regulation` is a top-level blob** (found with the parser bench; inside compe_data it is ignored):
    match_time NUM (this+0x580), condition STR (+0x588; CONDITION_WORST…CONDITION_BEST), ranking_match_num NUM
    and trade_num NUM (+0x5a8/+0x5ac), extra_time STR (+0x5b0), **pk STR (+0x5d0; key at 0x142add6d4, the schema
    extractor missed it)**, guest STR (+0x5f0; GUEST / GUEST_NONE / PLATFORM_GUEST), is_equalization /
    is_3member_coop / is_ex_substitution "YES"/"NO" (+0x610/+0x630/+0x650).
  - **extra_time "YES" + pk "YES" [live 2026-11-21]:** a match level after 90 minutes goes BREAK2 → EX1ST → EX2ND
    → BREAK4 → PK → END (without them: 2ND_30MIN → END). The shootout waits for the kickers' input (a copy left
    alone sat on the first kick for 12 minutes). Each scored penalty is a CMD_ADD_SCORE like any goal (after the
    PK phase report); the game's own result keeps them apart: Full Time 0-0 "120:00 (1 PK 0)", SEND_GAME_RESULT
    home_score 0 / away_score 0. The verdict push with winner_side of the shootout's winner and is_pk "YES" is
    taken without trouble. pesmatch keeps shootout penalties in room["pk_goals"].
  - **Both games play by the room owner's rules [live 2026-11-21, one direction]:** owner with match_time 5 /
    extra_time YES / pk YES, guest with 10 / NO / NO: both reported 1ST_15MIN after 50 s (a 5-minute match) and
    both went to BREAK2 at 0-0. A game reads the regulation only with COMPE_INFO (when the competition is opened
    from the list), not after each match. Leaving the 1v1 menu ("This will stop play and withdraw you…") goes
    back to the main menu; entering again asks for the team again.
  - **match_time = match length in real minutes [live]:** without the blob a whole match took ~2 minutes
    (1ST→BREAK1 3345 gtimer ticks); with match_time 5 the first half took 8100 ticks (2.5 min). A 10-minute
    Quick Match half is ~17150 ticks. Checked by the in-game clock 60 s after kick-off: match_time 7 → 13:04
    (a 7-minute match), 10 → 9:05 (10 minutes, like Quick Match).
  - Measure by the clock, not by gtimer over a whole half: a match with tools/attack.py running took 9.5 real
    minutes for a 10-minute half (28709 ticks). The two copies play in lockstep and the key tool keeps moving
    the focus, so both slow down.
  - **is_equalization [live]:** "YES" (and the default) shows "Any players used in this competition will have
    their overall ratings recalibrated…" at team select, and before the match the game sends
    GET_PLAYER_PARAMETERLIST with `is_equalization:"YES"`, expecting the evened-out stats from the server. With
    "NO" the notice is gone. pestourney always sends "NO" (it makes no such stats).
  - condition / guest / ranking_match_num / trade_num: values and effects not tested.
- **TEAM_LIST** (0x141bdf3b0): `team_list` [team ids], team_list_num. National-team ids 1–14, 45, 50 checked with
  pesdb.py; the select screen groups them by region.
- **1VS1_MAINMENU_INFO** (0x141c36530): compe_end_time ("Competition ends in"), matching_first_date +
  matching_time = the end of the matchmaking window ("Matchmaking window closes in"; [live]: a value of
  end − now showed the wrong time), matching_interval, point, win/draw/lose_count, match_num ("Matches to achieve
  record"), regulation_match_num (the "Best N consecutive match result" header: 5 showed "Best 5 …" [live]; the
  eFootball League ranked a player by their best N consecutive matches), rank_type, team_id (the flag and name
  shown), team_name. pestourney counts every match, so it sends N = matches played (at least 1).
- **COMPE_RANKING / _BYID** (0x141c1b2e0): `rank_list` [blob{pid, entry_name, team_id, clan_owner_name
  blob{name, xuid_one, psn_user_id blob{account_id, online_id, is_valid_cache}, is_deleted}, point, rank,
  win_count, draw_count, lose_count, match_num, get_score, lose_score, diff_score, recode_date,
  continuous_win, is_get_participation_right, is_mydata, rank_type}], list_num, list_max, update_date ("Last
  Updated"). An empty reply shows "Error occurred while loading Ranking Data." [live]. Five pages (LB/RB):
  Rating (= point), Matches, W/D/L, Consecutive Wins, Win Rate; is_mydata YES highlights the player's line.
- **Current Match Results** = CMD_GET_GAMERESULTS {game_res_type "PESLEAGUE", pesleague_compe_id}; empty →
  "No game data." [live]. Same rlist layout as results.md; match_category PESLEAGUE_MATCH labels it
  "eFootball League".
- **Competition Info** (in the 1v1 menu) shows schedule, "1 vs 1", Capacity from COMPE_INFO; no command.
- **Regulations** pages (Standardized Player Levels, About Matchmaking Windows, Match Settings, How Rankings
  are Decided) are the game's own text; no command [live].
- **Competition Outline** (list → X → Competition Details → Competition Outline; shown when
  is_exist_overview_info is "YES"): CMD_GET_PESLEAGUE_COMPE_OVERVIEW_INFO {compe_id} → `overview_info` STR,
  shown as is; "\n" breaks lines [live].
- **COMPE_RESULT_LIST** (0x141c11b00): `result_list` [blob{compe_id, compe_db_id, compe_name, banner_file_info
  blob{filename, hash_str}, start_date, end_date, player_rank, entry_user_num, clan_name, clan_owner_pid,
  clan_owner_name blob, is_participation_get, update_division, before_division, is_confirm, is_get_reward}],
  result_list_num. There is no separate confirm command: the server marks a result seen when it sends it with
  is_unconfirm_list YES.
- The verdict push (WATCH_GAMEADDPOINT) is the same as in Quick Match; its rating/oldrating are what "Changes in
  rating" shows, so pestourney puts the competition points there (1 > 2 (+1) live).

## Knockout cups (pescup.py, 2026-11-21)

- **The game has no bracket screen online [static]:** the flow files reach Compe/CompeKnockout,
  Common/CmnTournamentMenu and the TournamentProgress menus only from the offline Compe (Cup/League), ML and BL
  flows; no Online/* flow leads there. (The knockout stages of the official esports events were run outside the
  game's Online Tournament menus.) A cup is therefore a server-side bracket inside a PES League competition.
- The server pairs only the two players of an open tie (pesmatch._cup_pair), sends extra_time/pk "YES" for
  every cup match (pestourney.regulation), makes a player whose game holds the cup's rules the room owner
  (host_first), and shows the bracket in the Competition Outline text (overview_info), the Rankings order and
  the admin page. A knockout tie that still ends level is played again; a group match is then a draw.
- **Why the group matches have the knockout's rules:** a game reads compe_regulation only with COMPE_INFO, when
  the player opens the competition from the list. After a match it sends LEAVE_ROOM, JOIN_PESLEAGUE_COMPE and
  1VS1_MAINMENU_INFO only [live], so rules that changed between the groups and the knockout would never reach
  friends who stayed in the competition's menu. A match decided on penalties is a win (3 points in a group); its
  goals stay level.
- Match settings struct (default init 0x141007db0; the lobby's DECIDE_GAMEENV handler 0x1410ffac0 fills one):
  +0 cpuLevel, +4 gametime, +8 injury, +0xc ball_type, +0x10 exGame, +0x11 pkOnOff, +0x14 substitution,
  +0x18 exSubstitution, +0x1c limitTime [static].
- **[live 2026-11-21] a two-player cup (the final) between two copies, rules from pestourney.regulation:** 0-0
  after 90 minutes → extra time → penalties (one scored) → verdict winner_side WINNER_AWAY, is_pk YES → "Changes
  in rating" (the competition points, 0 > 1 each: the match itself is a draw) → the 1v1 menu with the game's own
  dialog "The final results have been calculated. You can view them from the Competition Menu." (the cup had
  ended itself) → on the next visit to Online Tournament the Competition Results notice, Final Position 1ST.
  A 549-character bracket text in Competition Outline shows about nine lines at a time and scrolls.
- In a shootout driven by keys, hold a direction and press shoot (d) on the kicker's copy and the other
  direction on the keeper's; the same direction on both was saved nine times in a row.
- **[live 2026-11-21, second session] a six-player cup with groups (two copies and four stand-in entries, the
  stand-ins' ties awarded by the host):**
  - Both copies chose Start Match before the draw: they waited, unpaired. The draw put them in one group and
    paired them at once (pescup._wake).
  - The group match: 0-0 → extra time → penalties 3-0. The match clock keeps running in a shootout (phase PK at
    gtimer 27075, the penalties at 29208, 32053, 34695, END at 37273), so penalties of one side never collapse
    and pesmatch tells them from goals by the clock. Full Time shows "0 0, 120:00 (3 PK 0)". The winner got 3
    points and 1/0/0, the loser 0/0/1.
  - **Current Match Results** (GET_GAMERESULTS game_res_type PESLEAGUE) with `pk_flag "YES"` and `score_pk` in
    the two score blobs shows "0 - 0" and "Loss (PK)" on the loser's copy. With pk_flag "NO" (before) the same
    match was a plain draw.
  - **Regulations → Match Settings** inside the competition reads the regulation: "Match Time: 5 min", "With
    Overtime", "With Penalty Shootout", "Condition: According to Live Update".
  - **JOIN_PESLEAGUE_COMPE result ERR_COMPE_AFTER_ENTRYTIME** (a player not in the draw): the dialog "Entries for
    this Competition are currently closed.", then back to the list (PesLgProcCompeJoin `return`).
  - **WATCH_MATCHING push matching_state "TIMEOUT"** (a player with nothing left to play, while searching or right
    after START_MATCHING): Online/Quick/QuickNotMatching, the dialog "Unable to find opponent.", then back to the
    competition's menu.
  - Rankings inside the competition lists the players in the order the server sends (knockout progress first).
  - **The Competition Outline is not offered inside the competition:** PesLg1vs1CompeDetail (Competition Menu →
    Competition Info) shows the schedule and format only; its flow has no overview exit. The outline is reached
    from the list (X → Competition Details → Competition Outline).
  - The cup ended by the host's award of the final while the winner was searching: the search ended (TIMEOUT), and
    on the next visit to Online Tournament the Competition Results notice showed Final Position 1ST, User Numbers 6.

- **[live 2026-11-21, third session] team lists and capacity (one copy):**
  - TEAM_LIST may hold clubs, national teams or both: the team select groups clubs by league ("English League",
    "Ligue 1 Uber Eats", "Serie A TIM", "PEU League", "Other European Teams", "Other Latin American Teams",
    "Classic Teams") and national teams by region (Europe, Africa, Asia-Oceania …), each with its real squad. All
    102 national teams of the online team list show, not only the 57 the admin page had (Albania, Serbia, Iraq
    checked). The game's league and tournament entry lists are in Competition.bin / CompetitionEntry.bin
    (tools/mkclublist.py).
  - The list may differ per player (pestourney.free_teams, one team per friend): the game shows what it gets.
  - JOIN_PESLEAGUE_COMPE **ERR_COMPE_FULL**: "Unable to enter Competition as maximum capacity has been reached.",
    OK → back to the competition list; entering again asks TEAM_LIST again.
  - COMPE_INFO **entry_user_limit "YES"** + entry_user_max 6 with 6 entries: Competition Details shows "Capacity
    6/6" (with "NO": "Off"); the card says "Participating" and A = Resume Competition. A friend already entered
    still gets in (team select with their own team only, JOIN NOERR): the game does not block a full competition
    by itself for them. Competition Details also offers "X Playable Teams".
- **Trophy ceremony: works online [live 2026-11-21, mod 0.7.0 "trophy1"].** How the end of a match works:
  - `[0x1436f3dc8]` = `match::registry`, a row of 0x28-byte Ref holders `{vtable, registered, +0x10 Pointer*,
    +0x18 raw T*}`; the raw pointers are filled only while the match job runs (`match::BeginListener`) and read 0
    from another thread. Stable: `[[reg+0x268]+0x10]+8` = MatchInfo (raw +0x270; phase at +0x1168: 4 half time,
    8/9/10 the ends, 9 after a shootout), `[[reg+0x2ac8]+0x10]+8` = ModeInfo (raw +0x2ad0; competition id +0x498
    reads 0 online), raw +0x310 = the score (side 0 / 1 goals at +0x18 / +0x1c, shootout goals included).
  - The end of a match is a step program of the sequencer 0x14044c880 (opcodes at this+0x878, built by
    0x1404505d0 / 0x140452260): time up = `[0x26, 3, 7, 0x45, 0x24]`. Event 7 = the full-time step 0x140447290
    (states at this+0x8f2). In state 0 it calls `[[0x1434a2d40]]->vfunc+0x88`; the object is **match::Online**
    (vtable 0x142bf1a28, +0x88 = 0x140ca1a50 `mov al,1; ret`; the offline base class
    `match::online::registry::Interface` returns 0). True → **no end demo**: group 0x7002b ("none") and the short
    online end (camera, stats). This one check is why online matches have no end scenes.
  - False (offline) → dispatcher 0x140488470 → 0x140486b10 (phases 8/10) or 0x1404878f0 (9, shootout) → END group
    picker 0x1404879b0 (rcx, rdx, r8 = result block, r9b = after a shootout). A non-zero group goes to
    [r8+0x1fc] → MatchInfo+0x1364 (side at +0x1368 from [r8+0x200]), message 0x1050046 → match controller state
    0x12 → FixDemoMatch plays the group (assets load then) → state 0x13 stats.
  - Proven live (two copies, probe patches, then the mod): bypass the `je` at 0x1404473ef at the end only, picker
    returns END_CUP_CMN 0x70007 with side → the full trophy ceremony online, both games: celebrations, the losers
    down, "BELGIUM · UEFA EURO 2020™ Winners", the trophy lift with confetti, the team photo with the trophy,
    then the stats (about 60 s). After a shootout (phase 9) the _PK group (0x70008) shows the deciding penalty
    first. END_CMN 0x70001 is the champions' celebration without the trophy.
  - The trophy and the look come from the original competition (COMPE_INFO compe_db_id → QuickProcessSetOriginal-
    CompeId): 33 = the UEFA EURO 2020 presentation and the EURO trophy. Group ids = 0x70001 + the group's index in
    table_end.bin (END_CMN, END_CMN_PK, END_LGCUP_LICENSE(_PK), END_CUP_INTER(_PK) 0x70005, END_CUP_CMN(_PK) 0x70007,
    END_CUP_ICC, END_CUP_INTERCLUB(_PK), END_CUP_KONAMI(_PK) 0x7000c, END_CUPCLUBEUR_A(_PK) 0x7000e, _B 0x70010,
    END_CUPCLUBSOUTH(_PK) 0x70012, END_ACL(_PK) 0x70014, ... END_2ND 0x7001f, END_3RD 0x70021, END_ONLINE 0x70028).
  - **Do not open the offline end branch at half time or at a level end of extra time** (phase 4, or 8 before
    penalties): the game crashes in a serializer (0x142153eef, a NULL+0x84 read). The mod opens it only when the
    score is not level at phase 8..10.
  - **After a shootout** the game reports the END phase only after its end steps, which the ceremony replaces: no
    END, no verdict, and the games left a minute apart (DISCONN_P2P GNCX_0003 for the slower one). The mod tells
    the server when the ceremony starts (/mod/final?over=1); that counts as the game's END report, so the server
    settles the match (shootout winner from its penalty count) and pushes the verdict; both games then finish
    together. (After a normal final whistle END is already in.)
  - Tracing 0x1404472d8 (a chunk) crashed the games; MinHook refused 0x140447230 (MH_ERROR_UNSUPPORTED_FUNCTION).

- **Cup bracket: Konami's "Tournament Table" online [live 2026-11-22, mod "bracket1"].**
  - The screen is flow node Common/CmnTournamentMenu (offline: Common/CmnCompeInfoMenu `01_tournamentdraw`),
    menu::MenuModeTournamentProgress (vtable 0x142723430; factory 0x140af9810). It is not tied to a mode: the
    factory reads the save's selected competition (save = [[0x143705e10]+0x48]; save+0x1787b5c u32 competition
    id + u16 instance id; save+0x183789c data state, 0 = the draw editor "You can freely alter the competition
    draw.", 1 = the competition's own data) and finds its entry in the instance table (save+0xc12e9c, 0x314 bytes,
    count at save+0xd0bcf4; +0 instance id, +0x80 competition id, +0x84 kind 3 = knockout, +0x170 the team per
    place as id << 14 (0xffffffff none), +0x30a & 0x7f places). No entry → NULL → the flow engine's "@@" placeholder.
  - Posting flow message 0x1030008 "Common/CmnTournamentMenu" (string at 0x142658880) through 0x14149b790(
    [0x143704e88], id, text) opens it from the online cup's menu; 0x1030008 "Online/PesLg/1vs1/PesLg1vs1MainMenu"
    (0x1426dae38) goes back (the menu reloads with MAINMENU_INFO). The save's real competitions (214 entries: the
    leagues and cups of the offline modes) draw with real teams and flags; 8 places → the one-column layout,
    9..32 → both halves, with Final and 3rd Place Playoffs cards.
  - Results come from round tables 0x141579ee0(&instance, &kind) (0x208 bytes: 16 match records of 0x20 at +4 —
    +0/+4 teams, +8/+0xa fixture ids of the legs —, count at +0x204; kinds 0x2e..0x35 by round, 0x35 the final,
    0x36 third place), fixtures 0x1414bb280(save, id) (0x17c bytes: +0 u16 its id, +4 bit 30 played, bits 28..29
    leg type (1 = one match), teams +0x14 / +0x18, goals +0x1c(+0x1d) / +0x1f(+0x20)), 0x1414bb950 (does the id
    exist) and the winner 0x14151b230(out, record) — the screen passes its own stack copy of the record.
    0x1414d6380(record, leg type) picks the fixture. A tie with an unknown side crashed the screen: a round's
    record exists once both teams are known.
  - Screen calls (probe, 16 places): 0x140ca5fc0(menu, column, index, code) = Lua setResult (column = round from
    1; index down the left half, then the right; code 4/5 = the top/bottom team won, 1..3 not played) and
    0x140ca6540(menu, column, index, goals a, goals b) put the numbers on the lines.
  - Opening it from Rankings by first going to PesLg1vs1Ranking and jumping on crashed the game (its ranking
    request answers a screen that is gone): the mod replaces the queued 0x1030006 "02_ranking" with the jump.
    The bracket's own Return queues 0x1030006 "return" (which offline goes to Common/CmnPostCompeMenuReturn →
    offline menus): replaced by the jump back. A jump posted while a screen change is under way
    ([0x1437f8990]+0x30 != 0) is lost.

## Not built / open

- **Random Selection competitions** [static, 2026-11-08]: `match_type "PES_LEAGUE_MATCH_1VS1_RANDOM"` (enum 1) takes
  PesLgProcCompeJoin's `join_1vs1_random` branch. On joining, the game asks CMD_GET_RANDOMSELECTION_LOTTERY_LIST
  {pid, compe_id} (0x1413712da, when the process flag +0xc9 is 1) → `gameplayer_list` [NUM player ids] (parser
  0x141d7c740; read into a vector at result+0xb8), gameplayer_list_num, trade_num. After the opponent search,
  QuickProcessEndSearching `1_proceed_randomselection` → Online/RandomSelection/OnRsProcLottery → lots screens
  (OnRsMenuLotsPre / LotsPost, a "G4" variant) → OnRsProcTradeInit → OnRsMenuTrade → OnRsProcTradeResult →
  OnRsProcPreMatch → QuickProcessTeamDataSync. RESULT_RANDOMSELECTION_LOTTERY, RESULT_RANDOMSELECTION_TRADE and
  SYNC_RANDOMSELECTION_TRADE have no reply parser: they are peer-to-peer match messages like TEAM_DATA_SYNC, not
  server commands. Open: whether gameplayer_list is the pool the draw picks from or the drawn squad, its size,
  and what trade_num counts. A player-list menu (0x140c72660) also reads the lottery result.

- Rewards (REWARD_INFO/REWARD_LIST/present box), participation rights (PARTICIPATION_RIGHT), the member list,
  clan and 3-vs-3 (co-op) competitions, the "best N consecutive matches" ranking rule (regulation_match_num),
  rank_type values, the effects of condition.
- Cups: two-legged knockout ties (the game reads extra time / penalties only when the competition is opened, so
  a first leg would go to extra time too); a draw by pots; a decisive normal-time final with the mod's own hooks
  (the probe version of the same path was seen; the mod's run went to penalties); the reverse of the owner-rules test (an owner with the longer match; with the same
  rules for every cup match it only matters after a league was changed into a cup); a third-place match and a
  knockout tie right after a group match in one sitting were not played live (unit tests only); showing the
  bracket inside the competition's menu (the game offers no text screen there).

## Testing notes

- `tools/tourney-enter.sh INST [DOWNS]` (game folder) takes a copy from the main menu to the 1v1 menu.
  Dialogs come up late: retry the Return + `ok 1` pair.
- Several menus keep their cursor between visits; a key sent before a screen is ready lands on the next one.
- tools/attack.py once kept running after full time (the log tail had no phase line any more) and kept a copy
  leaving menus; it now stops at the first END/result logged after it started.
