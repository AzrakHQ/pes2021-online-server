# Can the match link-up run over TURN? (static RE, PES 2021 1.01.00)

> **Confirmed live (2026-11-06, livetests.md):** with the owner shown as 192.0.2.1:9 and the guest as 203.0.113.1:9, the owner sent 14 bytes of ChannelData right after the binds and got 270 back, and the match ran entirely over the relay through DATASYNC, TEAMSELECT, ENTRY and 1ST (kick-off), with no END notice and no direct game-to-game UDP.

This is static reading only. No network traffic, no game launch. Addresses are VAs in PES2021.exe.
Tags: **[static]** = read in the code, **[inferred]**, **[guess]**.
Dumps: the working folder (not published).

## Short answer

1. **MXNV476 means the WithTurn strategy reported state 5 or 6.** There are two timers that can cause it:
   - **(a) The kick-off gate.** The kps match session is started only after P2pTurnIoFailover reports state 2.
     If that has not happened within `Network.NetworkIoConnectionTimeoutUs` (default **15 s**, counted from strategy
     init), the strategy marks itself failed. **[static 0x14122f452..0x14122f489, 0x14122ec15..0x14122ec29]**
   - **(b) The kps session's own connect timeout.** Once kps has started, `P2P.Session.ConnectionTimeoutUs`
     applies (default **10 s**). **[static 0x141245b55..0x141245b74, default 0x141ff3e2c = 0x989680]**
2. **Why nothing went over TURN.** TURN ChannelData from the game is only ever kps traffic copied onto the relay by
   the failover's send function. **The very first kps datagram always gets a TURN copy.** **[static 0x141252916..0x141252991]**
   So "no ChannelData at all" means kps never sent anything. The most likely reason is (a): the failover never reached
   state 2 inside the 15 s window, so kps was never started. **[inferred]**
3. **Yes, the design allows a TURN-only link-up.** The failover reports state 2 when the TURN route alone is up
   **[static 0x1412519e8]**. While P2P fails, every kps datagram is copied to TURN. The failover then switches to
   TURN mode (reason 1/4/5, nettune.md). The TURN route ignores the kps destination address. There is one relay
   channel per match, and it is bound to the peer address taken from WATCH_TURN_ADDRESS_DATA.
   **[static 0x141249f50 → 0x1411f0d30 → 0x1411f0c40]**
   - The server does **not** need to put anything into `relayed_addr` in the player list. That field is not used by
     this route.
   - What the server must provide: both allocations, both WATCH_TURN_ADDRESS_DATA pushes and both ChannelBinds,
     all finished well inside 15 s of CreateMultiplaySession, and accepted by the client. Optionally the server can
     raise both timeouts through `proto_opt`.

## Q1. What TaskMatchSession waits for at MXNV476

### The polling chain [static]
- TaskMatchSession state 18 (0x141143ef6) calls `MultiplaySession state` (0x1411ac0c0).
- That calls `strategy->GetState` (0x1411f82e0 → vt+0x20).
- For WithTurn this is **0x14122ef60**. Its result is handled like this:
  - == 4: connected.
  - < 4: keep waiting.
  - \> 4: MXNV_0013, line 0x1dc = 476, sub 0xd (0x141143f6c..0x141143fbe).
- **WithTurn GetState (0x14122ef60):**
  - `[s+0x8c9]` set → 6. Only the ctor writes it (to 0), so this never fires.
  - `[s+0x8c0] == 0` → **6** (the gate timed out; see below).
  - Failover state (0x14122f090 → failover vt+0x50 = 0x141251910):
    - < 0 or > 2 → 6.
    - ≤ 1 → 1 (waiting).
    - == 2 and kps not started yet (`[s+0x8c8] == 0`) → 1 (waiting).
    - == 2 and kps started → **kps session state** via base 0x1411f8160 (`[s+0x7d8]->vt+0x20`; 6 = failed).

### The kick-off gate (WithTurn update 0x14122f420) [static]
- Init stores `start = now` in `[s+0x8b8]`, `limit = cfg+0x28` in `[s+0x8b0]` and sets `[s+0x8c0] = 1`
  (0x14122ebad..0x14122ec29). Init fails if cfg+0x20 ("present") is 0.
