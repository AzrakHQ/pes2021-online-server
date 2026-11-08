# Spectating / observing ("Watch match") — static RE

## Corrections from later findings (2026-11-06)
- **Spectating works live** (2026-11-06, spectate2.md, livetests.md): recording with `upload "YES"`, the viewer through Online Tournament → PES League lobby → X Match List → Spectate Match, chunks `<WATCHING_GAME>/<match_id>/N.enc`, replay from storage. In GET_WATCHING_MATCH_LIST `team` and `score` are at the game_result level (two each), not per player.
- misc.md §2 (static, 2026-11-06): `data` = base64 of the full 256-byte buffer (keep `data_len` bytes); each record is already a viewer item (no re-encoding); the end item [3][0] is written by the client; both players upload; the URL id is never checked; the only watch menu is the PES League lobby. Not tested live.


Scope: watching other players' live matches (PES League lobby "Watching list" and Matchday "watch match"),
the upload side (players' game recording) and the download/playback side (viewer). All addresses are
PES2021.exe 1.01.00 (image base 0x140000000). Tags: [static] read in code, [log] seen in our traffic,
[inferred] reasoned, [guess].

## How it works (end-to-end)

```
 PLAYER (match host side, any online match)                 SERVER                        VIEWER
 GET_MATCHID -> {match_id, upload:"YES"}  ------------------>  decide which matches are recorded
 CMD_START_SEND_GAME_SEQUENCE_DATA {pid}   ------------------>  open recording for match_id
 recorder taps P2pFullMeshSession / CsClientSession frames
 every <=5 s or 16 KB: CMD_SEND_GAME_SEQUENCE_DATA
   {match_id, game_seq_data[ blob{type,fromId,channelId,
    sequenceNumber,data(base64?),data_len} ], game_seq_data_num} ---> append records
                                                               |
                                                               |  re-serialise records to a
                                                               |  big-endian item stream, cut into
                                                               |  chunks, [zlib], AES-256-CBC
                                                               v
                                       HTTP GET <WATCHING_GAME url>/<match_id>/1.enc, 2.enc, ...
                                       (404/403 = "not yet", retried; last chunk ends "END_OF_DATA")
                                                               <-------- CMD_GET_WATCHING_MATCH_LIST
                                                               ---------> UtilHttpStreamWatching
                                                                          -> ParameterWatchingGameData queue
                                                                          -> ObservePlayback -> ObserveSession
                                                                          (MultiplaySession mode 3 = observe)
                                                               <-------- CMD_SEND_WATCHING_RESULT (at end)
```

* The viewer is a passive replica: it never talks to the players. It re-plays the recorded session-level
  message stream of one player through an `onlinesystem::ObserveSession` (vtable 0x1428b7db0) inside a
  `MultiplaySessionStrategyObserve` (vtable 0x1428b2950). The data source is only HTTP. [static]
* The client builds almost all of the content itself (including a header frame with the game revision
  triple and the match id, 0x141256720). The server's job is store-and-forward plus a fixed container
  (chunking, optional zlib, AES-256-CBC with a key embedded in the exe, END_OF_DATA trailer). [static+inferred]
* Recording is opt-in per match by the server: `upload:"YES"` in CMD_GET_MATCHID. Our server sends `"NO"` [log],
  so no player ever uploads today.

Related source files and their on-screen error prefixes (errcode.py hash): OnlineSystemUtilHttpStreamWatchingData.cpp
= **DZWO**, OnlineSystemObserveSession.cpp = **PWTV**, OnlineModeTaskPesLgWatching.cpp = **OBMB**,
MenuOnlineObserverTaskSync.cpp = **NNAB**, MatchOnlineObserverForPesServer.cpp = **JCND**,
OnlineModeTaskMatchSession.cpp = **MXNV**.

---

## Upload side (players)

### CMD_GET_MATCHID `upload` field — gate for recording
- Reply parser 0x141b9acd0: `match_id` STR, `upload` STR (→ Result+0xd8). [static]
- OnlineModeTaskMatchSession (0x1411430b0) state 5 (0x141143733): calls 0x140ff7cd0. That returns true if the
  local debug var `direct_online_observe_record`==1, else compares the stored upload string with enum 0x1437f3868
  index 1 = **"YES"** (enum init 0x14021f9c0: 0="NO", 1="YES"). True → state 6 creates CmdStartSendGameSequenceData;
  false → straight to state 7 (CMD_GET_SERVER_ENV). [static]
- Server should: keep `"NO"` (safe default, no extra traffic). Send `"YES"` only for matches we want to make
  watchable. Confidence high.

### CMD_START_SEND_GAME_SEQUENCE_DATA — "start recording this match"
- Transport: gate, client→server. Request (packer 0x141b9b730): msgid, rqid, svrtype, client, pid (no match_id;
  the server must tie it to the session's current room/match_id). [static]
- When: MatchSession state 6, after GET_MATCHID and the SYNC_NOTICE exchange, before GET_SERVER_ENV /
  SetConfig / CreateMultiplaySession (0x141143854..0x14114390e). [static]
- Reply: only `result`. Command-level error (Result+0x98 flag) → error MXNV264 (line 0x108, code 6) "Command error
  CMD_START_SEND_GAME_SEQUENCE_DATA" and the match is aborted (state 0x17). Creation failure → MXNV248. [static]
- Server should: `{result:"NOERR"}`; mark match_id as recording; create the store. Confidence high.

### CMD_SEND_GAME_SEQUENCE_DATA — recorded session frames (batched)
- Transport: gate, client→server, fire-and-forget from a worker thread `SendGameSeqTask`/`SendGameSeqTaskWorker`
  (0x1411daed0 / 0x1411dafb0; worker 64 KB stack). Sender 0x1411db5c0 pulls all pending records from the
  recorder ([mgr+0x70], fn 0x1411cef60) and creates one command per batch (0x1412332f0). [static]
- Request (packer 0x141e50970, map of 8): msgid, rqid, svrtype, client, pid, match_id,
  `game_seq_data` = array of **nested msgpack blobs**, each a map of 6:
  `type` NUM, `fromId` NUM, `channelId` NUM, `sequenceNumber` NUM, `data` STR (encoded by 0x14184d8c0 — the
  encoder twin of the base64 decoder 0x14184e640 used for server_certificate → base64 [inferred]), `data_len` NUM;
  then `game_seq_data_num` NUM. Records are 0x118-byte structs on the client, so `data` ≤ ~256 raw bytes. [static]
- What is recorded [static]: hook 0x1412561c0 / 0x141257000 on `P2pFullMeshSession` (vtable 0x1428b7698, slots
  12–14) and `CsClientSession` (0x1428b6c88) send/receive. Frame parser 0x141257120 splits the session byte
  stream into frames and records each via 0x141257010 → 0x1411ced30:
  - kind 0/1/3: `[kind u8][len u16][payload]` → record {type=kind, fromId=0, channelId=0, seq=0, data=payload}
  - kind 2: `[2][from u8][channel u8][seq u32][len u16][payload]` → record {type 2, fromId, channelId,
    sequenceNumber, data=payload}  (host byte order in the frame)
  - kind 0 payload is a header written by 0x141256720: `[0][0][rev0 BE32][rev1 BE32][rev2 BE32][n][build string]
    [m][match_id or "MATCH_ID_DIRECTONLINE"][u8][u8][u8][k][k bytes member list]`.
- Batching [static]: recorder has 16 buckets; a bucket is closed at ≥ 0x4000 bytes (0x1411ced30) or when
  ≥ 5000 ms old (0x1411cf120, `cmp eax,0x1388`). So expect one SEND every ~5 s per recording player.
- Reply: only `result`; the worker ignores it. [static]
- Server should: append `(type, fromId, channelId, sequenceNumber, base64-decoded data)` in arrival order to
  the match's store (dedupe on (fromId,channelId,sequenceNumber) for kind 2 if both players upload).
  Reply `{result:"NOERR"}`. Confidence: shape high; base64 medium.

### CMD_SEND_DOWNLOAD_STATS_DATA — unrelated
- Request (packer 0x141e49cc0): pid, filename, file_hash, download_type, download_time, downloaded_size,
  average_bps, max_bps, min_bps, result_kind. Telemetry for file downloads (DLC/live data). Reply `result`.
  Only reference to its name is the command factory 0x14116dee0. Server: bare NOERR. [static/inferred]

---

## Download side (viewer)

### WATCHING_GAME urltype — the stream
- URL type enum 0x14021ee70: WATCHING_GAME stored at 0x1437f3b90 = table 0x1437f3a20 + **index 0x2e (46)**;
  UtilHttpStreamWatching looks it up via 0x141b9a250(0x2e) (fn 0x1411df950). We already serve it in
  CMD_GET_URLLIST (pescmds.py URL_TYPES). [static]
- Base URL (0x1411df950): `<WATCHING_GAME url> + "/" + <watching id> + "/"`, where the id is the string set by
  0x1411d0500 — in the real flow from the observe session config (MultiplaySession setup 0x14115c170: if
  config.mode==3, config+0x48 → id). That id is the match_id of the watched match [inferred].
- Files (0x1411dfb90): `<base><N>.enc` (or `.bin` if the encrypted flag +0x1000e8 is cleared; default is 1
  = `.enc`), N = 1, 2, 3 …. Plain HTTP GET via the downloader object 0x14116f910 into a **1 MiB** buffer
  (0x100000), request timeout 3,600,000 ms. [static]
- Mode: object flag +0x1000c8 defaults to 1 → segmented-file mode 0x1411dff40. A second "HttpStreaming"
  mode 0x1411e0650 (vtable dispatch 0x1411df670) reads one long response (URL +0x80, which is cleared) and
  rejects a body starting with `"\nNotice"` (PHP warning!) — apparently unused in this build [inferred].
- Chunk format (decrypt 0x1411e0b80, 0x1411df7c0) [static]:
  `.enc` body = `IV (16 bytes) || AES-256-CBC ciphertext`, total ≥ 0x20 and a multiple of 16.
  Key (32 bytes, at 0x142897bd0, passed to NclCryptAes 0x1418522a0; BCryptOpenAlgorithmProvider/SetProperty/
  BCryptDecrypt imports):
  `35a110675829e36845dc775162cca1bb69b786ed1e73d82ea9fe7e6176743881`
  Plaintext = `[flag u8][body]`; flag 1 → body is zlib-compressed (uncompress-style call 0x141852b60 →
  0x1403a2900, output buffer 20× input) [inferred zlib], else raw. Padding probably PKCS#7 (0x141852a20) [guess].
  The (decompressed) body is appended to the playback byte queue (0x1411d0200). If the body ends with the
  11 ASCII bytes **`END_OF_DATA`** (0x1411dfdd0) the trailer is cut and the stream is complete.
- Timing / retries (0x1411dff40) [static]:
  - success, not end → N++, wait `delay[min(fails,5)]` = {1000,1000,2000,3000,6000,8000} ms (table
    0x142898050; fails reset to 0 on success → 1 s between chunks), then GET the next N.
  - HTTP failure → fails++ and retry; give up when the time since the first failure exceeds 30 s, or
    **330 s if the status is 403/404 and N>1** (0x1411dfb40) → error **DZWO960** (code 3).
    So "not produced yet" must be answered with **404** (or 403), and the server must produce a chunk at least
    every 5.5 minutes while the match runs. First chunk: only 30 s of 404s are tolerated.
  - HTTP object create fail DZWO850; transport error DZWO947 (code 4); no watching id within 60 s DZWO832
    (code 7); playback queue full (> 0xC000 bytes pending, 0x14172aff0) pauses downloading.
- Stream item format (playback reader, ObservePlayback 0x141259b20 dispatch on first byte) [static]:
  - `0` header (0x141258f90): `[0][ver 0][rev0][rev1][rev2]` (BE u32, each must equal this client's revision
    0x141989390(0..2) — mismatched game version = no playback) `[n][str == local build string]`
    `[m][str == expected id]` `[u8][u8][u8][k][k bytes]` = exactly the kind-0 record `data` the player uploaded.
  - `1` member list (0x1412594d0): `[1][count][count bytes…]` = kind-1 record data.
  - `2` game message (0x141258c60): `[2][fromId u8][channelId u8][sequenceNumber BE u32][len BE u16][len bytes]`
    — big-endian, so the **server must re-serialise kind-2 records** from the uploaded fields.
  - `3` end (0x141258bb0): `[3][reason u8]` → playback ends with that reason.
- Server should: on GET `/<match_id>/<N>.enc`, return chunk N if ready else 404; chunk N body =
  `IV || AES-256-CBC(key, PKCS7([0x00] || items))` (start with flag 0 = uncompressed), items = header item first,
  then records in order (kind 0/1/3 data verbatim, kind 2 rebuilt BE), final chunk appends a `[3][0]` item
  (guess for "normal end") and the ASCII `END_OF_DATA` trailer inside the plaintext. Chunk plaintext ≤ ~1 MiB.
  Confidence: container high (decrypt path read end to end); item framing medium (reader read, writer side is ours);
  exact meaning of the 3 header bytes / reason codes low.

### CMD_GET_WATCHING_MATCH_LIST — list of watchable matches
- Transport: gate. Request (packer 0x141bd0a50): pid, `match_category`, `compe_id`, `index` (paging). [static]
- Users: TaskGetWatchingMatchListPesLeagueLobby / ...MyClubPesLeagueLobby (fns 0x141050fa0, 0x14112d7f0;
  RTTI TaskGetWatchingMatchListBase). Menu "SelectDialogWatchingListPesLeagueLobby". [static]
- Reply (parser 0x141bd11e0, key order): `match_list` = list of blobs { `match_id` STR, `game_phase` STR,
  `game_result` blob { `date` NUM, `ex_type` STR, `pk_flag` STR, `no_match` STR, `player` list of blobs
  { `pid`, `pname` blob{name, xuid_one, psn_user_id blob{account_id, online_id, is_valid_cache, is_deleted}},
  `side`, `is_com`, `cpu_level`, `is_follow`, `is_guest`, `team`, `score` blob{score_1st, score_2nd,
  score_ex1st, score_ex2nd, score_pk} } , `match_category`, `gameend_reason` } }, `match_list_num`,
  `total_match_num`. Exact nesting of the trailing fields is from key order + unpack calls, not verified. [static/inferred]
- Server should: today `{result:NOERR, match_list:[], match_list_num:0, total_match_num:0}` (empty list).
  Later: one entry per match currently recording.

### CMD_SEND_WATCHING_RESULT — viewer's report when watching ends
- Request (packer 0x141d173c0): pid, match_id, is_connect, phase, error_code, reason. Created by the
  menu::OnlineObserverEnd page ctor (0x140bebb60). Reply only `result`. Server: NOERR, log it. [static]

### CMD_SET_MATCHDAY_START_WATCH_MATCH / CMD_GET_MATCHDAY_WATCH_MATCH_REWARD — Matchday variant
- SET request: pid, compe_id; reply result. GET request: pid, compe_id; reply `is_exist_watch_match_reward`,
  `watch_match_reward_list` (present_id, type, title, body, entity_id, grade, name, skill…). The OnlineObserver
  menu picks "pesleague" or "matchday" context (0x140bec090). Server: NOERR / `is_exist_watch_match_reward:"NO"`. [static]

### Viewer tasks (for orientation)
- OnlineModeTaskPesLgWatching (0x1421a71c0): waits on a deadline (`[task+0x70]` vs value from 0x14115fe20) and
  shows a countdown; past deadline → error OBMB107 (code 1). [static, semantics inferred]
- MenuOnlineObserverTaskSync (0x140cf5ce0): pre-match sync for the viewer (TaskSyncUserData, matchPlanDataSync),
  errors NNAB162/178/187/217/240/283 — the data comes from the playback stream, not from extra server
  commands [inferred].
- ObserveSession::Update (0x141246ca0): playback state errors PWTV340/343/347/351 (codes 0–3).
- MatchOnlineObserverForPesServer (0x142167e60, `postOnlineObserveMatch`): in-match supervisor for the
  observe match, error dialogs at lines 325/627/636/645/648/655 (JCND…). [static, role inferred]
- Local tunables (not server): "OnlineSystem" config section read in 0x1411a7c70: ObserveRecordTimeoutMs,
  ObservePlaybackBufferLength, …ReadTimeoutMs, …RecvMatchCommandTimeoutMs, …MatchActiveStatusCheckTimeMs.

---

## Feasibility + implementation outline

**Feasibility: medium. No game-side blocker found**: the container is simple, the key is in the exe, the
upload already carries everything, and the viewer only needs plain HTTP. The unknowns are in item details,
so expect 1–2 live iterations.

Effort estimate: ~1.5–2 days of server work + 1 live session (2 players + 1 viewer = 3 clients).

1. Gate handlers (pesmatch.py): START_SEND_GAME_SEQUENCE_DATA (open store for the sender's current
   match_id), SEND_GAME_SEQUENCE_DATA (decode blobs, base64-decode `data`, append), SEND_WATCHING_RESULT (log).
   GET_MATCHID: `upload:"YES"` behind an env flag (e.g. PESSERVER_RECORD=1). ~0.5 day.
2. Stream writer: per match_id, serialise items (header/kind-1/kind-3 verbatim, kind 2 → BE frame), cut a
   chunk every ≤ 5–10 s or 512 KB, encrypt `IV||AES256CBC(key, PKCS7(0x00||items))`, store as N.enc; on match
   end (CHANGE_GAMEPHASE END / SEND_GAME_RESULT / ABNORMALEND) write the last chunk with `[3][0]` +
   `END_OF_DATA`. ~0.5 day.
3. HTTP (httpgate.py): route GET `<WATCHING_GAME base>/<match_id>/<N>.enc` → file or 404. Needs the URL for
   type WATCHING_GAME to point at a GET-capable prefix (currently `/u{i}`; the client appends `/<id>/<N>.enc`).
   ~0.25 day.
4. GET_WATCHING_MATCH_LIST: list recording matches (match_id, game_phase, players with pid/pname/side/team,
   score). Viewer entry is in the PES League lobby, which itself needs lobby/compe commands we don't serve
   yet → this menu path is the biggest unknown. ~0.5 day + live test.
5. Optional (debug): the exe also supports `.bin` (unencrypted) and `online_observe_playback_from_file`
   / `direct_online_observe_record` debug vars; not reachable without patching.

## Still unknown
> **Status 2026-11-08:** answered by misc.md §2 and the live replays (spectate2.md, livetests.md): `data` is
> base64 of a 256-byte buffer (keep `data_len` bytes); the viewer fetches `/r/<match_id>/N.enc`; both players
> upload and the server keeps one stream; the chunk encryption, padding and compression pesreplay.py writes play live; the viewer reaches the
> watch list through the PES League lobby. Still open: the three header bytes after the id, the kind-3 reason
> values, viewer pacing.
- Whether the viewer's `id` path component is exactly the match_id string (it comes from observe config +0x48;
  the writer of that field for the real menu path was not traced). Live test: a viewer's first GET shows it.
- Base64 vs. raw bytes in `data` (encoder 0x14184d8c0 not read in full) — first live SEND_GAME_SEQUENCE_DATA settles it.
- Which player(s) upload — both clients probably run the recorder when `upload:"YES"`; whether the server
  should merge or keep one (likely keep the HOME player's stream; kind-2 fromId shows both directions).
- Meaning of the three bytes after the id in the header item, the kind-3 reason values, and whether the
  viewer needs extra pacing (playback buffering thresholds are local config).
- PKCS#7 padding (0x141852a20) and zlib (0x1403a2920) identification — should be confirmed by reading those
  two functions or by test (send uncompressed first, flag 0).
- How the viewer reaches the PES League lobby watching list on our server (lobby/compe commands not served).
- Note for the co-op half: the recorder hook revealed `onlinesystem::P2pFullMeshSession` (vtable 0x1428b7698)
  next to `CsClientSession` (0x1428b6c88) — co-op's session strategy is very likely the full-mesh one.
