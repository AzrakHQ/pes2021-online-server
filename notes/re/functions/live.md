# Live Update / downloadable data (PES 2021 PC 1.01.00), static RE

## Corrections from later findings (2026-11-05)
- **Team data in a Live Update is applied** (live 2026-11-06): Team.bin short name "BAR"→"BRX" showed on the scoreboard, and PlayerAssignment.bin changes moved `entry_check_sum` to exactly the pesdb.py prediction.
- **Keep listing the update** (live 2026-11-06): the game asks for CMD_GET_DLC_FILELIST again when it enters an online mode; if that list is empty it computes CHECK_CHEAT from the base data, although it still reports `livedata_version`. With the update listed every time, the sums match pesdb.py exactly.
- dbgaps.md §5: after binding, all 28 pesdb files are reloaded (Team.bin too); PlayerWeekly matters only in live mode 1; ExtraUpdate.bin is `.\download\ExtraUpdate.bin`; save type index = slot − 10.
- Settled live (livetests.md): the Live Update slot is `C:\ProgramData\KONAMI\eFootball PES 2021 SEASON UPDATE\save\LIVE00000000` (encrypted, the CPK plus 990 bytes); `JOIN_ONLINE_MODE livedata_version` becomes the first field of version_wu.bin ("1.01"); the recipe in §5 works as written.
- Settled statically (sweep.md): the server DLC version in state 4 is GET_SVRVERSION `dlc_version`; version_wu.bin fields 2 and 3 are never read; ERR_DIFFERENT_* replies to JOIN_ONLINE_MODE give only the generic login error, so use ERR_MODE_BLOCKED.


Supporting disassembly dumps are not published; general tools are in `notes/re/functions/tools/`.

Every address below is a VA in PES2021.exe.

Tags:
- **[static]**: read in code.
- **[log]**: seen in our traffic.
- **[inferred]**: reasoned from strings or structure.
- **[guess]**: unverified assumption.

## How the system works

### Three kinds of downloadable data

All three are **CRI CPK archives**. The game binds them on top of the base data, the same way as a data pack.

| kind (`[flow+0x9c]`) | reply list | savedata slot (name) | CPK bind priority | version file in the CPK | version-table idx |
|---|---|---|---|---|---|
| 1 Live Update | `dlcfile_list[0]` | 0x1b "Live Update" | 5 | `common/etc/version/version_wu.bin` | 1 |
| 2 Variation i (0..15) | `variation_file_list[i]` | 0x1c+i "Variation Data00..15" | 5 | `version_vd00..15.bin` | 3+i |
| 0 Extra Update | `extra_file_list[0]` | 0x2c "ExtraUpdate.bin" (a file) | 6 | `version_xu.bin` | 0x13 |

Other entries of the version table:
- idx 0 = program version "1.01.00" [static 0x1434fcda8].
- idx 2 = the data pack version, from `version_dp.bin` in dt80.

Evidence:
- The slot-name table is at 0x1413f9420 [static].
- Bind: `LiveDataHandler` load (0x1413f8e10) calls 0x1413f8b50(path, 5), which calls 0x1415a2544. That function has the signature of criFsBinder_BindCpk(binder, 0, path, 0, 0, &id). Priority is then set by 0x1415a3834. `ExtraUpdateHandler` load (0x1413f8c90) does the same with priority 6 [static].
- The version files are read by 0x1414a0d00 from `cpk_dat/common/etc/version/` + the name table at 0x1434fcdb0 [static].

### Driver and transport

- **Driver:** `livedata::LiveDataSetFlow` in DB/LiveData/LiveDataSetFlow.cpp (on-screen error prefix **SGJC**). Update function 0x1420ac3f0; constructor 0x1420ab7c0.
- **Who creates it:**
  - `onlinelogin::TaskApplyLiveUpdate`: 0x140fdf850 calls 0x140fdf9d0, which picks a variant by game-mode id (0x14149f4f0): "LiveDataSetFlow", "…OnMode", "…Exhibition", "…OffMode" or "LiveDataSetEditData".
  - Edit mode: `process::ProcessEditLiveUpdate` (0x1413ab230). It runs the flow, then "editSaveData", so the result is written into the edit (option) file. `ProcessEditLiveUpdateLoginFunctor` (0x1413aa9e0) then sends CmdLeaveOnlineMode [static, strings].