- `cfg` is the strategy's multiplay-parameter struct (`strategy+0x5f8`, built by 0x1411a6b10).
  - cfg+0x20/+0x28 is `Network.NetworkIoConnectionTimeoutUs`. The reader is at 0x1411a6e2e..0x1411a6e51
    (struct base rbp-0x50; the value lands at +0x20/+0x28).
  - Its default is **0xe4e1c0 = 15 000 000 µs** (0x1411a7909).
- Every tick, while kps has not been started:
  - If `now − start ≥ limit`, then `[s+0x8c0] = 0` and an error is logged with tag "Socket" from
    `OnlineSystemMultiplaySessionStrategyP2pFullMeshWithTurn.cpp` line **643** (0x14122f4bf..0x14122f516).
    GetState then returns 6, which gives MXNV476.
  - If the failover state == 2, it calls `kps->vt+8` = **0x141245a50** (kps start). On success it sets
    `[s+0x8c8] = 1` (0x14122f53d..0x14122f572).
- **The kps match session (Dlcp key exchange, Lltcp, Msp) is not started at all until the failover says 2.**
  Before that, the only traffic is TURN-server control traffic (Allocate, Refresh pings, ChannelBind). No ChannelData
  is sent.

### When the failover reports 2 (0x141251910) [static]
- `[fo+0x31a]` (closed) → 4.
- If `[fo+0x3e2]` is set and P2P state ≤ 1 → 1.
  - `[fo+0x3e2]` = WithTurn `[+0x898]` = TaskMatchSession `+0xac` = "ping limit set" (0x141145371..0x14114537c).
    It is 0 in Friendly-lobby matches (ctor 0x141142e5b; only Quick Match writes +0x88, at 0x14111aa78).
- **If TURN route state == 2 or P2P state == 2 → 2.**
- Otherwise → 1, or 2 if `[fo+0x319]` is set.
- **TURN route state** = 0x1412495e0 → Turn 0x1411ef590 → 0x1411ef520 on the Turn peer object:
  - `[o+0xc]` → 1.
  - The ktc channel object at `[o+0xe8]`, or else `[o+0xd8]`, in state 3 → **2**.
  - That channel object is created in Turn state 5 only after the peer address arrives: `[o+0xb0]`, then
    0x142060820 and 0x1411eb8b0 (0x1411f3f8d..0x1411f4000).
  - The peer address comes only from CMD_WATCH_TURN_ADDRESS_DATA (handler 0x141212010 → ParameterTurn+0xb8 →
    WithTurn 0x14122f680 → Turn 0x1411f1220/0x1411f1090).
  - Its state is ktc's per-channel state (0x14206cf60 → 0x1420656c0: `channel_map[n]+0xb0`, valid only while the
    ktc session's public state is 2). **That state 3 means "ChannelBind accepted" is [inferred].**
- **P2P state** = NTL io 0x1412433a0: io+0 set → 2, io+8 set → 4, otherwise 0.

### Timing [static + inferred]
- The TURN connect itself is only armed in WithTurn init: the server list goes to Turn+0x98 (0x14122e936 →
  0x1411f0bb0). So the TURN TLS ClientHello time is roughly the gate's t0. **[inferred]**
- The whole chain has to fit in 15 s: TLS → Allocate → SOFTWARE directive → DTLS → Allocate →
  SEND_TURN_ADDRESS_DATA → our push → peer ChannelBind (both ways).
- From the client's side, gate (a) firing means one of these:
  - the relay channel was bound too late;
  - our ChannelBind success was never accepted by the client (for example it was sent to the old port after the 10 s
    DSCP socket swap, or it was badly framed);
  - the peer's WATCH arrived before that client's watch was registered (0x14114e120, at CreateMultiplaySession) and
    was lost.
  
  Which of these happened cannot be told statically.

### A loose end: END
- **END is only sent after NTL reports the peer as connected** (0x1411d7700 → NTL vt+0xc8 = 0x1418eb1a0, which
  blocks for up to 20 s = 0x1312d00 µs). Then 0x1411d40b0 needs status 0xe000001 and state 4 for every peer before
  0x1411da200 sends END. **[static]**
- If both copies really sent END with 192.0.2.1:9 everywhere, then NTL found a path some other way (same PC). In that
  case the P2P state may have been 2, kps would have started, and (b), the 10 s Session timeout, is the one that fired.
  **[inferred]**
- The "~10 s after START" figure fits (b), or fits (a) if START came about 5 s after strategy init. The timestamps in
  the live test below settle it.

