# Group msg: what the player sees (message ids, error dialogs, on-screen codes)

## Corrections from later findings (2026-11-06)
- misc.md §3 (static, 2026-11-06): `{re}`/`{rf}` both expand to "PES 2021"; the three 0x14114e7e0 checks are "GATE / ACCOUNT / MENU connection up"; 0x1410f8010 fills CMD_SEND_NOTICE; 0x141070880 is a Matchday task, not a command. Live: SET_GAMEMEMBER `ERR_DATABASE` → "An unexpected problem has occurred while communicating with the PES 2021 server. CODE:C_DYUN_0013".


Working files are in the working folder (not published):
- The full message-id → text dump (1563 ids) is not published; `data/server_errors.tsv` has the texts of every server reply. Macro tokens `\x11xx` are shown as `{xx}`.
- `server_errors.tsv`: all 764 rows of the client's server-error table, with the text the player sees.
- `onscreen_codes.tsv`: the code each error-reporter site displays.
- `allstrings.json`: every string in `use/string/all.str` and `common/string/sys_use.str` (raw text, group/id).
- `code_msgids.json`: the message ids that are immediates in game code, with their call sites.
- `err_enum.json`: the ERR_* enum (index to name).
- `autodialog_flag.json`: the command classes that switch off the automatic error dialog.

String tables came from dt80_100E_x64.cpk (`use/string/all.str`, `common/string/sys_use.str`).

## 1. Message id to string (verified)
- **msgid = (group number << 16) | entry id.** The group number is the u32 at offset +4 of each group header
  in the .str file. strtool calls this field `unk`, but it is the group number (e.g. PcC = 920 = 0x398).
  [static + verified]
  - 0x0398002f → PcC/47 "Press Any Button".
  - 0x020100c0..c3 → Nm7T/192..195, the courtesy-rating warnings.
  - 0x01fa00e8 → Nm2T/232, the maintenance warning.
  - 0x00f90042/43/4f/56 → Mc5T, the live-update texts.
- Lookup code [static]:
  - 0x141497dd0(mgr, id) does `shr edx,16`, then std::map<u16 group, table*> (0x141497d20).
  - Then 0x1414996f0 does a linear scan of the entries on the u16 id.
  - Fallbacks: unknown group → "No String" (0x142968420); unknown id → "NoString" (0x142968be8).
  - Wrappers: 0x1414969e0(std::string*, id) copies the text. 0x141496ca0(str, id, ...) applies sprintf
    arguments. 0x140e47d00(dialog, id) sets a dialog's message.
  - Tables are loaded per language by 0x141498010 → "common/string/" or "<lang>/string/" + "all.str" /
    "sys_<lang>.str".
- `\x11` + 2 characters are macros the text engine expands at runtime. Examples: `{rf}` in "…the {rf} server"
  and `{re}` in "Please reboot {re}"; competition names are `{na}`, `{nd}`; buttons are `{ja}`, `{ll}`; layout
  is `{i0}`, `{i1}`.
  - I did not find the expansion table.
  - `{re}`/`{rf}` are almost certainly the game title. The exe picks "eFootball PES 2021 SEASON UPDATE" or the
    Winning Eleven title by region (0x1449eb9e0). [guess]

## 2. How a server error reaches the screen (CommandObject base)
- Each command object keeps the reply `result` string at +0x78, the msgid string ("CMD_…") at +0xa0 and an
  error flag at +0x98. [static 0x1411667f0]
- The **auto-error-dialog** flag is at +0x74. The base constructor sets it to **1 by default**. [static 0x1411661a9]
- Base state machine 0x141166300 [static]:
  - On a failed reply with +0x74 set, it records "cmd:err" (0x14114ec40).
  - Then it calls `CommandErrDialogManager::Push` (trampoline 0x1411cae60 → 0x1447187a0, in the protected
    section). Push looks up msgid = **0x141166780(cmd, err)**, then queues it (0x1411cad30 → 0x144718020).
  - It waits for the dialog to close (state 7), then finishes (state 8).
  - With +0x74 = 0 it goes straight to state 8: the screen handles the error itself.