- **When it runs:** our logs show one GET_DLC_FILELIST at every login and one more when a player enters an online mode, shortly before JOIN_ONLINE_MODE. CHECK_CHEAT comes after that [log].
- **On the wire:**
  1. One HTTP web-API call, CMD_GET_DLC_FILELIST, wrapped by `command::CommandObjectCmdGetLivedataList` (created by 0x1421a8990; the result is copied out by 0x1421a8a70).
  2. For each file to fetch: a plain WinHTTP GET of the entry's `url`.
  3. A SHA-256 check against `hash`.
  4. The bytes are saved unchanged and bound as a CPK.
- **Integrity:** the SHA-256 value supplied in the reply is the only check in this flow. No signature or key appears on this path [static].

## 1. LiveDataSetFlow state machine

Function 0x1420ac3f0. The state is `[obj+0x94]`; the jump table is at 0x1420aecc0.

**Object fields**

| offset | meaning |
|---|---|
| +0x90 | mode |
| +0x98 | previous state |
| +0x9c | kind |
| +0xa0 | variation index |
| +0xa4 | download counter |
| +0xa8 | command object |
| +0xb0 | live-data entry |
| +0x138 | vector of variation entries (0x88 bytes each) |
| +0x150 | extra entry |
| +0x1e0 | TaskLivedataDownload |
| +0x1e8 | UtilHttpDownloadFile |
| +0x1f0 | download buffer |
| +0x1f8 | error code |
| +0x1fc | a variation failed |
| +0x1fd | live data is new |
| +0x1fe | there are variations |
| +0x1ff | extra is new |
| +0x248 | UtilHashTask |
| +0x250 | ProgressBarDialog |
| +0x268 / +0x270 / +0x278 | savedata tasks |

**Savedata task helpers:**
- 0x1413f8ec0(mgr, slot) = load + bind (handler vtable +0x10).
- 0x1413f9f60(mgr, slot) = unbind (handler vtable +0x18).
- 0x1413f9d10(mgr, slot, buf, size) = save.

All three are static, resolved through the task vtables 0x142942a68, 0x142942aa0 and 0x142942ad8.

**Error codes.** SGJC = file hash; the number is the source line. The error reporter is 0x1411546b0.

**States**

