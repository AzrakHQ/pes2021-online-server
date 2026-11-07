# Group "menus": fair-play checks, news/info, profile/account, store

## Corrections from later findings (2026-11-05)
- datacheck.md Q1 (static, 2026-11-06): the peers exchange revision, param_check_sum and entry_check_sum; only **param then entry** are compared (exact strings) → MXNV_0017 "DATAPACK error"; revision is never compared; peer record missing for 60 s → MXNV_0015; `GET_SERVER_ENV.is_revision_check` must be exactly "YES" (default when absent) to compare; "NO" skips it. Live confirmation still to do.
- On-screen codes: CCWR450 is shown as **CCWR_0014**, CCWR455 as **CCWR_0015**, CCWR211 as **CCWR_0003** (tag + sub-code; messages.md).
- `regstate(id)` in param_check_sum is **not** a registration flag: it is the player's weekly condition from PlayerWeekly.bin, used only in live-data mode, otherwise the constant 5 (db.md, confirmed live in livetests.md).


Static RE of PES2021.exe 1.01.00 plus our traffic logs. Tags: **[static]** read in code at the cited address,
**[log]** seen in logs/*.log, **[inferred]**, **[guess]**. Work files (asm dumps, helper scripts):
the working folder (dumps not published; general tools in `notes/re/functions/tools/`) (tools/: `obj.py` dumps each CommandObject's unique vtable slots, `pfields.py`
turns a reply-parser dump into key→Result-offset, `enumtab.py` resolves enum-name tables).

## How this area works

* Each command's `command::CommandObjectCmdX` vtable has three per-command slots that matter: **slot 7 (+0x38)
  fills the request Option**, **slot 9 (+0x48) runs on success (NOERR)** and copies Result fields into managers,
  and **slot 22 (+0xb0)** creates the Option/Result objects. Result fields are read in slot 9 at
  "parser offset + 0x118" (example: a field the parser stores at Result+0xb8 is read as `[obj+0x1d0]`).
* String enums are compared against interned-name tables `0x141b99xxx/0x141b9axxx(i)`. The names are resolved in
  this report (for example YES/NO = table 0x1437f3868 {0 "NO", 1 "YES"}).
* **Login → online menu** (OnlineLoginTaskConnectMenu2.cpp, dispatcher 0x140fe4980, state at +0x78) runs, in
  this order: JOIN_ONLINE_MODE (0x140fe5680) → SET_MYCLUB_VSCOM_GAMERESULT (0x140fe5a80) → three myClub/other
  steps (0x140fe6100, 0x140fe62e0, 0x140fe5f70) → **CHECK_CHEAT (0x140fe4bc0)** → 0x140fe4fe0 → done
  (event 0x10d0113). Traffic for one player [log]: SEND_LOG, GET_GAME_TEAM_LIST, GET_MATCH_ANALYZE_ENV,
  GET_MANNER_WARN, GET_DMM_NOTIFICATION, GET_PRIVATEINFOTITLELIST, GET_DMM_PLAYERINFO, SEND_LOG,
  **JOIN_ONLINE_MODE, CHECK_CHEAT, GET_URGENT_ANNOUNCEMENT(PRE_MAIN), GET_MANNER_WARN, GET_PRIVATEINFOTITLELIST**,
  GET_QUICKMATCHOPTION, (MAINTENANCE_INFO), START_MATCHING …
* **News/notifications** are fetched by `process::TaskUpdateOnlineNewsAndNotifications` (0x141312070:
  DMM_NOTIFICATION, GLOBALINFOTITLELIST(Menu), PRIVATEINFOTITLELIST, DMM_PLAYERINFO) and by
  `process::OnlineInformationGetList` (0x141397df0: the Gate, Menu and PesLeague variants of the global list,
  plus the private list). Item bodies come from `CMD_GET_INFOBODY`
  (`onlinelogin::TaskGetPrivateInformationBodyText` 0x140feaeb0, and `menu::TopModeSelectWeeklyInfoPadEventDecide`
  0x140c58b90). Banner images come from `onlinesystem::UtilDlImg` (HTTP GET).
* On-screen error codes for ConnectMenu2: the file tag is **CCWR** (OnlineLoginTaskConnectMenu2.cpp).

---

### CMD_CHECK_CHEAT — data-integrity check of the player database (anti stat-edit)
- Transport: gate, svrtype MENU, client→server. [static 0x1410e7940, log]
- When: ConnectMenu2 state 9, after JOIN_ONLINE_MODE. It is sent only when the current game-mode id
  (0x14149f4f0) is in 13–19, 22, 28 or 29; otherwise the step is skipped. [static 0x140fe4e15]
- Request (Option fill 0x1410e7940):
  - `pid` — current player.
  - `dlc_version` — "1.00". [log]
  - `dlc_type` — never filled, so it is "NotImplement". [log]
  - **`param_check_sum`** = 0x141128e90(). [static] It is **not MD5** but a custom sum over the **player
    database** (`DB[+0x48]`; count at +0xd0bce8, at most 30001 records of 0x17c bytes). For each player with a
    non-zero 16-bit id (`u16 @rec+0x30`):
    `s = id + Σ ability[0..107] (accessor 0x1414c66c0, the 108 player parameters) + low byte of two extra
    params (indices 0x2e/0x2f via 0x1414c01a0) + regstate(id) (0x1415106b0, a 30000-entry id→flags table)`,
    then `acc[id & 3] += s`. The four u32 accumulators are XOR'd with the key
    `{0x29bda03d, 0x0593fbe2, 0x298015ab, 0x9fbead21}` and printed with `std::hex|uppercase`, `setw(8)` and
    fill '0', giving 32 uppercase hex characters.
  - **`entry_check_sum`** = 0x141129240(). [static] A sum over **team rosters** (count at DB+0xd0bcec, at most
    750 teams of 0x690 bytes at DB+0xadf4bc): `s = teamId (dword@team+0 >> 14) + Σ over the team's ≤40 slots
    (count = u16@+0x426 & 0x7f) of (playerId = hi-dword of qword@+0x14c+8i) + u16@+0x29a+2i
    (probably the shirt number)`, then `acc[teamId & 3] += s`. Same hex formatting, **no XOR key**.
  - So: **ability edits, transfers, roster or number changes and live-update data** change the sums. **Names,
    kits, faces, emblems, stadium and other cosmetic option-file content do not.** [static, inferred for
    "cosmetic"]
  - The same two strings are stored in the local online info object (+0x40 and +0x60, via 0x141041ed0 and
    0x141041f60). They are reused for the P2P revision check and the tamper check (both below).
- Reply: only `result` is parsed (parser 0x141d21650). [static]
- Errors (0x140fe4bc0) [static]:
  - `ERR_PARAMCHECK_FAILED` → login-error kind 0x16; error report line 450 (0x1c2), code 14
    (**CCWR450**). Online login is aborted with an error dialog.
  - Any other non-NOERR → kind 3; line 455, code 15 (CCWR455). Same abort.
  - Command creation failure → line 434.
  - **A non-NOERR reply therefore blocks online entry. There is no "warn and continue" path.**
- Logs: every session so far sent param=`25D6DFBB091E75D525E090F993DBAE73`,
  entry=`1E21A82F1DFF00C9DBE591735FF8746A` (48/48 requests, both players). [log]
- Server should: store `(pid, param_check_sum, entry_check_sum, patch/dlc/livedata versions from
  JOIN_ONLINE_MODE)` per session. In matchmaking, **only pair players whose param_check_sum (and revision) are
  equal**. Otherwise the P2P revision check aborts the match after connecting (see below). Optionally add a
  strict mode: keep a whitelist of accepted sums and reply `ERR_PARAMCHECK_FAILED` to others. That blocks the
  player with CCWR450. Default: NOERR.
- Confidence: high for the algorithms and error paths (code read end to end). Medium for the field semantics
  (shirt number, regstate).

### P2P revision check (MultiplayCmdRevisionCheck): where the checksums are actually enforced
- Peers exchange `CmdRevisionCheckSend/Receive` (keys: msgid, rqid, pid, platform_id{blob}, np_account_id,
  xuid_one, name, revision, **param_check_sum**, **entry_check_sum**, is_request; receive parser 0x1411b01a0,
  fields at +0x108/+0x128/+0x148). The local record is built in 0x140ffbe90 (revision string from
  `[0x14149a470()+0xe8]`, param sum from the info object). [static]
- Verdict 0x141001190 (writer of multiplay+0x5c4 in 0x141000ea0):
  - When flag `[mp+0x5c0]` is set, for each peer two adjacent strings are compared with the local copy (record
    +0xa0/+0xc0). [static]
  - By offset alignment these are **revision and param_check_sum**. [inferred]
  - Any mismatch → result 4 = **"DATAPACK error"** (OnlineModeTaskMatchSession line 533, MXNV533). A wait past
    60 s → 2 "Revison check TIMEOUT". [static, see match.md]
  - The flag is most likely `is_revision_check` from GET_SERVER_ENV, which our server sends as "YES". [inferred]
- Effect: two players with different player databases (stat mods, different live update) can be paired but
  fail at session start. Pairing on equal CHECK_CHEAT sums avoids this. Sending `is_revision_check:"NO"`
  should let mismatched players play anyway, at the risk of desync (untested).

### CMD_SEND_TAMPER_DATA — client self-report of in-memory tampering (not in scope list; found via the checksum)
- Transport: gate MENU. Request: `pid, match_category (enum 0x141b99ef0), data (text), error_code (int)`. Reply:
  result only. [static 0x141d67bd0, 0x1410f8d80]
- When: `onlinemode::TaskWatchCheat` (0x1411263a0) and the check 0x141126580. The check is called from
  `process::OnlineQuickAfterMatchMenu` (0x14139c470) and `process::OnlineCpuMatchStart` (0x1413f17d0). [static]
  - It recomputes param_check_sum and compares it with the value stored at CHECK_CHEAT time (0x140ffbc50).
  - A mismatch (stats edited in memory after login, for example by a trainer) posts event 0x1070009, which
    triggers the report.
  - It also scans the squad for "Unregister:<id>", "Doping:<id>" and "costover" entries; these go into `data`.
    [static strings, inferred flow]
- Not seen in our logs.
- Server should: reply NOERR and **log it as a cheat flag for that pid** (admin page). This costs nothing and
  is a useful anti-cheat signal.

### CMD_GET_MANNER_WARN — fair-play (disconnect) rating warning
- Transport: gate MENU. Request has no extra fields (packer sends only the basics; fill 0x1410edec0 sets
  svrtype/client/pid). [static, log]
- When: `onlinelogin::TaskWarningManner` (0x140fdf490, at online login) and `process::OnlineWarningManner`
  (0x14138b950, the "Online/Match/ProcWarningManner" flow). Sent twice per session in our logs. [static, log]
- Reply: `warn_kind` STR (Result+0xb8). Slot 9 (0x1410edf57) maps it [static]:

  | warn_kind | kind | message id | text (live 2026-11-07) |
  |---|---|---|---|
  | "DOWN_B" | 1 | 0x20100c0 | "The number of times you have failed to properly complete matches is increasing. …" |
  | "A_TO_B" | 2 | 0x20100c1 | "Your courtesy rating has fallen to "B". …" |
  | "DOWN_C" | 3 | 0x20100c2 | "The number of times you have failed to properly complete matches remains high. …" |
  | "STILL_C" | 4 | 0x20100c3 | "Your courtesy rating has fallen to "C". …" |
  | anything else / missing | 0 | no dialog | |

  The value is stored at obj+0x120. The tasks open a dialog (0x140fdf5f0 / 0x14138b880) with that message and
  wait until it is closed. **Correction (live):** the names don't say what the texts say. DOWN_C is the "remains
  high" warning (at B), and STILL_C is the fall to C. The dialog is a DialogProceed; OK continues into the mode.
- Errors: non-NOERR → the task finishes silently with no dialog (state 3). [static 0x140fdf4da]
- Server (pesmanner.py, 2026-11-07): rates each player on their last 10 matches and sends one warning per
  early exit: still A → DOWN_B, falls to B → A_TO_B, at B → DOWN_C, falls to C or another at C → STILL_C. The game only displays the warning; matchmaking restrictions
  (ERR_COMPE_JOIN_MANNER, ERR_COOPMANNER_NOTMET) are separate errors.

### CMD_SEND_LOG — live-update log upload
- Transport: gate MENU. Request `title, log`. [static 0x1423d9f00]
- When: sent by `LiveDataSetFlow` (0x1420ac3f0 → 0x1420ac0b0) during the live-update check. `title`
  (0x1411cf9c0) = "LiveUpdate"; `log` (0x1411cf980) = "NotImplement" in every capture, because the log buffer
  is a stub in retail. [static, log]
- Reply: result only.
- Server should: NOERR; log it. No content of value.

### CMD_CHECK_STRING — NG-word (profanity) filter
- Transport: gate (onlinesystem command, created through factory 0x14116dee0). Request `str`. Reply result only.
  [static 0x141e47c40]
- When: `onlinesystem` NG-word checker 0x141158940/0x141158a30, used by: profile settings
  (`menu::OnlineSettingProfile`), co-op room text, clan edit, `process::OnlineLobbyMultiCheckNGWord`, myClub
  team name, home town and squad name. [static]
- Effect: NOERR → accepted (state 2 → returns 1). **Any non-NOERR → treated as an NG word** (state 4 → returns
  2, and the caller shows an error dialog). Related error strings: ERR_NGWORD, ERR_NGWORDINPNAME,
  ERR_NGWORDINRNAME, ERR_NGWORDININTRO. [static 0x141158ad7]
- Not seen in logs (our tests never typed a name).
- Server should: NOERR. Optionally run a word list and reply `ERR_NGWORD`.

### CMD_GET_GLOBALINFOTITLELIST / CMD_GET_PRIVATEINFOTITLELIST — news list (Information menu, weekly info)
- Transport: global = gate, svrtype **GATE** (second gate session, `kind DEFAULT`) or MENU (Menu/PesLeague
  variants). Request: `lang, region, package_type, kind`. Private = MENU, request `pid, lang, region`. [log, static]
- Reply (parsers 0x141c746b0 / 0x141c24ca0, identical layouts) [static]:
  ```
  { result:"NOERR",
    announce_info_list: [ <msgpack BLOB of map>, ... ],   // list of nested-msgpack blobs, element stride 0x178
    annnounce_info_list_num: N }                          // note the triple-n spelling; stored, not needed for iteration
  element map:
    announce_type   STR  NO_BANNER_INFO | BANNER_INFO | WEEKLY_INFO | COMPE_INFO | PERSONAL_INFO   (+0x00)
    title           STR  (+0x20)
    announce_id     NUM  u32 (+0x40)      priority NUM u32 (+0x44)
    popup_type      STR  ONCE | DAILY | EVERYTIME (+0x48)
    weekly_info_type STR MYCLUB_AGENT|MYCLUB_EVENT_COMPE|MYCLUB_VSCOM_COMPE|MYCLUB_PESLEAGUE|MYCLUB_MATCHDAY|PESLEAGUE|MATCHDAY (+0x68)
    event_id        NUM  (+0x88)          date NUM u32 (+0x8c)
    evoked_text     STR  (+0x90)
    list_banner_file_info  BLOB{ filename STR (+0xb0), hash_str STR (+0xd0) }
    matchday_emblem_info   BLOB{ is_use_emblem STR, is_auto_make_compe_name STR, matchday_group_type STR,
                                 team_list_group_a [NUM], team_list_group_b [NUM], compe_db_id NUM }
  ```
  - Values of type 0 (nil) are skipped; missing keys keep their defaults. [static 0x141c74ebe]
  - On success, 0x1410c1180 clears the info manager (`[loginmgr+0x70]`), converts each element (0x1410c0980,
    type 2 = global) and adds it (0x141027130).
  - The enums were resolved from tables 0x1437f2908 (announce types), 0x1437f3928 (popup types) and
    0x1437f25a8 (weekly types).
- Text encoding: msgpack str copied as raw bytes into std::string. **UTF-8** is the natural choice (the game's
  UI text is UTF-8). [inferred]
- `date`: u32. Probably Unix seconds, like maintenance_time, which is definitely epoch → `_localtime64_s`.
  [inferred]
- **Private list, live 2026-11-07:** the game asks for GET_PRIVATEINFOTITLELIST at login and on every online-mode
  entry (right after GET_URGENT_ANNOUNCEMENT). A new PERSONAL_INFO item with popup_type ONCE pops up at once: the
  game fetches its body with CMD_GET_INFOBODY {is_private "YES"} and shows it in an `onlinelogin::
  MenuInformationNoticeTask` window (A = vtable +0x188 closes it). After it was closed, it did not pop up again,
  even after a game restart. Items it had already shown did not pop up again with any popup_type
  (EVERYTIME, DAILY, "", NONE, none). The box before Quick Match is an `onlinelogin::MenuUrgentAnnouncement`
  window, closed the same way.
- Server should: **yes, our server can publish its own news.** Send elements with
  `announce_type:"NO_BANNER_INFO"` (no image needed), a unique `announce_id`, `title`, `priority`,
  `popup_type:"ONCE"`, `date:<unix>`, `event_id:0`, and serve the body via CMD_GET_INFOBODY. Keep sending bare
  NOERR (empty list) until this is tested live.

### CMD_GET_INFOBODY — body of one news item (sent when the item is opened or auto-popped)
- Transport: gate MENU. Request `pid, announce_id, is_private, lang, region`. [static 0x141ba37f0]
- Reply (0x141ba40a0) [static]:
  ```
  { result, announce_info: BLOB{
      announce_basic_info: BLOB{ <same element fields as the title list> },
      body STR (+0x230), link_url STR, direct_move STR, use_template STR, template_id NUM,
      banner_file_info: BLOB{ filename STR, hash_str STR }, compe_name STR, compe_start_time NUM } }
  ```
- Slot 9 (0x14105fc50) [static]:
  - It matches `announce_type` against the 5 names.
  - For BANNER_INFO, WEEKLY_INFO or COMPE_INFO it downloads `banner_file_info.filename` from the **TOPINFO**
    URL (url-type index 9). It uses `UtilDlImg(url, "fname", "version")` and requests
    `GET <TOPINFO url>?fname=<filename>`. `hash_str` is the `version`, used as the cache key; it is not put
    in the query (0x141164a30 builds `url + '?' + key + '=' + value`).
  - For weekly info it uses **WEEKLY_INFO_BANNER** (index 45) with key `announce_id`.
  - The response is **raw file bytes**, at most 409,600 B (0x64000 buffer), stored as-is for the menu
    (0x1410260c0). The image format is not determined (see Still unknown).
- Server should: answer with `announce_basic_info` (echo the list element), `body` (UTF-8), `use_template:"NO"`,
  `direct_move:"NO"`, `link_url:""` (a link may open an external browser, so do not use it), and leave
  `banner_file_info` out.

### CMD_SET_WEEKLY_INFORMATION_ACTION — acknowledge a weekly-info popup
- Request `pid, announce_id, action_kind`; reply result only. Server: NOERR (could record "read").
  [static 0x141ba7240]

### CMD_GET_URGENT_ANNOUNCEMENT — blocking announcement before entering a mode
- Transport: gate MENU. Request `pid, package_type, online_mode (QUICK…), announcement_type (PRE_MAIN), lang,
  region`. [log]
- Caller: 0x140fed150. After the reply it opens `MenuUrgentAnnouncement` if the announcement manager
  (`[loginmgr+0x100]`) holds one. [static]
- Reply (0x141c6b0f0) [static]:
  ```
  { result, is_announcement:"YES"|"NO",
    announcement_data: BLOB{ online_mode STR, announcement_type STR, announcement_id NUM,
                             read_check "YES"|"NO", title STR, body STR } }
  ```
  - Slot 9 (0x1410bf5d0) runs only when `is_announcement == "YES"`. It then maps:
    - `online_mode` against {QUICK, RANKMATCH, ?, MULTI, COMPE, ?, MYCLUB, PESLEAGUE, COOP,
      EDIT_DATA_LIVE_UPDATE, MATCHDAY};
    - `announcement_type` against {PRE_MAIN, USE_SCOUT, USE_TOP_AGENT, ?, CONTRACT, RECOVERY, STORE,
      SUBSCRIPTION};
    - `read_check` against YES/NO;
    and stores title and body in the manager.
  - `read_check:"YES"` probably means "show once per announcement_id". [guess]
- Server should: NOERR with `is_announcement:"NO"` (the current bare NOERR behaves the same). To push a
  server-wide notice before Quick Match, send "YES" plus a blob echoing the request's online_mode and
  announcement_type with title and body. **This is the simplest way to put server text on screen.**

### CMD_GET_MAINTENANCE_INFO — "maintenance soon" prompt before matchmaking
- Transport: gate MENU, no extra request fields. [log]
- Users: `process::OnlineMaintenanceInfo` (0x1413916d0, before Go to Matchmaking) and 0x141044430 (stores the
  flag and time in a manager through 0x14103eaf0). [static]
- Reply (0x141d12fd0): `maintenance_warn` STR (Result+0xb8), `maintenance_time` NUM u32 (+0xd8).
- Effect [static]:
  - When `maintenance_warn == "YES"` (0x141391640), the game shows a Yes/No dialog (message 0x1fa00e8, layout
    "OnlinePassAgreement").
  - The time is `maintenance_time` converted with `_localtime64_s` (0x141160160, year+1900), so it is **Unix
    seconds**, shown as local time.
  - Yes → proceed; No → cancel back to the menu. A non-NOERR reply or "NO" → proceed silently.
- Server should: bare NOERR. Before a planned restart, send `{"maintenance_warn":"YES","maintenance_time":<epoch>}`
  to warn players who are about to queue.

### CMD_GET_ONLINE_AD_FILE — stadium ad-board / banner files for a match
- Transport: gate MENU. Request `pid, ad_match_kind (PLAYMATCH…), compe_id`. Objects: Base, PlayMatch,
  Matchday, MyClubEvCompe, PesLg. [log, static]
- Reply: `ad_banner_file_name` STR, `ad_signage_file_name` STR. [static 0x141dde9c0]
- `onlinemode::TaskOnlineAdDownloader` (0x1410327c0 → 0x141033000) downloads each name with
  **`GET <ONLINE_AD url>?fname=<name>`** into a 1,142,784 B (0x117000) buffer. [static]
- The ADFILE url type is not referenced by this path. [static]
- Server should: bare NOERR (empty names → nothing downloaded). Custom ad boards are possible in principle,
  but the file format is unknown.

### TOPINFO / BANNER / WEEKLY_INFO_BANNER / ONLINE_AD / ADFILE url types
- URL-type enum index = position in table 0x1437f3a20 (getter 0x141b9a250). [static]
- Users per index:
  - BANNER (5): PES League / competition menus, `UtilDlImg` with fname/version.
  - TOPINFO (9): news body banners.
  - MYCLUB_AGENT_BANNER (17), DMM_TOP (18), MYCLUB_LIVEEVENT_BANNER (39).
  - WEEKLY_INFO_BANNER (45): key announce_id.
  - WATCHING_GAME (46).
  - ONLINE_AD: by name, `?fname=`.
- No code path for **ADFILE** was found.
- All of these are GET `url?key=value` downloads of raw bytes (not msgpack). Our server only needs to answer
  them if it ever names a file in a reply.

### CMD_GET_GP_ACHIEVEMENT — GP balance and achievements
- Transport: gate MENU (sent right after SET_CURRENTPLAYER). [log]
- Created by name "CmdGetGPAchievement" (capital GP, 0x140ff4ad0). [static]
- Reply (0x141c908b0): `all_gp` NUM, `pes_point` NUM, `all_achievement_bit_array` [NUM list]. Slot 9
  (0x1410c70f0) copies all_gp → obj+0x138, pes_point → obj+0x13c, and the u32 bit-array into a vector. [static]
- Server should: `{"all_gp": <GP>, "pes_point": 0, "all_achievement_bit_array": []}`. Bare NOERR shows 0 GP.
  GP and coins only matter for myClub, which we do not run.

### CMD_GET_PLAYER_PARAMETERLIST — server-side player stats override (myClub squads)
- Transport: gate MENU. Request `pid, player_list` (blobs `{gameplayer_unique_id, gameplayer_id}`),
  `is_equalization`. [log]
- When: `TaskSyncTeamData` (0x14139bac0, Quick team sync) and the co-op matching manager; also
  `menu::OnlineMyClubMatchingVsCom`. [static]
- Reply (0x141d8a120):
  ```
  { param_playerlist: [ BLOB{ gameplayer_id_info: BLOB{gameplayer_unique_id, gameplayer_id},
                              parameters: [NUM × 108] } ],
    is_cost_over: "YES"|"NO" }
  ```
- Slot 9 (0x141103030) [static]:
  - It walks the **reply list positionally** against the local myClub squad copy (`DB+0x1795f10`).
  - Entry without parameters → copies the DB stats (accessor 0..0x6b).
  - Entry with parameters → applies the server values (0x1410290b0).
  - `is_cost_over == "YES"` sets a flag; then the squad is written back (0x1414e1790).
  - An empty list changes nothing.
- Server should: bare NOERR (current behaviour; Quick Match works). If used, it uses the same 108-parameter
  indexing as param_check_sum, but this path touches the myClub squad only.

### CMD_GET_AVATAR_HTTP (AVATAR url type) / CMD_SET_AVATAR — profile avatar image
- Transport: HTTP POST form `msgid, rqid, urltype=AVATAR, client=NotImplement, pid=<whose avatar>`. Sent
  during matching, for each player. [log]
- Reply (0x141bb1b90): `avatar_data` STR = **base64** (decoder 0x1418574d0), `avatar_data_size` NUM (decoded
  size), `avatar_md5` STR. [static]
- Slot 9 (0x141093160) [static]:
  - It computes **MD5** of the decoded data (`fox::ncl::NclDigestMd5`) and formats it as **lowercase hex**
    (0x141157a00).
  - It requires size ≠ 0, size ≤ 0xC000 (48 KiB), size ≤ the caller's buffer, and md5 equal to `avatar_md5`.
  - Pass → copies the data and clears the error flag (+0x1ac=0). Fail → flag=1 (default avatar).
- CMD_SET_AVATAR (gate MENU; `avatar_data_size, avatar_data, avatar_md5`) is the upload. It was not seen in
  our logs.
- Server should: bare NOERR (= no avatar). Later: store whatever SET_AVATAR uploads and return it verbatim.
- **Done and live-tested 2026-11-07 (pesavatar.py):**
  - **Editor:** Settings → Online Settings (main-menu item 0x21, `pesflow go settings`) → Edit Profile → Avatar
    (Online/Avatar/MenuAvatarEdit). It has Appearance, Facial Expression, Background Color and Uniform Color, and
    draws a 3D face.
  - **Upload:** OK after a change sends gate `CMD_SET_AVATAR {pid, avatar_data_size 49152, avatar_data (65536
    base64 chars), avatar_md5}` once, in fragments (kind 0x70). The game then asks GET_AVATAR_HTTP for its own pid.
  - **Format (rendered by the server, matched the game):** 128×128, 3 bytes a pixel = R, G, B, A at 6 bits each,
    big-endian (R in the top bits), top row first. The alpha makes the round shape.
    A served test pattern of red/green/blue/white bands showed in that order in Edit Profile.
  - **Seen by others:** copy 2's Matchmaking screen showed copy 1's avatar beside its name (GET_AVATAR_HTTP
    pid=1 from copy 2).
  - The static details (editor states, the loader cache used by ~24 screens) are in the avatar agent's notes,
    summarised here.

### Profile: CMD_SET_PLAYERPROFILE / CMD_GET_PLAYERINFO (Edit Profile) — live 2026-11-07, pesprofile.py
- Edit Profile (Online/Setting/SettingMenuProfile) shows: the player name, Avatar, Favorite Team, Favorite Player
  and Comment. All three texts are free text from the on-screen keyboard.
- Leaving it after a change sends gate `CMD_SET_PLAYERPROFILE {profile: blob{date 0, favorite_team,
  favorite_player, intro, is_enable_myPES "NO"}}`. The reply fields error_favorite_team / _player / error_intro
  are not needed: a bare NOERR keeps the new values.
- Opening it sends `CMD_GET_PLAYERINFO {pid, user_id: blob{pid, pname{…}}}` (the player being looked at).
  - The parser reads `pname` (blob; name in the header), `favoriteTeam`, `favoritePlayer` and `intro` at the
    **top level** (parsebench, several placements in one run). Nothing nested is read for them.
  - Missing fields show as "NotImplement", which is what the bare reply gave.
  - The reply also has `manner` (OBJ) and `disconnect_rate` (STR); not decoded yet.
- Live: values stored on the server showed in Edit Profile, and an edit in the game was saved.
- **Other players' profiles** (e.g. Online Settings → Match Block List → A → User Details =
  Online/UserList/UserListPlayerInfo, 7 pages with LB/RB) show two more rows:
  - **Courtesy Rating** comes from `manner: blob{manner: "C" | "B" | "A" | "AA" | "AAA"}` (live: A, AA, AAA and
    B shown as sent; missing → "C").
  - **Sending `manner` as a plain string crashes the game when the profile opens** (live 2026-11-07; the scenario
    runner caught it and relaunched).
  - **Number of Disconnections** comes from `manner.disconnect_rate` (a std::string at Result+0x2a0). "HIGH",
    "MIDDLE", "LOW", "high", "50", "0.5", "100" and "20" all showed "Low", so the values that change it are
    still unknown [needs static reading].
  - Pages 2+ are play-style analysis (Off. Awareness, Advanced Instructions, Favorite Move, Attacking Areas):
    empty, from other commands.

- **Profile page 2, "Online Divisions"** (live 2026-11-07): `rankmatch_stats` must be a **blob** {division "D8",
  rating, max_weekly_point, current_weekly_point, match_num, win_num, lose_num, draw_num, contWin_num,
  contWinMax_num, goal, lostGoal}. An inline map faults inside the parser (the probe's crash guard caught it).
  - Shown: Division badge, Rating, Matchday Class (from `matchday_rating_rank`; default "Expert"), Matches,
    W/D/L, Win Rate (computed), Consecutive Wins/Most Consecutive Wins, Avg Goals Scored/Conceded (goal /
    matches).
  - "Teams Chosen in Last 5 Matches" comes from `rankmatch_teamLog`, not sent yet.
- `matchday_stats_single` blob {entry_count, group_win_count, pickup_count, best_rank, win_count, draw_count,
  lose_count} is read the same way (parsebench). Its page was not opened.

### Match Block List: CMD_GET_MATCHREJECTLIST / CMD_SET_MATCHREJECTPID — pesblock.py (2026-11-07)
- Online Settings → Match Block List opens Online/UserList/UserList ("User List") from GET_MATCHREJECTLIST
  {pid} → `reject_list` [blob{pid, pname}] + `reject_list_num` (live: the stored entry showed by name).
- A on an entry offers: User Details, Play Coverage - Online Divisions, Play Analysis - myClub: Ranked Match,
  Remove from Match Block List.
- SET_MATCHREJECTPID {pid, reject_pid, is_reject YES/NO} [static packer 0x141d73b70] adds or removes one player.
  Where a player first adds someone was not traced; removing was not run live.
- The server never pairs two players in Quick Match / Divisions / Matchday when either has blocked the other.
- **Live 2026-11-07:**
  - With pid 1 blocking pid 2, two copies searching Quick Match were never paired.
  - **Adding a block in the game:** main-menu item 0x1a "Online Stats" (User Details, Overall Ranked Match
    Rankings, **Last 20 results**, eFootball League Competition Results, eFootball League Past Competitions) →
    Last 20 results (Online/Information/InformationOldMatchRecord, from CMD_GET_GAMERESULTS {pid,
    game_res_type "ALL", pesleague_compe_id 0}) → A "Participating Users" → the opponent → **"Add to Match
    Block List"** → confirm → `SET_MATCHREJECTPID {pid 1, reject_pid 2, is_reject YES}` → GET_MATCHREJECTLIST →
    "Added".
  - It needs GET_SERVER_ENV `matchrejectpid_max` > 0: the server sent 0 until 2026-11-07, which made every Add
    say the list is full [static 0x140bdcd75].
  - The profile-analysis agent's notes: "Add" is offered only in past-results lists (also Divisions → Past Game
    Results, myClub → Game Results); "Remove" only in the Match Block List. Chat presets are kept in the local
    save only; no server command exists for them.
- **Number of Disconnections** values [static 0x1410a41f0, table 0x1437f33f0]: "LOW_RATE" = Low, "SEMI_HIGH_RATE"
  = Moderately High, "HIGH_RATE" = High, anything else = Low (exact, case-sensitive, inside `manner`). The server
  maps early exits: 0–1 LOW_RATE, 2–3 SEMI_HIGH_RATE, 4+ HIGH_RATE.

### CMD_GET_GAMERESULTS — the "Last 20 results" list (pesresults.py, live 2026-11-07)
- The reply `rlist` [blob{date, ex_type, pk_flag, no_match, player [blob{pid, pname, side, is_com, cpu_level,
  is_follow, is_guest}], team [home id, away id], score [blob{score_1st, …} × 2], match_category,
  gameend_reason}] + `rlist_num` fills it. It shows date/time, names, score, Win/Draw/Lose and team crests.
- The header label comes from `match_category`, an exact name of the 25-name enum 0x1437f2310 [static
  0x141058a30; label switch 0x140be28dd]. Unknown names count as 0 = RANK_MATCH:
  - "Online Divisions": RANK_MATCH (and anything unknown);
  - "Competition": COMPE_MATCH; "eFootball League": PESLEAGUE_MATCH;
  - "Matchday": MATCHDAY / MATCHDAY_COOP;
  - "myClub": the MYCLUB_* names except the co-op/lobby ones;
  - no label: the co-op and lobby names (PESLEAGUE_COOP_MATCH, ONLINE_COOP, PESLEAGUE_LOBBY, …).
  - Live: MATCHDAY → Matchday, ONLINE_COOP / PESLEAGUE_LOBBY → blank, the OC_* names → Online Divisions.
  - No name gives "Quick Match" or "Friendly Match", so the server sends PESLEAGUE_LOBBY (blank) for those.
  - The name also picks the profile page that opens; it never hides Participating Users or Add to Match Block
    List.
- "RANK" (Divisions → Past Game Results) gets the Online Divisions matches only.

### CMD_SEND_MATCH_ANALYZE / CMD_GET_MATCH_ANALYZE — Play Analysis (pesresults.py) [static, agent 2026-11-07]
- SEND {pid, analyze_data_type ONLINE_DIVISIONS|MYCLUB_DIVISIONS, analyze_data: {46 ints + game_res STR +
  intercept_area [9 ints]}} is sent after Online Divisions (gamemode 24) and myClub matches; Quick Match sends none.
- GET {pid, target_pid, analyze_data_type} → `analyze_data_size` + `analyze_data_list` [inline maps, the same 46
  keys]. The game uses the last 10 and does the aggregation itself, so the server stores the uploads and returns
  them. Not run live yet (needs a finished Divisions match).

### CMD_SET_LANGUAGE
- Transport: gate ACCOUNT, `lang` ("US"). Both success and error handlers (slots 9 and 10) just "proceed",
  so the result is ignored. [static 0x1410fbaa0/0x1410fb910, log]
- Server should: NOERR; store lang per account (for localized news).

### CMD_JOIN_ONLINE_MODE
- Transport: gate MENU. Request `online_mode (QUICK…), patch_version "1.01.00", dlc_version "1.00",
  livedata_version, extradata_version`. [log]
- Handler: ConnectMenu2 0x140fe5680. [static]
  - `ERR_MODE_BLOCKED` → login-error kind 0x15, line 211 (**CCWR211**), online entry aborted.
  - Other errors → kind 3, line 216 (CCWR216).
- Server should: NOERR. This is the natural **version gate**: reply `ERR_MODE_BLOCKED` when patch, dlc or
  livedata versions differ from the server's required set (better than letting players fail with DATAPACK
  errors). Store the versions with the session.

### CMD_LEAVE_ONLINE_MODE / CMD_JOIN_OFFLINE_MODE / CMD_LEAVE_OFFLINE_MODE — presence notifications
- LEAVE_ONLINE: `online_mode`. Sent by `process::ProcessEditLiveUpdateLoginFunctor` (0x1413aa9e0) after the
  edit-mode live update.
- JOIN/LEAVE_OFFLINE: `offline_mode` ("EXHIBITION"). Sent by `menu::ModeFlowCmnInit` (0x140aefc20 / 0x140aef2e0)
  when entering or leaving offline modes while logged in. [log]
- The results are ignored: the users only poll for done and delete the command. [static]
- Server should: NOERR. These could drive an "in menus / playing offline" presence status on the admin page.

### CMD_GET_DMM_NOTIFICATION / CMD_GET_DMM_PLAYERINFO — **not** a Japan-only store feature
- Both are sent on the Steam build every session, from `TaskUpdateOnlineNewsAndNotifications`. [log, static]
  "DMM" here is the top-menu **Notifications** feed.
- DMM_NOTIFICATION request: `pid, lang, region, package_type`. Reply (0x141cf8f80): `info_list` [BLOB{
  id, date, is_watch, type, header_text, title, body, evoked_text, link_url, storelinkinfo BLOB{type, link,
  service_id}, thumbnail_info BLOB{filename, hash_str}, movie_url, gameplayer_list BLOB{gameplayer_id,
  position, default_overall, default_rarity, nation_id, club_team_id}, gameplayer_list_num }], `info_list_num`.
  Enums (slot 9 0x1410dd580) [static]:
  - `type` ∈ {FEATURED_PLAYER, LEGEND_PLAYER, STORE_LINK, WEB_LINK, ICONIC_MOMENT};
  - rarity ∈ PLAYER_RARITY_{WHITE, BRONZE, SILVER, GOLD, BLACK};
  - storelink type ∈ {SINGLE, GROUP, ALL, …}.
  - Thumbnails use url-type DMM_TOP. CMD_SET_DMM_NOTIFICATION_ACTION `{notification_id, action_kind}` is the
    read acknowledgement.
- DMM_PLAYERINFO reply: `is_present_inbox` STR (YES/NO → the myClub gift-inbox badge). [static, inferred]
- Server should: bare NOERR (no notifications, no badge). Do not use WEB_LINK or STORE_LINK, because they lead
  to an external browser or the store.

### CMD_GET_STORE_PRODUCT_LIST / CMD_GET_STORE_TRANSACTION_STATE / STEAM_STORE_* — myClub coin store
- PRODUCT_LIST and TRANSACTION_STATE go over the gate. svrtype and client are never filled, so they read
  "NotImplement". [log]
- They are driven by the `onlinesystem::Store` state machine 0x141162240:
  PRODUCT_LIST → TRANSACTION_STATE → (if a transaction is pending) END_STEAM_STORE … [static]
- PRODUCT_LIST reply: `product_list` [BLOB{product_id, psn_*, live_*, total_point, base_point,
  bonus_point, expiration_date, is_purchasable, steam_item_id, steam_dlc_id, is_consumable, is_subscription}],
  `product_list_num`.
- TRANSACTION_STATE reply: `state` ∈ {STATE_WAIT, STATE_PROGRESS} (table 0x1437f25f0), product_id, use_num,
  possession_num, live_transaction_id, transaction_id, used_present_product_id_list[], steam_order_id.
  [static 0x141e38b00, 0x14120e4c0]
- GET_STEAM_STORE_STATE, START_STEAM_STORE and END_STEAM_STORE are **HTTP** (url types STEAM_STORE_STATE /
  _START / _END; forms carry ticket, steamid, orderid, itemid, language). START is triggered only from a
  purchase dialog (0x140be6e00). [static]
- Server should: bare NOERR for PRODUCT_LIST and TRANSACTION_STATE (empty, no pending transaction). For
  START_STEAM_STORE reply an error (no orderid), because we cannot run a Steam microtransaction.

## Still unknown (and what would settle it)
1. **Exact P2P fields compared** (revision + param vs param + entry) and whether `[mp+0x5c0]` is
   `is_revision_check`. Test: one client with a single edited player stat → expect MXNV533 "DATAPACK error";
   then repeat with `is_revision_check:"NO"`.
2. **On-screen text of CCWR450, CCWR211 and the manner messages 0x20100c0–c3**: these come from the game's
   message files. Test: reply ERR_PARAMCHECK_FAILED once and photograph the dialog.
3. **News UI**: whether NO_BANNER_INFO items show in the Information list without an image, which sort order
   `priority` gives, and that `date` is Unix seconds. Test: one NOERR list with 2 items plus an INFOBODY reply.
4. **Banner, ad and avatar file formats** (raw bytes go straight to the menu texture loader). Test: serve a
   PNG, then a DDS, at the TOPINFO URL.
5. Index 2 of the online_mode enum and index 3 of the announcement_type enum were not resolved (resolver
   misses).
6. When TaskWatchCheat runs in a Quick Match and what `match_category` and `error_code` look like. Test: edit
   a stat in memory after login, finish a match, look for CMD_SEND_TAMPER_DATA.