## Q2. Can the link-up run over TURN when the direct path never works?

**Yes, by design. [static]**
- **The kps start condition is satisfied by TURN alone** (failover state 2 when TURN == 2, 0x1412519e8).
- **The kps traffic reaches TURN even in P2P mode** (failover send 0x141252740):
  - Mode 0 (P2P) sends on P2P (0x141251b90).
  - With `P2pModeTurnSendEnable` (default true, `[fo+0x388]`), it also sends on TURN (0x1412531f0) whenever the P2P
    send failed, or on the first send, or once 5 s have passed (`[fo+0x2f0]/[fo+0x2f8]/[fo+0x38c]`).
- **The switch to TURN mode follows** (decision 0x141253850, nettune.md §2):
  - P2P link state ≠ 2 → reason 1.
  - P2P never received while TURN received within 7.5 s → reason 4.
  - P2P idle ≥ 7.5 s → reason 5.
- **Addresses are labels only.** kps addresses its peer by the `IP:port` strings built in SetConfig from the session
  member list (0x1411cb630 "%d.%d.%d.%d" / 0x1411cb750 "%d", 0x1411451d9..0x1411452c8, → strategy cfg+0x188).
  - The failover TURN leg resolves the same strings into its own label (0x141250ba0/0x141248a80 get the caller's
    rdx/r8).
  - TURN send ignores the destination (0x141249f50).
  - So it is harmless if the player-list addresses are unreachable (or identical on both sides).
- **What the server must provide:**
  1. **No change to `relayed_addr` in the player list or member blobs.** That field feeds only NTL's own candidate
     list. The ktc route never reads it **[static: peer only from WATCH_TURN_ADDRESS_DATA]**. Putting the TURN
     relayed address there would make NTL send UHP to the relay port from an IP that has no permission, and the relay
     would drop it **[inferred]**. Leave it as the game reports it (family 0).
  2. **WATCH_TURN_ADDRESS_DATA timing.**
     - Push A's address to B as soon as A sends it.
     - **Push it again when B sends its own SEND_TURN_ADDRESS_DATA.** At that point B's watch is certainly
       registered: SEND only happens after B's allocation, which happens after CreateMultiplaySession.
     - Push again after every TURN restart (a new relayed address means a new SEND).
     - The format is the client's bytes unchanged (net.md).
     - A push that arrives too early is kept in ParameterTurn and used once the own allocation is up (0x14122f581..f596).
  3. **ChannelBind replies must reach the client.** Use the DTLS framing from ktcdtls.md, sent from the directive
     HOST:PORT to the client's **current** source port. Either migrate on the 10 s socket swap or set
     `DscpEnable:false` (turncycle.md). Check that the client does **not** retransmit the ChannelBind after our success.
  4. **proto_opt (optional but recommended for the test).** Raise both timers so a slow relay set-up does not lose the
     race:
     ```
     {"P2P":{"Network":{"NetworkIoConnectionTimeoutUs":40000000,"DscpEnable":false},
             "Session":{"ConnectionTimeoutUs":30000000}}}
     ```
     - The paths are `root.P2P.Network.*` (0x1411a6c00) and `root.P2P.Session.ConnectionTimeoutUs`
       (0x1411a8e10 → 0x1411a8c30).
     - Values are JSON integers in µs (int64 getter 0x141174780).
     - Level profiles `P2P_HIGH_LEVEL` and the others override these if present.
     - Two distinct values (40 s and 30 s) make it possible to tell which timer fires.
     - Other link timers, if the KX turns out to be slow: `P2P.Domain.LinkTimeoutUs` and
       `P2P.ProtocolTransmission.ConnectionTimeoutUs` (both 15 s), `P2P.Domain.KeyExchangeTimeoutUs` (20 s).
       **[defaults from nettune.md]**
  5. **There is no initial-mode setting.** The failover always starts in P2P mode (ctor 0x141250375). `turn_mode` and
     `direct_online_turn_mode` (TurnOnly) are local debug variables (0x141b34850), not server-settable **[inferred]**.
     `NatTraversal.DoNotGiveUpMode` only feeds NTL (reader 0x1411a6720). It does not touch the ktc route **[inferred]**.

## Q3. Server recipe and a live test that proves a relay match

### Recipe
1. Keep the unreachable P2P addresses for the test (192.0.2.1:9 in REQWATCHL_ROOMPLAYERLIST_NTL and JOIN_ROOM_NTL).
   Leave `relayed_addr` untouched.