| st | action |
|---|---|
| 0–3 | Bind the saved Live Update (slot 0x1b). Copy the version-table entry 1 into system data (`[sys+0xe9fee0]` int, `+0xe9fee8` string) via 0x140404880. Unbind it again. → 4. The flow first measures the version it already has. |
| 4 | **Data pack version gate.** `my` = version table[2] (version_dp.bin: "1.00" → 100). `svr` = 0x1411c79c0: the string at `[netmgr+0x68]`, parsed as `a*100+bb`. Inferred to be GET_SVRVERSION `dlc_version`, the only reply field of that name. If `my < svr` → "DLC VERSION my[%u] svr[%u] … old", ERROR_CODE_DP_VERSION_OLD, **SGJC392** (code 1). If mode = 1, it ends silently instead. Otherwise → 5; in mode 2, with data already applied this session → 0x11. |
| 5 | Create "CmdGetLivedataList". If creation fails → ERROR_CODE_DOWNLOAD_FAILED, **SGJC432**. → 6 |
| 6 | Poll the command. On failure → "CmdGetLivedataList failed", **SGJC573** (code 4, error state 0x20). If the result is NULL → "LIVEDATA_ERR_CMD_RES_NULL", **SGJC474**. On success, copy the lists (see §2), sort the variations by `name` (comparator 0x1420aede0), compute the three "new" flags, build the progress-dialog text, set the progress total to 10000 × the number of files. → 7. If the extra is not new, kind starts at 1. |
| 7 | Choose the next file (kind 0 → 1 → 2). Kind 1 not new: if `[sys+0xe9fee0]` ≠ 0 **and** `atoi(entry.dlc_version)` ≠ 0, keep the current data and move to the variations. Otherwise → **ERROR_CODE_NOT_DELIVERED** (error code 7; no reporter call; error state 0x1e). Kind 2: skip variations that are not new; when all are done → 0x11 (or 0x20 if one failed). If the progress dialog is missing → **SGJC649**. Otherwise allocate a buffer of `entry.size` bytes, log "[LiveDataSetFlow] Download %s (%d)" (url, size), then: kind 0 → `onlinesystem::UtilHttpDownloadFile(url, size, "ExtraUpdate.bin")`; kind 1/2 → `TaskLivedataDownload` 0x1421a9570(url, buf, size). If starting fails → **SGJC729** (code 6). → 8 |
| 8 | Progress (0..100). On error → "!!! error (%s) !!!", **SGJC809** (code 7, carries the HTTP status/errno). When kind 0 finishes, start an unbind of slot 0x2c. → 9 |
| 9 | Kind 0: when the unbind finishes, UtilHttpDownloadFile verifies the hash itself: 0x1421acbf0(extra.hash) → UtilFileHashTask. Kind 1/2: start `UtilHashTask(buf, size, type 0)` (0x141219530). If it cannot be created → ERROR_FILE_BROKEN, **SGJC880** (code 8). → 10 |
| 10 | Hash progress. Hash-task error → ERROR_FILE_BROKEN, **SGJC963** (code 10). Kind 0 → 13; kind 1/2: compare digest with `entry.hash`. Mismatch → "!!! wrong hash !!!", **SGJC1006** (code 12). → 11 |
| 11–12 | Save the raw buffer to slot 0x1b (kind 1) or slot 0x1c+i (kind 2, table 0x142bd2460) with 0x1413f9d10. Log "save succeeded" or "save failed (%d)" → ERROR_CODE_SAVE_FAILED, **SGJC1102** (code 14). → 13 |
| 13–14 | Kind 0: bind slot 0x2c, then check that version_xu's "required DP" (field +0x14) ≤ DP version **and** has the same major (÷100). On mismatch → unbind and report at line 1200 through 0x141154560 (code SGJC1200 [inferred]). Kind 1: if there are variations → kind 2; otherwise → 0x11. Kind 2: bind the variation slot; on failure → "variation%d bind failed", ERROR_CODE_LOAD_FAILED, **SGJC1222**. Next variation. |
| 0x11–0x12 | Bind slot 0x1b (the Live Update). On failure → "bind failed", ERROR_CODE_LOAD_FAILED, **SGJC1306**. On success → `[sys+0xe9fee0]` = version-table entry 1 (from version_wu.bin inside the CPK). |
| 0x13–0x19 | Close the progress dialog. Reload the version table (0x1414a1100 / 0x1414a0d00 loop). "LiveDataSetDialog" (message 0xf90042). Save system data (0x1414b7290 / 0x1414b7560). Short waits (≥ 0x7d0 ms) for the dialog. |
| 0x1a–0x1d | Unless the edit mode equals 2, reload the edit data: "editLoadDataInLiveDataSet" (0x141efb1f0), so that option-file edits stay on top. Optionally show the "liveDataDetSummary" featured-player screen (`livedata::LiveDataMenuSummary`, which uses CmdGetMyclubFeaturePlayerList). |
| 0x1e–0x1f | Error dialog "DialogProceed" (text from 0x1420ac100(code)). Sends **CMD_SEND_LOG** `{title:"LiveUpdate", log:"NotImplement"}` (0x1420ac0b0) [log]. Code 7 (NOT_DELIVERED) is shown once per session: flag `[0x1437f9648]+0xc`, and in mode 1 it is skipped the second time. |
| 0x20–0x26 | "LiveDataErrorDialog" (message 0xf90043 live data, 0xf90056 extra, 0xf9004f variation). Unbind the failed slot. "LiveDataVersionError" (0x20f0070). Finish (0x26). |

**Timeouts**
- HTTP GET: one overall limit of **120 000 ms**, measured from the start of the request. `HttpsTransaction+0xc` = 0x1d4c0 (set at 0x1421a9626); the start tick is stored at 0x141170f7e and checked at 0x141170480 ("[HTTP] %d msec. timeout") [static].
- The flow itself has no command timeout of its own; it relies on the generic command layer.

## 2. CMD_GET_DLC_FILELIST

**Transport:** HTTP web API to urltype FLOW. Form-urlencoded POST.

**Request:**
```
msgid=CMD_GET_DLC_FILELIST&rqid=0&urltype=FLOW&file_type=LIVEDATA&region=REGION_ALL&pid=0
```
[log]

- `file_type` enum (0x140221d40): CPK / TORRENT / LIVEDATA. Only LIVEDATA is ever requested.
- "CmdGetDlcFilelist" is registered in the factory (pointer at 0x1434d33c0), but no code creates it by name. Only the CmdGetLivedataList wrapper does [static].

**Reply** (parser 0x141d34a90; Result offsets relative to the Result object):

