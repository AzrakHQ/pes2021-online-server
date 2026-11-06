# Co-op 2: SYNC_PROGRESS, the zero pids/addresses, and MXNV_0010 (PES2021.exe 1.01.00, static)

> **Live (2026-11-06, livetests.md):** the SYNC_PROGRESS echo and `game_sycom "P2P"` behaved exactly as described (START_COOP_MATCHING, then SEND_SYNC_NOTICE / TURN list / SEND_TURN_ADDRESS_DATA, MXNV_0010 gone); the session then stops at `START:pid=0` with an empty pid_list and MXNV_0013, as the follow-up explains: co-op never hands its peer list to NtlImpl. **Needs a game-side (mod) patch; no server reply can fix it.** → **Done: mod 0.5.0 (both patch steps below, as hooks) + server pescoop.py; a co-op match kicked off live (livetests.md).** Also needed: real `use_team_id` values (262143 on both sides crashes the load).

Static reading only. Tags: **[static]** (address), **[inferred]**, **[guess]**. Dumps are in the working folder
(not published). Names: **g** = `0x140ff0a20()`. **C** = `[g+0x30]` (co-op object). **N** = `[g+0x58]` (the
"network mode" struct: +0 type, +0x10 relay "ip:port", +0x60 peer vector, 0x30-byte entries).

## Short answers

1. **SYNC_PROGRESS is a group-presence handshake run by `onlinemode::TaskSyncProgress`, a child task of
   TaskCoopMatching.** The task waits until every non-guest member of my own group, **me included**, shows up in
   some received `WATCH_NOTICE SYNC_PROGRESS`. I am not added locally, so the server must echo the notice to the
   sender too (as it did). The timeout is 30 s. Nothing else is expected before START_COOP_MATCHING.
2. **The push was parsed correctly. The zeros are not a parse failure.** SEND_MATCHING_RESULT fills `member` /
   `vs_member` only when N.type == 1 (P2P). With type 0 it fills nothing: pid 0, zero addresses,
   `game_sycom:"NotImplement"`. That is exactly the log.
3. **N.type was 0 because our GET_GAMERELAY reply has `game_sycom:""`.** Quick Match forces type 1 after the
   relay reply; **co-op does not**. Type 0 then skips SEND_SYNC_NOTICE and the TURN list, and leaves the session
   config at type 0 (CsClient) with an empty address list. CreateMultiplaySession rejects an empty list →
   **MXNV_0010**.
4. **Fix 1 (server, one line): answer GET_GAMERELAY with `game_sycom:"P2P"`** (at least for co-op players).
5. **Second problem, client side: co-op never gives the P2P layer the peers' addresses.** The WATCH_COOP_MATCHING
   handler drops `host_addr` / `reflexive_addr` / `relayed_addr` / `ntl_nat_type` (they are parsed, then ignored).
   So with "P2P" each peer entry has address 0.0.0.0:0 and NAT type 1. Direct NTL can't connect. Only the TURN
   path can still work. That is untested.

## 1. SYNC_PROGRESS: which task waits, and for what

- **TaskCoopMatching ctor** 0x141132d50 creates `TaskSyncProgress("CoopMatching")` (factory 0x141147970, ctor
  0x1411475e0, vtable 0x1428789d0) and stores it at task+0x78 [static]. **TaskCoopVSComOpponent** ctor
  0x141139d50 does the same with "CoopVSComOpponent" (0x141139e92). So vs-COM co-op runs the same handshake
  [static].
- **TaskCoopMatching state 0** (0x141133240) [static]:
  - It clears the opponent slot (C+0x90) and sets my side C+0x5c = 0.
  - If my group's owner_pid C+0x60 ≠ 0, it becomes C+0xf8.
  - It looks up the C+0xf8 member in my member vector C+0x70. If there is none: error log line 195 → fail.
  - If the WATCH_COOP_MATCHING watch (task+0x88) is missing: line 201 → fail.
  - It collects every member of my group whose guest byte (+0x30) is 0 and calls
    `TaskSyncProgress::Start(list, 30000 ms)` (0x141147fd0).
