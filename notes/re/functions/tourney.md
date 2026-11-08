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
    and trade_num NUM (+0x5a8/+0x5ac), extra_time STR (+0x5b0), guest STR (+0x5f0; GUEST / GUEST_NONE /
    PLATFORM_GUEST), is_equalization / is_3member_coop / is_ex_substitution "YES"/"NO" (+0x610/+0x630/+0x650).
  - **match_time = match length in real minutes [live]:** without the blob a whole match took ~2 minutes
    (1ST→BREAK1 3345 gtimer ticks); with match_time 5 the first half took 8100 ticks (2.5 min). A 10-minute
    Quick Match half is ~17150 ticks.
  - **is_equalization [live]:** "YES" (and the default) shows "Any players used in this competition will have
    their overall ratings recalibrated…" at team select, and before the match the game sends
    GET_PLAYER_PARAMETERLIST with `is_equalization:"YES"`, expecting the evened-out stats from the server. With
    "NO" the notice is gone. pestourney always sends "NO" (it makes no such stats).
  - condition / extra_time / guest / ranking_match_num / trade_num: values and effects not tested.
- **TEAM_LIST** (0x141bdf3b0): `team_list` [team ids], team_list_num. National-team ids 1–14, 45, 50 checked with
  pesdb.py; the select screen groups them by region.
- **1VS1_MAINMENU_INFO** (0x141c36530): compe_end_time ("Competition ends in"), matching_first_date +
  matching_time = the end of the matchmaking window ("Matchmaking window closes in"; [live]: a value of
  end − now showed the wrong time), matching_interval, point, win/draw/lose_count, match_num ("Matches to achieve
  record" showed the match count), regulation_match_num (the "Best N consecutive match result" header; 0 shows
  "Best 0"), rank_type, team_id (the flag and name shown), team_name.
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

## Not built / open

- Rewards (REWARD_INFO/REWARD_LIST/present box), participation rights (PARTICIPATION_RIGHT), the member list,
  clan and 3-vs-3 (co-op) competitions, the "best N consecutive matches" ranking rule (regulation_match_num),
  rank_type values, the effects of condition / extra_time.

## Testing notes

- `tools/tourney-enter.sh INST [DOWNS]` (game folder) takes a copy from the main menu to the 1v1 menu.
  Dialogs come up late: retry the Return + `ok 1` pair.
- Several menus keep their cursor between visits; a key sent before a screen is ready lands on the next one.
- tools/attack.py once kept running after full time (the log tail had no phase line any more) and kept a copy
  leaving menus; it now stops at the first END/result logged after it started.