| field | type | Result offset | use |
|---|---|---|---|
| result, msgid, rqid | STR/num | — | standard |
| dlcfile_list_count | NUM | +0x1d0 | stored but **not used**: the extractor iterates the real list length |
| dlcfile_list | array of **inline maps** | count +0x1d8, ptr +0x1e0 | **only element [0] is used** (Live Update) |
| variation_file_list_count | NUM | +0x1e8 | not used |
| variation_file_list | array of inline maps | +0x1f0 / +0x1f8 | all elements; sorted by name; at most 16 (the slot table 0x142bd2460 has 16 entries) |
| extra_file_list_count | NUM | +0x200 | **used as the count**: if it is ≥ 1, element [0] at +0x210 is read |
| extra_file_list | array of inline maps | +0x208 / +0x210 | element [0] |

There is **no top-level `dlc_version`**: the parser has no such key. Our current reply sends one; it is ignored [static].

**Each element (inline map)** is parsed into a 0x90-byte `DlcFile`, then copied to a 0x88-byte entry by 0x1421a8a70:

| key | type | entry offset | notes |
|---|---|---|---|
| `name` | STR | +0x20 | backslashes stripped; must be non-empty for variations |
| `size` | NUM | +0x80 | positive int (msgpack int or double); buffer size and the exact expected byte count |
| `url` | STR | +0x40 | backslashes stripped; must be non-empty for variations |
| `hash` | STR | +0x60 | 64 lowercase hex characters (SHA-256) |
| `dlc_version` | STR | +0x00 | **integer string**, compared with `atoi` |
| `required_program_version` | NUM | (DlcFile +0x88) | parsed but **not copied or used** |

**Shape rules** (from how the parser reads the data):
- List elements are read **as maps without a type check**: `[elem+8]` is the entry count and `[elem+0x10]` the 0x30-byte entries (0x141d3509d). So every element must be a real msgpack map. A nested msgpack blob (bin), which our `blobs()` helper produces for other commands, would be walked as if it were a map [static].
- String fields are read as strings, so send `name`, `url`, `hash` and `dlc_version` as msgpack str.
- An empty list (or an empty string, as today) is harmless: the loop runs 0 times.
- `extra_file_list_count ≥ 1` with an empty `extra_file_list` would make the client read element [0] of an empty list. Keep the count and the list consistent.

**How the client decides "new"** (0x1420ac240, 0x1420ac1e0, 0x1420ac280):
- **Live Update:** `atoi(dlcfile_list[0].dlc_version) > [sys+0xe9fee0]`. The right-hand side is the integer of the version_wu.bin currently in the saved CPK; it is 0 when there is no live data.
- **Variation i:** `name`, `url` and `hash` are all non-empty **and** `atoi(dlc_version) > version-table[3+i]`.
- **Extra:** `atoi(dlc_version) > version-table[0x13]` **and** `size > 0`.

**Version string format** (0x1414a0a60 for files, 0x1411c79c0 for the server value):
- "a.bb.cc" → a·10000 + bb·100 + cc
- "a.bb" → a·100 + bb
- "a" → a

Examples: version_dp.bin "1.00" → 100, so GET_SVRVERSION `dlc_version` "1.00" passes the state-4 gate; any value ≤ 100 passes. Entry `dlc_version` values use `atoi` only, so they must be plain integers on the same scale, e.g. "101" for a version_wu.bin of "1.01".

**What our server sends today, and the result:**
```
{"result":"NOERR","dlc_version":"1.00","dlcfile_list_count":0,"dlcfile_list":"",
 "variation_file_list_count":0,"variation_file_list":""}
```
- Kind 1 is not new and the client has no live data (version 0) → NOT_DELIVERED (code 7).
- One "No new Live Update" dialog per session, plus CMD_SEND_LOG "LiveUpdate". Login then continues [log + static].
- Equivalent, tidier form: `{"result":"NOERR","dlcfile_list_count":0,"dlcfile_list":[],"variation_file_list_count":0,"variation_file_list":[],"extra_file_list_count":0,"extra_file_list":[]}`.

## 3. Download, storage, integrity

### Kind 1/2 (Live Update / Variation)

**Transfer**
- `onlinemode::TaskLivedataDownload` (vtable 0x142c007a0) uses `onlinesystem::HttpsTransaction` (WinHTTP; ctor 0x14116f910; Request 0x141170a30; send 0x141170e90).
- Verb: **GET**, because no post data is passed ("POST" only when post data is non-null, 0x14117112f).
- `WINHTTP_FLAG_SECURE` only when the URL scheme is "https" (0x1411710e8). Plain `http://` works, as it already does for our web API.