- 0x141166780 scans the **server error table at 0x1428a1170**: 24-byte rows `{char* cmd, char* err, u32 msgid}`,
  764 rows, ending at a NULL cmd pointer.
  - On a miss it uses a **generic per-ERR table at 0x1428accb0** (16-byte rows {err, msgid}, 11 ERR_CLIENT_*
    rows). All of those give 0, except ERR_CLIENT_SENDFAIL = 0xffffffff. Anything else gives 0. [static 0x1446cde20]
- Display rules (`CommandErrDialogManager` vtable slot 0, 0x1411ca5c0; dialog "CommandErrDialog") [static]:
  - **msgid = 0xffffffff → no dialog** ("silent"). The calling screen decides what to do.
  - **msgid = 0 → generic text 0x01fb00d2.** The dialog is shown only if one of three online-state checks passes
    (0x14114e7e0(mgr, 0/1/2)); otherwise there is no dialog.
  - **msgid = 0x01fb00d2** (explicit or from the 0 rule): text + `"\nCODE:"` + code, where:
    - code = `TAG(cmd)_%04d(ERR enum index)`. TAG is the 4-letter hash (same as errcode.py) of the **command
      name string**, e.g. "CMD_SET_CURRENTPLAYER" → CKRF.
    - The ERR index comes from the 189-entry enum (0x1437f3ee0, filled by 0x14021fa30; see err_enum.json).
    - If the ERR is not in the enum, the code is `TAG0` (e.g. ERR_INSUFFICIENT_POINT → "GAOZ0").
    - If the command has an extra int (+0xc0), `_%d` is appended to the code.
    - The player sees: **"An unexpected problem has occurred while communicating with the {rf} server.
      CODE:CKRF_0096"**.
  - **Any other msgid → that text only, with no CODE line.**
- Classes that **turn the auto dialog off** (+0x74 = 0 in their constructors; autodialog_flag.json) [static]:
  - LEAVE_ROOM(_FORCE), CANCEL_MATCHING, JOIN_ROOM_NTL (Free and LobbyMulti), GET_GAMERELAY_FOR_MATCHING.
  - CHECK/SET_LOCAL_GAMERELAY*, SET_PLAYDATA_MATCH, SEND_TAMPER_DATA, REQ_GAMEADDPOINT,
    REQWATCHL_BLOCK_MATCHLIST.
  - All SEND/WATCH_SYNC/SYSTEM_NOTICE, SEND_HEARTBEAT, CHECK_STRING, the TURN/punching commands.
  - The coop matching commands and the clan accept/reject/cancel/confirm commands.
  - The matchday pickup commands and GET_ONLINE_AD_FILE*.
  - Their table text appears only where the screen shows it itself. Example: the JoinRoomNtl result handler
    0x1410f30a0 calls 0x1411667f0 → 0x140ff0b70 and opens "err_dlg" / "err_pass" / "err_dlg_noroom".
- Summary of the 764 rows: **492 have a specific message, 163 show the generic text + CODE, 109 are silent.**
  The most common text: "An error occurred while accessing the {rf} database." (202 rows, mostly ERR_DATABASE).

## 3. On-screen error codes (error reporter 0x1411546b0)
- Signature: 0x1411546b0(reporter, extra, file, line, code). It stores the record {extra, basename(file), line,
  code} (0x141154e20).
- The display string is built by 0x141154020 [static]:
  - `code >= 0` → `TAG_%04d`, e.g. "MXNV_0013".
  - `code < 0` → `TAG` + line, e.g. "EYRH763".
  - Then `_` + extra if extra is not empty, e.g. "GNCX_0003_0x20000000". This matches our logs.