2. Relay:
   - DTLS framing as in ktcdtls.md.
   - DTLS association migration, or `DscpEnable:false` in proto_opt.
   - Answer every request on the DTLS leg within 10 s.
   - ChannelBind success goes to the client's current port.
3. Gate server:
   - SEND_TURN_ADDRESS_DATA(A) → push WATCH(A) to B.
   - On SEND_TURN_ADDRESS_DATA(B) → push WATCH(B) to A **and push A's latest to B again**.
   - Repeat after every re-allocation.
4. GET_SERVER_ENV `proto_opt` = the JSON above.

### Live test (stop at the first proving signal)
1. **Log timestamps (no behaviour change).**
   - Log these per game: first TURN TLS ClientHello (≈ gate t0), START and END from each side, SEND_TURN_ADDRESS_DATA,
     our WATCH pushes, each ChannelBind request and our reply (note any client retransmit after our success), the first
     ChannelData in each direction, and LEAVE_ROOM.
   - **How to read it:**
     - LEAVE ≈ t0 + 15 s and no ChannelData → gate (a). The TURN route never reached state 2 on that client.
     - LEAVE ≈ (both channels bound) + 10 s → kps timeout (b).
     - A ChannelBind retransmit after our "success" → the client never accepted it.
2. **Fix run.**
   - Send the proto_opt above and the double-push.
   - **Proof of a relay link-up:** relayed ChannelData in both directions carrying the kps first exchange. Payloads are
     about 14 bytes one way and about 266 bytes back (5-byte failover header + 2 route bytes + 7- or 259-byte dlcp
     record, misc.md §Q4).
   - Then both clients send SEND_MATCHING_RESULT / SET_GAMERELAY_CHECK_RES with `error_code` "" / NOERR.
   - **Stop there.** No match play is needed.
   - If it still fails, the failure time tells which timer fired: about t0 + 40 s → gate; about bind + 30 s → kps.

## Confidence
- **High:**
  - the polling chain to 0x14122ef60;
  - the kick-off gate, its 15 s source and its line-643 log;
  - "kps starts only when failover == 2";
  - "TURN alone gives failover 2";
  - the first-send TURN copy;
  - the Session.ConnectionTimeoutUs 10 s default;
  - "relayed_addr is not used by the ktc route".
- **Medium:**
  - that ktc channel state 3 = ChannelBind accepted;
  - that the TURN connect starts at strategy init;
  - which of (a) or (b) fired in the 2026-11-06 run (the END loose end).

---

## Follow-up after the 16:32 relay run (relaymatch2.log)

### What the run shows

The run's facts:
- START at 46.44.
- Bind at 47.1.
- MXNV_0013 at 77.46, which is bind + 30.3 s.
- No END.
- No ChannelData.

### Answers

**1. kps does not wait for NTL. It started at the bind.**
- The 30 s timer is `P2P.Session.ConnectionTimeoutUs`. It is read only inside kps start 0x141245a50
  (0x1411a91a0 ← 0x141245b74; no other caller) **[static]**.
- So kps was started at about 47.1. That happens only when the failover reports 2 (WithTurn update
  0x14122f544..0x14122f572) **[static]**. With no END, the P2P side was not up. So the TURN route alone gave
  state 2: the ktc channel reached state 3 (0x1411ef520) **[static gate + live timing]**.
- No NTL link is needed. Nothing else (CreatePermission, refresh, peer data) was missing.

**2. Why kps sent nothing: the msp role is chosen by sorting address strings, and both sides chose "passive".**
- msp session start is **0x141ff53e0** (called via 0x141ff4b80 from kps start 0x141245ddf) **[static]**:
  - The address list must have **exactly 2** entries (0x141ff542e..0x141ff5448). This path is 1v1 only.
  - It is `std::sort`ed as strings (0x141ff3a70 → 0x141ff32a0; std::string compare is memcmp then length).
    The entries are `ip:port` text, `[ip]:port` for IPv6 (formatter 0x1411daca0).
  - Own entry = std::find of the local label (0x1414b3c70). **Node id = index in the sorted list**
    (0x141ff5490..0x141ff558c). Peers go into the map at +0xa0.