**Response handling** [static, 0x1411706e4..0x1411708ba]
- The status must be **200**.
- The `Content-Length` header is read.
- The body is read into the `size`-byte buffer:
  - Content-Length > `size` → error ("full receive buffer").
  - Content-Length < `size` → the remainder is zero-filled, so the hash would not match.
  - So **`size` must equal the file length exactly.**

**Limit:** 120 s for the whole transfer. `size` is a u32.

**Hash (SHA-256)**
- `UtilHashTask` with hash type 0 = `onlinesystem::Sha256Impl` (vtable 0x1428b0748; standard IV 0x6a09e667… at 0x1415d19cb). Type 1 would be Md5Impl.
- Input: exactly the `size` downloaded bytes, fed in 256 KiB blocks (0x14121a5d0).
- Digest text: ostringstream, `hex`, `setw(2)`, `setfill('0')` over 32 bytes (0x141219fa0). That gives **64 lowercase hex characters**; the uppercase flag is not set (manipulator 0x14040c310).
- Comparison: exact, case-sensitive string compare with the entry `hash` (0x140cf3990 at 0x1420adbd6).

**Storage**
- The **raw bytes** go to savedata slot 0x1b "Live Update" (or 0x1c+i), through the platform save service (0x1413f7390; vtable +8 = write).
- Afterwards `LiveDataHandler` binds that saved file **by path** as a CPK (0x1413f8e10 → 0x1413f8b50). The bytes are therefore kept in CPK form [inferred from bind-by-path].
- The save root on this PC is `Documents/KONAMI/eFootball PES 2021 SEASON UPDATE/<steamid>/save/`; no live-update file exists there yet. The exact file name is unknown.

### Kind 0 (Extra)