- **So most sites show TAG_<sub-code>, not TAG+line:**
  - CCWR450 is shown as **CCWR_0014**, CCWR211 as **CCWR_0003**, MXNV476 as **MXNV_0013**.
  - The CMD_LOGIN failures in OnlineLoginTaskConnectAccount.cpp (tag QIMJ) show as QIMJ_0014..0017.
  - onscreen_codes.tsv lists all 178 parsed sites.
- Dialog texts that carry a code (all built as text + `"\nCODE:"` + last code via 0x141154470):
  - `OnlineObserveErrorDialog` (0x140bebe00): **0x01fb00d2** "An unexpected problem has occurred while
    communicating with the {rf} server." + "\nCODE:" + code.
  - Login `TaskError` (0x140fe69e0) and live update (LiveDataSetFlow 0x1420ae939): kind text +
    0x14116f6a0 (adds "\nCODE:<code>" when a code exists).
  - `OnlineErrorDialogFromParameter` (0x140bf26b0) shows the online error manager's text (mgr 0x1436fced8 +0x148).
    - The default is 0x01fb00d2. It is set by 0x140ff0b70(msgid) and reset after display.
    - Setters include 0x2c0018 "Your connection attempt timed out.", 0x20f0071 "Unable to send or receive data.",
      0x1fa006f "This match room no longer exists.", 0x4640031 "The Match Room was closed because the Owner
      left.", 0x20f00a1 "Unable to connect.", 0x20f009e "Unable to obtain relevant data.".
    - Table 0x141396160 maps {0→0x1f50013, 1→0x20f003a, 2/3→0x1fb00d2}.

## 4. Login errors (ConnectMenu2 / ConnectAccount → login-error "kind")
- The kind is at [errmgr+8]+0x58. The text switch is in 0x140fe69e0 (jump table 0x140fe6f1c) [static]:

| kind | set by | text the player sees (+ "\nCODE:…") |
|---|---|---|
| 2 | gate/server-list failures (0x140fdb320, 0x140fdd860, GET_SVR_LIST …) | 0x2080005 "The online service is currently unavailable. The server may be undergoing maintenance or you may be using incorrect network settings. … {re} Official Website https://www.konami.com/wepes/2020/" |
| 5, 6 | network test | 0x2c0079 + UDP port lines 0xce0141/0xce0142 + 0x2c007a/0x2c007c/0x2c007d (router advice) |
| 7 | | 0x20f00b5 "Unable to connect to the {rf} server.\nPlease check the configuration of your network device." |
| 13 | | 0x2c0078 "Please make sure your network settings are configured correctly." |
| 14 | NAT restriction | 0x20f0075 + 0x20f0061 + 0x2c007d |
| 15 | | 0x20f00b6 "The necessary login data could not be acquired from the {rf} server." |
| 19 (0x13) | AUTH_STEAM fail, LOGIN ERR_LOGIN_FAILED (QIMJ_0014) | 0x20f00b8 "Unable to login. Account verification failed." |
| 21 (0x15) | JOIN_ONLINE_MODE ERR_MODE_BLOCKED (CCWR_0003) | 0x1fa00e9 "\"%s\" is currently unavailable online.\nPlease refer to Online Information for further details." %s = mode name by game mode (0x140fe6f90): Quick Match, Online Divisions, myClub, eFootball League, Matchday, Online Co-op, Friendly Match Lobby, Team Play Lobby, … |
| 22 (0x16) | CHECK_CHEAT ERR_PARAMCHECK_FAILED (CCWR_0014) | 0x20f00b9 "Unable to load content data.\nThe data may be corrupted, or conditions for use may not have been met." |
| 23 (0x17) | SET_MYCLUB_VSCOM_GAMERESULT ERR_DIFFPLAYER_SEND | 0x2260216 "The account has changed. Returning to Top Menu." |
| 24, 25 | | 0x20f007b "Unable to obtain relevant list." |
| 4, 8–12, 18, >25 | | 0x1fb003e "An error has occurred." |
| 0, 1, 3, 16, 17, 20 | 3 = "other error"; 16 = LOGIN ERR_DIFFERENT_PATCHVERSION; 20 = ERR_SERVERNOTREADY | no TaskError text; another dialog (the CommandErrDialog table text, e.g. LOGIN ERR_SERVERNOTREADY → 0x2080005) [inferred] |