- **Start** 0x141147fd0 [static]: expected set (+0x90) = list ∪ {my pid}, sorted and de-duplicated. Deadline
  +0x80 = now + 30 000. Then state 1.
- **Each tick** 0x141147880 [static]:
  - Send 0x141147d00 → 0x141091b00: `CMD_SEND_NOTICE {pid_list: expected set,
    data: {notice_key:"SYNC_PROGRESS", pid_list: heard ∪ {me}}}`. It is re-sent whenever the "heard" set grows
    (flag +0x78).
  - Receive 0x1411479d0 → 0x141090bc0: for each new queued notice with `notice_key == "SYNC_PROGRESS"`, add every
    pid of its `data.pid_list` to "heard" (+0xa8).
  - Done 0x1411478e0: every expected pid is in "heard" → state 2. If the deadline passes → state 3.
- **TaskCoopMatching state 1** (0x141133890): state 3 → error line 231 → fail. State 2 → state 2 = the owner
  sends START_COOP_MATCHING (0x1411336d0, only if my pid == C+0xf8) [static].
- **Server rule:** relay every `CMD_SEND_NOTICE` as `CMD_WATCH_NOTICE {data}` to **every** pid in `pid_list`,
  sender included. For groups of 2–3, each member then hears the others and itself [inferred from the set logic].

## 2. Why pid/addresses are 0: the network-mode type

- **SEND_MATCHING_RESULT filler** = vtable slot 7 of CommandObjectCmdSendMatchingResult, 0x1410c2b40 [static]:
  - N.type == 2 ("SC"): `game_sycom:"SC"`, relay ip/port from N+0x38 / N+0x58.
  - N.type == 1 ("P2P"): `game_sycom:"P2P"`. `member` = my own NTL addresses (0x14114a910()+0x10).
    `vs_member` = **the first entry of N+0x60** (pid +0, host family +4 / sa_data +8, reflexive family +0x18 /
    sa_data +0x1c, nat +0x2c).
  - Anything else: none of these fields are written → `"NotImplement"` / pid 0 / zeros (0x1410c2c5e falls
    through) [static]. That matches the log.
- **Who sets N.type** [static]:
  - GET_GAMERELAY handler 0x141092240: `game_sycom` "P2P" → 1, "SC" → 2 (keeps "ip:port" at N+0x10), anything
    else → 0.
  - Co-op GetGamerelayForMatching 0x141134310 resets N first (0x1410921d0 → type 0, peer vector empty).
  - Quick Match 0x14111a7bb forces type = 1. The lobby-room session 0x1421a7b80 forces 1. **No co-op code writes
    N.type.** I checked this with a scan of all 2503 callers of g (the working folder, `g60.py`).
- **What type 0 does in TaskMatchSession** (0x1411430b0) [static]:
  - No SEND_SYNC_NOTICE (check at 0x1411434cf).
  - No GET_TURN_SERVER_LIST (0x141143abd).
  - SetConfig 0x141144d80 has no branch for type 0. So the session config (task+0xa8) keeps its ctor value,
    type 0 = CsClient (0x1411c844e), and its address list (cfg+0x28 = task+0xd0) stays empty.
  - CreateMultiplaySession 0x1411444e0 → 0x14115c170 → 0x14115dbc0: **an empty cfg+0x28 list returns false**
    (0x14115dcdb..0x14115dce5) → MXNV_0010 (line 431).

## 3. How co-op builds the peer list, and what it reads from our push