- `onlinesystem::UtilHttpDownloadFile` (ctor 0x1421abda0) downloads to `<dir type 3>/ExtraUpdate.bin.unverified`.
- It SHA-256 hashes the file (`UtilFileHashTask`, type 0), compares with `extra.hash`, then replaces `ExtraUpdate.bin` (0x1421ac2d0).
- Directory type 3 comes from a protected thunk (0x1414af5e0). It is probably `.\download\` next to the exe, the folder the game creates at 0x140405360 and where dt80 lives [inferred].
- Unbinding the extra also unloads `Asset/model/bg/common_dt89.cpk` (0x1413f9eb0). So the extra CPK can carry an embedded asset CPK [inferred].

## 4. Data format and effect

**Container:** a CRI CPK with paths relative to the `cpk_dat/` root, like dt80. Our own `tools/pesmod/cpkwrite.py` and `cpkread.py` handle this format.

**Version files** (30 bytes, three NUL-padded 10-byte ASCII fields; read by 0x1414a0d00):
- version_dp.bin from dt80 = `"1.00" | "1.01.00" | "2396"` [static, extracted].
  - +0x00: content version → table +0x10 (the value the client compares).
  - +0x0a: required program version → table +0x2e0.
  - +0x14: build string → table +0x880.
- version_xu.bin (extra): field +0x14 = required DP version → table +0x5b0, checked at 0x1420ae026 and at boot (0x140404449, 0x1404068e6); field +0x1e → table +0x880. So that file is 40 bytes [static].
- version_wu.bin: same 30-byte layout as dp [inferred].
  - **It must be present in a Live Update CPK.** Without it, version-table entry 1 stays 0, so `[sys+0xe9fee0]` stays 0 and the client downloads again at every login [static logic].

**Tables a Live Update CPK is expected to carry**
- The livedata module's name table at 0x143530c00 (strings at 0x142bd0ec0…) lists: PlayerWeekly, Tactics, TacticsFormation, PlayerAssignment, Player, PlayerAppearance, BootsList, PlayerDeleteList, Coach, CoachDeleteList, TeamWeekly. They sit under `common/etc/pesdb/` (`cpk_dat/common/etc/pesdb` string at 0x1428baf40).
- Format: WESYS+zlib, the same as dt80; the db group documents it.
- Sizes in dt80: Player.bin 1.27 MB, PlayerAssignment 138 KB, PlayerWeekly 445 B, TeamWeekly 1.2 KB. A full roster update is about 1.5 MB, well inside the 120 s limit.
- How it is applied: bind at priority 5 overrides files by path; `livedata::SetLivePlayerDataThread` and LiveDataManager then load these tables into the DB [inferred].

**Persistence**
- The CPK stays saved in its slot across restarts. The client keeps it until a higher version replaces it; NOT_DELIVERED does not unbind or remove it (it only goes to the error dialog) [static].
- Boot sequence 0x14040410e:
  - Binds 0x1b, records its version, then unbinds it (states 0xe → 0x10).
  - Binds variations 0x1c..0x2b and keeps them.
  - Binds ExtraUpdate.bin and keeps it unless the xu/dp check fails [static].
- So the Live Update is **applied per session by LiveDataSetFlow** (online login / modes, or the offline "Live Update" setting). Variations and Extra are active from boot.

**Effect on the integrity sums**
- CHECK_CHEAT is sent after JOIN_ONLINE_MODE, i.e. after the flow has bound the live data [log order].
- `param_check_sum` sums the player abilities in the DB; `entry_check_sum` sums the rosters (menus group).
- The db group found that in live-data mode 1 `param_check_sum` also includes PlayerWeekly condition.
- Therefore every client that applied the **same** Live Update CPK produces the same sums. A client without it, or with an older one, produces different sums and gets "DATAPACK error" in the P2P revision check.
- Clients report their live-data version in LOGIN / JOIN_ONLINE_MODE `livedata_version` (today "0") and `extradata_version` ("0") [log]. Source = the version-table strings [inferred]. A server can compare these.
- The result enum also contains `ERR_DIFFERENT_LIVEDATA_VERSION` (0x142ad5128, table 0x14021fa30). How the client displays it is unverified; `ERR_MODE_BLOCKED` on JOIN_ONLINE_MODE is a known clean refusal (menus group).

## 5. How our server would publish an update (the game's expected inputs)

1. **Build a CPK** containing:
   - `common/etc/version/version_wu.bin`: 30 bytes, `"<ver>"` / `"1.01.00"` / a build string, each NUL-padded to 10 bytes. Example: `1.01`.
   - The changed `common/etc/pesdb/*.bin` tables from the list in §4.
2. **Describe it in the reply.** `size` = file length; `hash` = SHA-256 of the file as 64 lowercase hex characters; `dlc_version` = the integer form of `<ver>` ("101" for "1.01"). It must be higher than what clients already have. To publish again, raise both values together.
3. **Serve it over plain HTTP GET** at the `url` given in the reply, e.g. `http://<server>:8080/livedata/101.cpk` on our existing HTTP port: status 200, exact `Content-Length`, raw body. Today `httpgate.py` answers unknown GET paths with "ok" or an HTML page, so this needs a new route.
4. **The CMD_GET_DLC_FILELIST reply.** Lists are arrays of real msgpack maps:
   ```
   {"result":"NOERR",
    "dlcfile_list_count":1,
    "dlcfile_list":[{"name":"livedata_101.cpk","size":<len>,"url":"http://<server>:8080/livedata/101.cpk",
                     "hash":"<sha256 lowercase>","dlc_version":"101","required_program_version":10100}],
    "variation_file_list_count":0,"variation_file_list":[],
    "extra_file_list_count":0,"extra_file_list":[]}
   ```
5. **What clients do next:** clients with an older version download, verify, save and bind it. At the next login, the same entry no longer counts as new and the "No new Live Update" dialog stops: the client's version is non-zero and the server's version is non-zero.
6. **Server-side checks:** compare `livedata_version` in JOIN_ONLINE_MODE with the current version and refuse mismatches. Pair players only when their CHECK_CHEAT sums are equal.

Confidence: high for the reply shape, the "new" comparisons, GET, the exact size and SHA-256 (all read in code). Medium for the version_wu.bin layout and the table list (inferred from the dp/xu files and the name table).

## 6. Still unknown / what a live test would settle

- **Source of the `[netmgr+0x68]` server DLC version** in state 4 (inferred: GET_SVRVERSION `dlc_version`). Test: reply "1.01" and expect SGJC392.
- **Whether the client uses GET_SVRVERSION's `livedata_version`.** No consumer was found.
- **Exact save-file name and on-disk encoding** for slots 0x1b–0x2b, and the directory behind type 3 (ExtraUpdate.bin). Test: ship a tiny CPK, then list the save folder and `game/files/download/`.
- **version_wu.bin field +0x14 semantics.** Test: a mismatch shows whether anything rejects it.
- **Which pesdb tables are actually re-read after binding**, e.g. Team.bin, which is not in the livedata list. Also how PlayerWeekly affects the sums outside mode 1.
- **The message text for each error code** (0x1420ac100 → string ids 0x20f00xx), and whether old live data stays bound after NOT_DELIVERED.
- **Variation and Extra content:** probably myClub card art ("variation/%03d.png") and stadium assets [guess]. No test data exists.
- **How the client handles `ERR_DIFFERENT_LIVEDATA_VERSION`.**