- CMD_LOGIN's own table rows are below. LOGIN keeps the auto dialog, so these texts are shown [static table;
  display inferred]:
  - ERR_LOGIN_FAILED → 0x20f00b8.
  - ERR_INVALID_ACCOUNT → 0x20f00a2 "…we have suspended your account in line with our policy on violations."
  - ERR_FREE_TO_PLAY_FULL → 0x1f5003f "Due to excessive traffic on the {re} server, login is currently restricted."
  - ERR_SERVERNOTREADY → 0x2080005.
  - ERR_ALREADYLOGGEDIN → 0x20f0001 "Account verification failed.\nPlease try to login again at a later time."
  - ERR_DIFFERENT_PATCHVERSION / ERR_DEFCLIENTVER → 0x2080068 "Your version of {re} is out of date. Please update…".
  - ERR_DIFFDCVERSION → 0x20f000e.
  - ERR_DIFFERENT_LIVEDATA_VERSION → 0xf90046 (a Condition Rating help text; looks like a Konami table bug).
  - ERR_LOBBYUSERMAX → silent.
- **CHECK_CHEAT ERR_PARAMCHECK_FAILED** shows the table text 0x20f0015 **"Invalid player data found."** (code
  SXCA_0034 is not displayed, because the text is specific). The kind-0x16 TaskError adds "Unable to load content
  data… CODE:CCWR_0014". Two dialogs are possible. [static; order not verified]

## 5. Other message ids that were asked for
- Courtesy rating (GET_MANNER_WARN warn_kind):
  - DOWN_B → 0x20100c0 "The number of times you have failed to properly complete matches is increasing…".
  - A_TO_B → 0x20100c1 "Your courtesy rating has fallen to \"B\"…".
  - DOWN_C → 0x20100c2 "The number of times you have failed to properly complete matches remains high…".
  - STILL_C → 0x20100c3 "Your courtesy rating has fallen to \"C\"…". (Live 2026-11-07; the names don't match
    the texts, see menus.md.)
- Live update (LiveDataSetFlow):
  - 0xf90042 "Live Update in progress."
  - 0xf90043 "Unable to implement Live Update Data."
  - 0xf90056 "New data asset download unsuccessful."
  - 0xf9004f "New data asset download unsuccessful. Some player photos were not updated."
  - 0x20f0070 "Data Pack Incompatible."
  - The error text switch 0x1420ac100(code at +0x1f8), each followed by "\nCODE:":
    - 1 → 0x20f0012 "Unable to login."
    - 2/6/default → 0x2c0039 "Communication error" (download failed)
    - 3 → 0xce0064 "The downloaded file is broken."
    - 4/5 → 0xce0060 "Install failed." (save/load)
    - 7 (NOT_DELIVERED) → 0xf90047 "No new Live Update available."
    - 8 (DP version old) → 0x20f000e "Data Pack Incompatible.\nPlease reboot {re} and update the software."
- Maintenance warning: 0x1fa00e8 "We will be undertaking maintenance work on our servers starting at %d:%02d
  today.\nAll online services … will be suspended during maintenance.\nAre you sure that you want to start a match
  now?" (formatted with hour:min, 0x14139152c).
- **"Failed to connect with opponent." = 0x045b0010** (group Xbl7T, but used on PC).
  - It is shown by OnlineModeTaskLobbyRoomMain fn 0x1410438a0 in "DialogReturn" when room+0x1d0 == 1.
  - Any other non-zero value → 0x1fb003e "An error has occurred." [static]