- After the GET_GAMERELAY reply, **0x141134c00** builds N+0x60 [static]:
  - It walks the HOME slot, then the AWAY slot (C+0x58 / C+0x90, chosen by C+0x5c).
  - It takes every 0x68-byte member record **except my own pid**. My own local guests are counted in
    TaskCoopSession+0x7c instead.
  - Each entry is {pid = rec+0x28, rec+0x3c..+0x67}. In the record: host family +0x3c, host sa_data +0x40
    (14 bytes), reflexive family +0x50, reflexive sa_data +0x54, nat +0x64.
  - It also sets N+4 = C+0xf8 (the session owner), and N+8 / N+0xc = the HOME / AWAY `owner_pid` (0 → that
    group's first member pid).
- **The WATCH_COOP_MATCHING handler 0x14110fe00 never writes rec+0x3c..+0x63.** They stay 0, and +0x64 = 1
    (zero-init 0x1411102bd..0x1411102e2). It writes only these fields [static]:
  - rec+0x28 pid (from Result member +0x28) and rec+0..0x28 pname (0x141059ad0);
  - rec+0x30 guest word (GUEST_NONE 0, GUEST 1, other 0x101), rec+0x33 is_follow == "YES";
  - rec+0x34 coop_level, rec+0x38 coop_manner (C..AAA → 0..5).
- The parser 0x141dc9a70 does decode the addresses into the Result member: host family +0xd8 / sa +0xdc,
  reflexive +0xf0 / +0xf4, relayed +0x108 / +0x10c, ntl_nat_type +0x120. Nothing reads them [static].
  CMD_GET_IPANDPORT_NTL exists but its command object is never created (no reference to "CmdGetIpandportNtl")
  [static]. **So there is no way, in 1.01, for the server to hand co-op peers each other's addresses.**
  [inferred: Konami's co-op probably ran on "SC", the game relay; only "SC" works without peer addresses.]

## 4. Exact layout the client reads (WATCH_COOP_MATCHING), and the corrected push

The parser key order and nesting match what we sent [static 0x141dc9a70]: `group_list` = list of bin blobs;
in each blob, `member_list` = list of bin blobs; in each of those, `member` = bin blob; `pname` and the three
`*_addr` = blobs. **Keep the structure.** Only these fields are used [static 0x14110fe00]:

```
{result:"NOERR", matching_state:"FINISH",                    # str → C+0xc0 (3)
 group_list_num: 2,                                           # int (not used for the loop)
 group_list: [ BLOB{                                          # Result group, 0x20 stride
     member_list: [ BLOB{                                     # Result member, 0x3e8 stride; max 3 kept
         member: BLOB{ pid:int (+0x28, REQUIRED; finds "my group"),
                       pname: BLOB{name, xuid_one, psn_user_id:BLOB{...}, is_deleted} (copied),
                       is_owner:"YES"/"NO" (+0x8; last YES → C+0xf8 = session owner),
                       side:"HOME"/"AWAY" (+0x278; read for MY member only → my side; other group = other side),
                       matching_order:int, host_addr/reflexive_addr/relayed_addr/ntl_nat_type (parsed, IGNORED) },
         guest_kind:"GUEST_NONE" (+0x380 → rec+0x30 = 0 → included in the P2P pid list),
         is_follow:"NO", coop_level:int, coop_manner:"A" } ],
     member_list_num:int,
     use_team_id:int (262143 = none; else team loaded for that side),
     owner_pid:int (group+0x18 → slot+8 → N+8/N+0xc), group_id:int (group+0x1c) },
   BLOB{ ...AWAY group... } ]}
```

For 1 vs 1 co-op, the push we sent is already right: one `is_owner:"YES"` (pid 3, HOME), and `owner_pid` = each
group's own pid. Leave the addresses in; they cost nothing.

## 5. What the server must do between the push and SYSTEM_NOTICE START (with fix 1)

1. GET_GAMERELAY → `{result:NOERR, game_sycom:"P2P", ...}`. Then N.type = 1, and 0x141134c00 builds N+0x60
   = [the other player].
2. GET_MATCHID → the room's non-empty id (asked again later by TaskCoopStartGame).
3. **New in the log:** SEND_SYNC_NOTICE `data "<other pid>,"` → push WATCH_SYNC_NOTICE to the other pid
   (existing relay; the room must hold both pids).
4. GET_SERVER_ENV; **new:** GET_TURN_SERVER_LIST (HTTP) → our TURN list as for 1v1.
5. SetConfig makes one cfg entry per peer from the peer's *reflexive* address. For co-op that is "0.0.0.0" / "0"
   (0x1411cb630 formats a zero address). CreateMultiplaySession then passes the non-empty check.
6. The usual P2P set-up: SEND/WATCH_TURN_ADDRESS_DATA, SYSTEM_NOTICE START/END (existing relays).
- Note: net.md says WithTurn init needs **exactly one** element in a 0x40-byte config vector (0x14122eab1). If
  that vector is this peer list, 1 vs 1 co-op passes and 2v2 would fail [guess].

## 6. Live test that settles it (one change, stop at the first signal)

- **Change:** reply `game_sycom:"P2P"` to CMD_GET_GAMERELAY (co-op only is enough). Everything else stays as today.
- **Signal 1, proves the type fix** [static prediction]: after GET_MATCHID, each copy sends
  **SEND_SYNC_NOTICE** and then **GET_TURN_SERVER_LIST**. SEND_MATCHING_RESULT, if it comes, has
  `game_sycom:"P2P"`, `member` = own addresses, and `vs_member.pid` = the other pid with zero addresses.
  MXNV_0010 is gone.
- **Signal 2, the address question:**
  - SYSTEM_NOTICE START/END, then SET_GAMERELAY_CHECK_RES NOERR and SET_GAMEMEMBER from pid 3 only → TURN carried
    it. Stop there.
  - MXNV_0013 (session failed, line 476) after START → the zero peer addresses block the link.
- **If signal 2 fails**, there are two ways on [inferred]:
  - (a) **Mod patch (small).** In 0x14110fe00, after the record is built (before the push at 0x14111073d),
    copy from the Result member (r15) into the record (rbp+0): +0xd8 → +0x3c (4 bytes), +0xdc → +0x40
    (14 bytes), +0xf0 → +0x50 (4 bytes), +0xf4 → +0x54 (14 bytes), +0x120 → +0x64 (4 bytes). Co-op then gets
    the same reflexive-address peer entries as Quick Match. The server keeps rewriting reflexive per viewer as in
    1v1.
  - (b) **"SC" plus a game relay** speaking Konami's CsClient protocol (not reverse-engineered). Much bigger.

## Still open
- Whether the P2P/TURN layer connects with a 0.0.0.0 reflexive hint (live test §6).
- What the WithTurn "exactly one element" vector is (it matters only for 2v2 / 3v3).

---------------------------------------------------------------------------------------------------
## Follow-up (live log server-coop3.log, 16:16:50–16:17:26): START:pid=0, empty pid_list, MXNV_0013

### What the log shows
- **The co-op peer list is correct.** SEND_MATCHING_RESULT `vs_member` has pid 4 on copy 3 and pid 3 on copy 4.
  Its addresses are zero and its nat type is 1, exactly as §3/§5 predicted.
- 0x141134c00 takes the peer pid from the **member blob's `pid`**: Result member +0x28 → record +0x28 → entry +0.
  It skips only my own pid. It checks no side, guest or owner field [static, §3].
- **SEND_SYNC_NOTICE is also correct:** pid 3 sent `pid_list:[4], data:"4,"` and pid 4 sent `[3], "3,"`. The
  data names the other side, as in 1v1. The pushes went to the peer (log lines 395–402).
- **What is broken is the NTL layer.** Both copies sent `START:pid=0;` with `pid_list:[]`.
  - The rendezvous text and pid_list come from `onlinesystem::NtlImpl`: own pid at +0x908, peers at +8 (count
    +0x438), copied from +0x43c / +0x864 by the connect thread 0x1411d9620 [static].
  - The only writer of +0x908 is NtlImpl vtable slot 8, 0x1411d8030 (SetPid).
  - The only writer of +0x864 / +0x43c is slot 7, 0x1411d88e0 (SetPeers). It takes 1–2 entries of 0x20c bytes
    each [static].

### Why NtlImpl stays empty in co-op
- **0x141092720** is the function that hands N+0x60 to NTL [static]:
  - It calls 0x14114c320 → NtlImpl slot 8 (SetPid, own pid from NTL info +0x15c).
  - If N+0x60 has **fewer than 2** entries (0x1410927ab), it converts each 0x30 entry into a 0x20c NTL peer:
    pid, host block at +4, reflexive block at +0x84, nat at +0x104.
  - It then calls 0x14114c770 → slot 7 (SetPeers).
  - It also stores the first peer pid at NTL info +0x160.
- **Its only callers are Quick Match (0x14111a901) and the lobby-room session (0x1421a7c71). Co-op never calls
  it.** TaskCoopSession builds N+0x60 (0x141134c00) but never pushes it to NTL [static].
- Result: own pid 0 and no peers. START names pid 0 and has nobody to go to. The connect fails after 30 s
  (ER_CONNECT, failed_reason 1), then MXNV_0013 [static + log].
- **Even when called, 0x141092720 handles at most one peer** (`cmp rdx,2; jae skip`). With 2 or more peers it
  sends SetPeers with count 0. So in 1.01, P2P can carry 1 vs 1 co-op at most. 2v2 / 3v3 co-op can only run on
  "SC", the game relay [static 0x1410927ab; inferred about retail].

### Corrected WATCH_COOP_MATCHING push
- **No change is needed.** The push in the log is right. pid, side, is_owner and owner_pid are all used as
  intended (see the dict in §4).
- Nothing in the push can make the client call 0x141092720 or copy the addresses.

### Is there a server-side way around it?
- **No** [static + inferred]:
  - SetPid and SetPeers are reached only through 0x141092720.
  - The address copy is missing in the client handler.
  - CMD_GET_IPANDPORT_NTL is never created.
  - WATCH_TURN_ADDRESS_DATA only gives the TURN layer the peer's relay address (log 466/470: both sent
    SEND_TURN_ADDRESS_DATA). The NtlImpl rendezvous (START/END, the pid list, the connect tag "<pid>-<pid>-time",
    0x1411d81d0) still needs NtlImpl's own pid and peer list. With pid 0 and no peers, TURN cannot save it.
- Leftover state from an earlier Quick Match in the same session might mask this. Don't rely on it [guess].

### Mod patch (minimum for 1 vs 1 co-op over P2P/TURN)
1. **Hand the peers to NTL.** In GetGamerelayForMatching 0x141134310, right after `call 0x141134c00`
   (0x1411346c3), call `0x141092720(rcx = [0x140ff0a20()+0x58])`, as Quick Match does at 0x14111a901. This alone
   should fix `START:pid=0` / the empty pid_list (live signal: `START:pid=3;` with `pid_list:[4]`).
2. **Give the peer entries real addresses** (§6a). In 0x14110fe00 copy the Result member fields into the record:
   +0xd8 → +0x3c, +0xdc → +0x40 (14 bytes), +0xf0 → +0x50, +0xf4 → +0x54 (14 bytes), +0x120 → +0x64.
   - Without step 2, NTL gets 0.0.0.0 host and reflexive addresses. The connection could still come up over the
     TURN relay, but that is untested [inferred].
   - The server keeps rewriting `reflexive_addr` per viewer in the push, as in 1v1. Once copied, it reaches NTL.
- Steps 1 and 2 together give co-op the same NTL input as Quick Match. 2v2 and larger stay impossible on P2P
  (one-peer limit above).