- Link set-up is in **0x141ff55e0** (from kps wrapper vt+0x28 0x141246070) **[static]**:
  - For each peer: `if self_id < peer_id`, it opens the link actively (0x141ffbf40 at 0x141ff56ff). That is the
    side that sends the first dlcp KX datagram. Idle state Send 0x142049650 → KeyExchangeActive 0x142049ba0.
  - Otherwise it only arms the passive path (r12b, 0x141ffdc50 at 0x141ff5871). Idle state Receive
    0x142049220 → KeyExchangePassive 0x14204abd0.
- **What the labels were** **[inferred]**:
  - Local label = own host IP:port. The IP comes from OnlineSystem+0xa8 via 0x14116e3c0 in SetConfig 0x141144e7a.
  - Peer label = the address the server gave for that peer (SetConfig 0x1411451d9..0x1411452c8).
  - Both games saw `"192.0.2.1:9"` < `"192.168.1.100:…"`. So **both were node 1, both passive, and nobody ever
    sent**.
  - With real addresses each side's label for the other equals that side's own label, so the order is consistent.
    That is why P2P works.

**3. Is a relay-only match possible? Yes.**
- No proto_opt key or reply field changes the role rule.
- The server only has to give **order-consistent labels**: exactly one side must see its own local label sort
  **before** its peer's label.
- The two timeouts can stay raised. The other parts of the recipe (pushes, DscpEnable:false) already worked.

### Server recipe (replaces "leave the addresses" for forced relay)
- Pick an active side, say the room owner O, and the other side G. Let `L_O` and `L_G` be their own host_addr
  `ip:port` (what each game uses for itself) **[inferred]**.
- The label O sees for G must sort **after** `L_O`. The label G sees for O must sort **before** `L_G`.
- Set both host_addr and reflexive_addr in REQWATCHL_ROOMPLAYERLIST_NTL and JOIN_ROOM_NTL.
- For this PC (locals 192.168.1.100):
  - G as seen by O = `203.0.113.1:9`;
  - O as seen by G = `192.0.2.1:9`.
  - Both are TEST-NETs, so the P2P copies go nowhere harmful.
- **Warning for real WAN matches (P2P too):** labels are whatever the server sends, so two players can both end up
  "higher" and deadlock. For example: local 192.168.x, with the peer shown as a public 1xx.x address on both
  sides. The server should always check `(L_A < X_B) == (X_A < L_B)` and fix the labels it sends when that fails
  **[inferred from the rule above]**.

### Live test (stop at the first signal)
- Use the same run, with the labels above.
- **Proof:** right after the ChannelBinds (≈ 47.1 s), the relay forwards ChannelData from O first. The payload is
  about 14 bytes (5 failover + 2 route + 7 dlcp KX). About 266 bytes come back from G.
- Then SET_GAMERELAY_CHECK_RES / SEND_MATCHING_RESULT go out without MXNV_0013. Stop there.
- If it is still silent, the local label is not host_addr. Swap the two labels, or use `0.0.0.1:9` / `240.0.0.1:9`
  as the low and high labels, and retry.

## The 5-minute drop and the rematch that never connects (live, 2026-11-14) **[LIVE]**
- First real WAN match over the relay (host on the LAN, friend on the internet). It dropped with `DISCONN_P2P`
  (`GNCX_0003_0x20000000`) on both sides at the same moment, twice, each time **300 s after the ChannelBinds**.
- Caught on the second run: the server's packets out to the far player fell from ~55/s to 0 between bind + 300.03 s
  and bind + 300.29 s, while his packets kept coming in. Both games sent ABNORMALEND 15 s later.
- Cause: the game sends one ChannelBind per side and after that **only Refreshes the allocation** (every 30 s,
  LIFETIME 60). It never sends CreatePermission and never re-binds. Our relay used the RFC timers (permission 300 s,
  channel 600 s), so the permission ran out mid-match and the relay dropped everything.
- Fix (turnrelay.py `Allocation.renew`): every Refresh also renews the allocation's live permissions and channels.
- Second symptom: a rematch ~90 s later got **the same relay ports** as the dead match (lowest free port first). Both
  games published their TURN address, sent no ChannelBind, and quit after 15 s with `ER_MATCHING_RETRY`, then
  ABNORMALEND `DEGRADE_SC` `EYRH1530`. The next try, on new ports, bound at once. The guess is that the game
  still has the old peer address cached **[inferred]**. Fix: relay ports go out least-recently-freed first.
- Both fixes are not yet tested in a match (the drop itself was caught live).