## 6. What the server can use for better UX
- **Ban:** reply `ERR_INVALID_ACCOUNT` to CMD_LOGIN. The player gets the account-suspended text, with no code.
  - `ERR_FORBIDDEN` on CMD_SEND_AUTHORIZATION_CODE / CMD_SEND_PSID gives "The system you are using to connect has
    been denied access due to a violation of the Terms of Use…".
- **Server full / throttling:** `ERR_FREE_TO_PLAY_FULL` to CMD_LOGIN → "Due to excessive traffic… login is
  currently restricted. Please try reconnecting again later."
- **Maintenance:** `ERR_SERVERNOTREADY` to CMD_LOGIN → the "online service is currently unavailable … maintenance"
  text. It contains Konami's URL; we can only change that with a string mod.
- **Version gate:** use CMD_JOIN_ONLINE_MODE.
  - `ERR_DIFFERENT_PATCHVERSION` → "Your version of {re} is out of date…"
  - `ERR_DIFFERENT_LIVEDATA_VERSION` → "Unable to log in while using outdated Live Update data."
  - `ERR_DIFFERENT_EXTRADATA_VERSION` → "…outdated Asset Data."
  - `ERR_DIFFDCVERSION` → "Data Pack Incompatible. Please reboot…"
  - Prefer these over LOGIN, whose LIVEDATA row points to the wrong string.
- **Close a mode:** JOIN_ONLINE_MODE `ERR_MODE_BLOCKED` → "\"Quick Match\" is currently unavailable online…"
  (+CODE:CCWR_0003). For matchmaking only: START_MATCHING `ERR_COMMAND_BLOCKED` → "This feature is currently
  unavailable. Please refer to Online Information…".
- **Anti-cheat refuse:** CHECK_CHEAT `ERR_PARAMCHECK_FAILED` → "Invalid player data found." (plus the CCWR_0014
  dialog).
- **Rooms:**
  - ERR_ROOMISFULL / ERR_TOOMANYMEMBERS → "Cannot participate due to full quota."
  - ERR_PASSWDINCORRECT → "Incorrect password."
  - ERR_ROOMSTATNOTMATCH → "Could not enter because a match is currently being played."
  - ERR_RATING_NOTMET → the rating restriction text.
  - ERR_JOINKICKEDROOM (kicked) has **no friendly text** (generic + ZVMY_0079).
- **Courtesy:** GET_MANNER_WARN DOWN_B/A_TO_B/DOWN_C/STILL_C show warnings; JOIN_COMPEV `ERR_COMPE_JOIN_MANNER`
  → "You cannot take part in competitions as you have a \"C\" courtesy rating."
- **Avoid:** `ERR_REFUSED` / `ERR_NOPLAYER` on SET_CURRENTPLAYER, `ERR_INVALID_TICKET` on AUTH_STEAM, and any ERR
  missing from a command's rows. They all show the generic "unexpected problem … CODE:XXXX_nnnn". That is still
  useful for support: the admin page can decode CODE = hash(cmd) + ERR index (see server_errors.tsv
  `code_if_generic`).
- **Silent rows** (msgid -1, e.g. JOIN_ONLINE_MODE ERR_MODE_BLOCKED, LEAVE_ROOM, SET_GAMEENV, GET_MATCHID
  ERR_DATABASE) show nothing at command level. The task logic decides.

## Still unknown
- Expansion of the `{rf}`/`{re}` and other `\x11` macros (table not found). A screenshot of any dialog
  containing them settles it.
- The meaning of the three 0x14114e7e0 checks that gate the generic dialog for msgid 0.
- For CMD_LOGIN (auto dialog on) together with the ConnectAccount kind dialog: whether both appear, and in what
  order. Test: reply ERR_INVALID_ACCOUNT once and photograph.
- 0x1410f8010 and 0x141070880 set +0x74 both ways (conditional). Not resolved which commands they are.
- Display of error-reporter sites whose sub-code is not a constant (marked `TAG_????|TAGline` in
  onscreen_codes.tsv).
