# Group "nettune": link tunables, P2P↔TURN failover, ntl_nat_type, the NAT test — PES 2021 1.01.00 (static RE)

Working files (asm dumps, helper scripts): the working folder (not published) (netparams.asm, netdefaults.asm, turnio_range.asm,
nattest_range.asm, natsm.asm, ia_result.asm, ntl_all.asm, withturn_setparams.asm, getters.txt, proto_opt_example.json).
Tags: **[static]** read in code (address given), **[log]** seen in logs/*.log, **[inferred]**, **[guess]**.

## Overview (headline results)

1. **The "multiplay parameter tree" is not a data file. It comes from the server**: it is the JSON text in
   `CMD_GET_SERVER_ENV.proto_opt`. The parser stores it at Result+0x1d0. `0x141210dd0` passes it to `0x141174b20`, which
   writes it into `onlinesystem::ParameterMultiplay` (RTTI, OnlineSystemManager+0x40, ctor 0x141149970). That object holds a
   `onlinesystem::JsonData` (JsonCpp-style Reader at 0x140387bf0; getters 0x141174700 obj / 0x141174440 int /
   0x141174800 bool / 0x141174780 int64 / 0x1411744c0 double). Nothing else writes ParameterMultiplay. No CPK file holds
   these values: the listings show only `movie/fade/settings.json` and `anim_infos.json` [static]. Our server sends no
   `proto_opt`, so the parser default `"NotImplement"` stays. That text is not valid JSON, so the parse flag (JsonData+0x20)
   is 0, every getter returns "absent", and **the built-in code defaults apply** [static 0x141174910, 0x141174700].
   → **Our server can tune every value below by sending `proto_opt` (JSON text).** Konami's real values are lost (they lived
   only on their server). Nothing in the logs shows them.
2. **Default timeouts explain the "~30 s then ER_MATCHING_RETRY" failure**: `Network.NetworkIoConnectionTimeoutUs`,
   `ProtocolTransmission.ConnectionTimeoutUs` and `Domain.LinkTimeoutUs` all default to **15 s**. The match task retries
   once (wait 15 s, 0x141143ccd), so two attempts take about 30 s. That matches the 16:28:48 → 16:29:18 log. Raising these
   through proto_opt gives hole punching more time [static defaults; the causal link is inferred].
3. **The P2P↔TURN failover (P2pTurnIoFailover) starts in P2P mode.** It switches to TURN only while the TURN route is up.
   It switches when one of these holds: the P2P link is not connected; P2P has never received; P2P has received nothing for
   ≥ 7.5 s while TURN has received within 7.5 s; or P2P has been "buffer-critical" for ≥ 10 s. It switches back to P2P
   when P2P has received within 7.5 s again. Rules and addresses are below.
4. **ntl_nat_type 0x10307533 decoded**: Konami bit field, not an RFC enum. Today it reads: UDP connectivity OK, port
   preserved, port parity preserved, UPnP IGD found + external IP + port mapping, STUN-mapped ≠ UPnP-external, NTL config
   flag 0x10000000 — **filtering = "3" (worst / unknown) because our STUN never lets the filtering tests run**; hairpin and
   mapping-behaviour tests also never ran. Value `1` = "no NAT test result yet" (a global default).
5. **The NAT type does not gate P2P in the client.** I found no client code that refuses or changes the P2P/TURN strategy
   from it. The strategy factory uses only the TURN list. Inside NTL only bit 0x10 (connectivity) is touched again (by
   "RevokeConnectivity"). The value is reported to the server (`CMD_SEND_IPANDPORT_NTL`), presumably for Konami's
   matchmaking. No "NAT Type" UI string exists in the exe [static, exhaustive string search]. So skipping the NAT tests
   costs us nothing functionally [inferred, medium-high].
6. **A complete NAT-test server** answers RFC 5780 style: MappingTestIA must carry OTHER-ADDRESS (0x802C) and a Konami
   0xF000 attribute with an `O573_CR_XADDR` address (its port is used). Without them, MappingTest II/III, FilteringTest I/II
   (and others) are marked "skipped" (state 4) and never sent. **The logs confirm: only the 32-byte IA probe ever reached
   our STUN** (30×), besides the 10 398 keepalives.
7. SC/CsClient game relay: implementing it means re-implementing kps dlcp+lltcp+msp *and* being a match endpoint.
   **TURN is strictly better** (standard coturn, opaque relay, already wired into the client).

---

## 1. Multiplay parameters (`proto_opt` JSON)

### CMD_GET_SERVER_ENV.proto_opt — JSON tuning tree for the multiplay stack
- Transport: gate (existing command; reply field `proto_opt` STR). The client never sends anything back about it, except
  `PROTO_OPT_HASH` (0x141219b50 of the text) in the post-match stats record (0x1411b7490) [static].
- When: GET_SERVER_ENV is processed in 0x141210dd0 → `ParameterMultiplay::SetJson` 0x141174b20 → 0x141174a00 (hash) →
  0x141174910 (parse; success flag at JsonData+0x20). It is read each time a multiplay session is created:
  0x1411aa730 (CreateMultiplaySession) → 0x1411a6b10(params, **type** = cfg+0x18, **level** = cfg+0x20) [static].
- **Tree shape** (0x1411a76d0): root object → *profile* object → *group* object → keys.
  - Profile chosen by session type: 0 → `"CS"`, 1 → `"P2P"`, 2 → `"P2P_ADHOC"` (3/Observe → no lookup).
  - Level profile chosen by level: 0 → `"<P>_HIGH_LEVEL"`, 1 → `"<P>_LOW_LEVEL"`, 2 → `"<P>_GIVE_UP_QUICKLY_LEVEL"`
    (`CS_*`, `P2P_*`, `P2P_ADHOC_UNKNOWN_*`). Other readers also know `P2P_ADHOC_{LAN,BLE,BTC,WIFIDIRECTLAN}_*`.
  - Order: code defaults (0x1411a7890) → `root[profile][group]` → `root[levelprofile][group]`. Later layers override earlier
    ones key by key. A missing key keeps the earlier value.
  - Which level Quick Match uses is **not determined** (cfg+0x20 is filled upstream of 0x14115c170). Put values in the base
    `"P2P"` profile so they apply at every level.
  - Value types: ints must be JSON integers (isInt check 0x1403854b0); bools JSON true/false; doubles → float.
- Groups and keys (reader function → keys; full lists in the working folder (not published)) [static]:
  - `Network` (0x1411a6c00): table below.
  - `NatTraversal` (0x1411a6720): DoNotGiveUpMode (bool), BackgroundTimeout, DoesBlockSocketError, EnableSocketErrorLog.
  - `Session` (0x14119d4f0 / 0x1411a8c30): ConnectionTimeoutUs.
  - `Domain` (0x14119db80): FinWaitTimeoutMs 1000, TimeWaitTimeoutMs 500, PingIntervalUs, MaxNumberOfPingRequest,
    PingRequestTimeoutUs, LinkMode 1, **LinkTimeoutUs 15 000 000**, LinkUpMode 2, **KeyExchangeTimeoutUs 20 000 000**,
    KeyExcahngeRetransmissionTimeoutUs (sic), KeyExchangeEncryptionAlgorithm/Key, SessionKeyEncryption*. Defaults are from
    0x14119e390; the offset mapping is medium confidence.
  - `ProtocolTransmission` (0x14119c1f0): **ConnectionTimeoutUs 15 s, EstablishedConnectionTimeoutUs 15 s** (defaults
    0x14119d380), KeepAliveTimerUs, congestion control (FastTcp/ScalableTcp, DefaultPps/MinPps/MaxPps,
    MaxSendOctetLengthPerPacket, RTO estimator …).
  - `ProtocolChannel` / `All` (0x14119b2c0): channel datagram length, FEC (MediaSpecific/Parity), queues.
  - `MatchCommandBufferingControl` (5 variants 0x1411a0240…0x1411a3760): input-delay buffering (MinBufferSize,
    MaxBufferSize, CommandHz, Correction*, Buffer{Warning,Critical,Fatal}*).
  - `Match` (0x1411a4ae0): LoadTimeoutMs, OutOfPlayTimeoutMs, InitializationTimeoutMs, CommandLackTimeoutMs,
    SuspendTimeoutMs, ActualMatchHz* …
  - `MultiplayStatistics` (0x1411a5aa0), `OnlineSystem` (0x1411a7c70: AccessLineTester*, NetworkTester*, VoiceChatEnable,
    Observe*, AvatarEnable …), `MatchCommand.Priority` (0x14119fe80).

**`Network` group: offsets (struct filled by 0x1411a6c00, optional<T> = flag byte + value) and code defaults
(0x1411a7890)** [static]:

| key | type | default | notes / consumer |
|---|---|---|---|
| DscpEnable / Dscp | bool / u8 | true / 46 (EF) | IP TOS of the game UDP socket [inferred] |
| TcpFallbackEnable | bool | **false**; **true for CS** (type 0) | [inferred] relay link falls back to TCP |
| TcpFallbackTimeoutMs | int | 10000; **3000 at GIVE_UP_QUICKLY** | |
| MaxPayloadLength | int64 | 1196 | max UDP payload (fits TURN overhead) |
| NetworkIoConnectionTimeoutUs | int64 | **15 000 000** | NTL/NetworkIo connect timeout [inferred] |
| TurnReconnectWaitTimeMs | int | 5000 | used by AutoChangeover/Backup setup (0x14122df8b) |
| NtlReconnectWaitTimeMs | int | 5000 | → failover cfg +0x24 |
| ChangeoverPeerReceiveIdleTimeMsThreshold | int | **7500** | failover cfg +0x28 ([fo+0x378]) |
| ChangeoverToTurnReceiveIdleTimeMsThreshold | int | 10000 | cfg +0x2c; not read by the Failover decision code (AutoChangeover/Backup) |
| ChangeoverToTurnBufferCriticalConditionTimeMsThreshold | int | **10000** | cfg +0x30 ([fo+0x380]) |
| ChangeoverToP2pFromBufferCriticalConditionTimeWaitMs | int | 5000 | cfg +0x34; not read by the Failover decision code |
| P2pModeTurnSendEnable | bool | true | cfg +0x38 ([fo+0x388]) |
| P2pModeTurnSendIntervalMs | int | 5000 | cfg +0x40 ([fo+0x390]) |
| TurnModeP2pSendIntervalMs | int | 1000 | only reaches cfg +0x58 when TurnNetworkIoPingIntervalMs is absent (compiler-merged cmov at 0x14122dc83..dd38; looks like a source bug) |
| P2pBufferCriticalConditionTimeMs | int | 30000 | cfg +0x44 ([fo+0x394]) |
| TurnEnableDualRouteModeTime | int | 15000 | cfg +0x48 ([fo+0x398]) |
| TurnBufferCriticalConditionTimeMsMax / Coef / AddConst | int | 60000 / 10000 / 0 | cfg +0x4c/+0x50/+0x54 |
| TurnNetworkIoPingIntervalMs / QuickPingIntervalMs | int | 5000 / 1000 | TURN-route RTT probing |
| TurnNetworkIoCheckDegradationThresholdRttCount | int | 3 | |
| TurnNetworkIoTcpConnectionTimeoutMs / ConnectionTimeoutMs | int | **10000 / 10000** | TURN TLS-over-TCP connect must finish in 10 s |
| TurnNetworkIoReconnectServerTimeWaitMs | int | 1000 | |
| TurnNetworkIoMigrateTimeWaitMsMax | int | 90000 | dual-route / migrate |
| TurnNetworkIoEwmaSmoothCoef / EwmaPenaltySmoothCoef | double→float | 0.25 / 0.25 | |
| TurnNetworkIoRecentlyRttMaxSize | int64 | 5 | |
| TurnNetworkIoDegraded{Judgement,ByDisconnect,ByLatency}Enable | bool | true ×3 | |
| TurnNetworkIoDegradedLatencyRatio | double→float | 0.5 | |
| TurnNetworkIoRttSuddenChangeThreshold | int | 100 | |
| DcTestAddress | string | "" | "DC test" = data-centre latency test; unused while empty [inferred] |
| DcTestConnectionTimeoutMs / RecvTimeoutMs / PingTimeoutMs / NumWorker | | 60000 / 10000 / 5000 / 5 | |

- Server should: send `proto_opt` only once a value really needs changing. Start with
  `{"P2P":{"Network":{"NetworkIoConnectionTimeoutUs":30000000}}}` to test whether a longer connect window helps NAT-hard
  pairs. The other 15 s timeouts are `ProtocolTransmission.ConnectionTimeoutUs` and `Domain.LinkTimeoutUs`; they may need
  the same treatment. Invalid JSON is harmless: all defaults stay. Keep `proto_opt` small (gate frames > ~1 KB need 0x70/71/72
  fragmentation, see wire.md). Example: the working folder (not published).
- Confidence: high (source of the tree, shape, Network defaults); medium (Domain/ProtocolTransmission defaults, which
  timeout governs the 30 s failure).

## 2. P2P↔TURN failover rules (`onlinesystem::P2pTurnIoFailover`, vt 0x1428b9a90)

Used when the TURN list is non-empty (strategy P2pFullMeshWithTurn, io type 3; factory 0x14124ac50). Parameters are
copied from the Network struct by WithTurn setup 0x14122db10 (rbx = Network struct) into a 0x90-byte cfg via
0x14124a720 → 0x141252a10 → 0x141250a50. The cfg sits at fo+0x350 (failover-own defaults ctor 0x14124fe40) [static].
- Mode `[fo+0x3e4]`: 0 = P2P (initial, ctor 0x141250375), 1 = TURN. Changeover = 0x141250ca0(fo, newMode, reason).
- Per-route "last receive" stamps: route A [fo+0x450/0x458], route B [fo+0x468/0x470]. They are updated by 0x141253cb0
  from header fields of each received failover packet (0x14125a460 in recv/update 0x141251e20), so the peer's view takes
  part [static]. 0x1412510e0 returns (now−A, now−B), or −1 if never.
- **In P2P mode (0x141253850)**, evaluated only when the TURN route is up (base state == 2, 0x1412495e0):
  1. P2P link state ≠ 2 (not connected / no NTL io) → **switch to TURN, reason 1**.
  2. P2P buffer-critical time (0x141250fc0) > `ChangeoverToTurnBufferCriticalConditionTimeMsThreshold` (10 s) and TURN
     usable (0x141251a20) → **reason 3**. A counter `[fo+0x3e8]` counts these switches. It resets to 1 if the last one was
     more than `P2pBufferCriticalConditionTimeMs` (30 s) ago.
  3. TURN idle < `ChangeoverPeerReceiveIdleTimeMsThreshold` (7.5 s) and (P2P never received → **reason 4**, or P2P idle
     ≥ 7.5 s → **reason 5**).
- **In TURN mode (0x141253a10)**, evaluated only when the P2P link state == 2:
  - If the last switch was buffer-critical (counter > 0): wait `min((n−1)·Coef + AddConst, Max)` = 0 s, 10 s, 20 s … ≤ 60 s
    since the switch before going back. If TURN itself is buffer-critical longer than `TurnEnableDualRouteModeTime`
    (15 s), set dual-route flag `[fo+0x400]` (send on both routes) and stay.
  - P2P idle < 7.5 s → **switch back to P2P, reason 6** (counter−− if TURN unusable).
- Keep-alives across routes (send path 0x141252740): in P2P mode, if `P2pModeTurnSendEnable`, something is also sent over
  TURN every `[fo+0x38c]` (5000 ms, fixed default). In TURN mode, P2P is probed every `[fo+0x390]` =
  `P2pModeTurnSendIntervalMs` (5000 ms). Changeover reason and time go to stats `TURN_CHANGEOVER_REASON/TIME`
  (0x1411ba005).
- Implications for the server [inferred]: a NAT-hard pair connects through TURN as soon as both allocations and the peer
  permission are up (reason 1). Because the failover is the kps SocketInterface, the kps link-up itself can run over TURN.
  The TURN server must keep allocations alive for the whole match: keep-alives every ~5 s, refreshes, lifetime ≥ 600 s is
  fine.
- **WithTurn init also requires exactly one element in config vector +0x170** (`cmp rax,1; jne fail`, 0x14122eab1). The
  0x40-byte entries hold two strings. If this is the peer list, TURN mode supports **1v1 only**: co-op with a TURN list
  would fail with "Error CreateMultiplaySession" [static check; meaning of the vector is a guess → test with co-op].
- Confidence: medium-high for the rules (structure clear); medium for the exact parameter↔field mapping at +0x3c/+0x40/+0x58.

## 3. ntl_nat_type — the packed value

Built by the NAT-test evaluator (end of state machine 0x1418d979f, `[ntl+0x31c]`). Read through GetNatType 0x1418e5f50 →
0x1418d5f50 [static].
- **0 internally ⇒ reported as the global default** `0x1437bfd58` (set by 0x1418dddb0; logs show **1**). That is "not
  tested / no result yet".
- **NAT test failed** (IA got no answer): value = `((v & 0x800000) | 0x1000000) >> 23` = **2 or 3** (0x1418da58a).
  GetNatType leaves 0/2/3 unchanged.
- Otherwise bits:

| bit | meaning | set where |
|---|---|---|
| 0x0000_0003 + 0x20 | **filtering class**: 0x20 = classified; low bits 3 = no answer to FilteringTest I and II (address+port-dependent / unknown), 1 = FT-I fail but FT-II ok (address-dependent), 2 = FT-I ok, FT-II fail. Both answered → no 0x20 bits (endpoint-independent) | 0x1418da67f |
| 0x0000_0010 | **UDP connectivity** (MappingTestIA answered). Cleared by "RevokeConnectivity" when keep-alives fail (0x1418e48e9) | 0x1418da572 |
| 0x0000_0040/0x44/0x4c | mapping-behaviour classes from comparing the mapped addresses of extra tests (0x2000 test) | 0x1418da5cd |
| 0x0000_0080 | IA mapped address is a local interface address (no NAT) — only if mask 0x100 configured | 0x1418dc775 |
| 0x0000_0100 | UPnP AddPortMapping succeeded | obfuscated callback 0x15afe4dc0 (via 0x1418d6da0) |
| 0x0000_0200 | UPnP check 0x1418f76e0 on local IP (unclear) | same |
| 0x0000_0400 | UPnP external IP obtained (also fills ntl+0x21c) | same |
| 0x0000_1000 / 0x2000 | UPnP IGD found / UPnP 0x1418f7650 ok | same |
| 0x0000_4000 | STUN-mapped address ≠ UPnP external address | 0x1418da826 |
| 0x0001_0000 | **hairpin works** | 0x1418da7ad |
| 0x0002_0000 | mapping varies between tests (endpoint-dependent = symmetric) | 0x1418da718 |
| 0x0004_0000 | IA vs IC mapped address differ | 0x1418da89a |
| 0x0008_0000 | MAPPED-ADDRESS ≠ XOR-MAPPED (ALG rewriting?) [guess] | 0x1418da78a |
| 0x0010_0000 | **port preserved** (mapped port == local port) | 0x1418da764 |
| 0x0020_0000 | port parity preserved | 0x1418da73e |
| 0x0040_0000 / 0x0080_0000 | further extra-test results (0x8000 / IV test) | |
| 0x0100_0000 | TURN client state 6 (added by getter 0x1418d5f7c) | |
| 0x0200_0000…0x4000_0000 | NTL config flags added by GetNatType 0x1418e6026..6087 (cfg+0xc → 0x1000_0000, cfg+0x10 → 0x2000_0000, cfg+4 bits → 0x0200_0000/0x0400_0000/0x4000_0000, 0x1418e91e0 → 0x0800_0000) | |

- Decoding of **0x10307533** [static+log]: 0x10000000 (cfg flag) | 0x200000 (parity) | 0x100000 (port preserved) | 0x4000
  (mapped ≠ UPnP external: our STUN sees the LAN IP, UPnP reports the WAN IP) | 0x2000|0x1000|0x400|0x100 (UPnP) | 0x20|0x3
  (filtering "3") | 0x10 (connectivity). Hairpin, mapping-variance and the ID/IE/IF/IG/IV results are absent.
- Which classes "can't connect": the client never decides this. No branch on these bits was found outside logging.
  Konami's matchmaker probably avoided pairing two players with filtering 3 + mapping-varies (0x20000) [guess]. **Our
  matchmaker can ignore ntl_nat_type**, or use `nat & 0x20000` (symmetric) on both sides as a hint to prefer TURN.
- The game shows no NAT type to the player: no such string in the exe (the menu .bin files were not checked).

## 4. The NAT test (what the client sends and expects)

Test plan: 14 test objects (table 0x142a65060: id, phase; sequence 0x142a650d0). They run phase by phase (0–10,
`[ntl+0x35c]`), and only those enabled by the start mask `[ntl+0x340]`. The classification flags are `[ntl+0x350]` [static].

| id | test | phase | request (from vararg lists of sender 0x1418dd0a0) | destination |
|---|---|---|---|---|
| 0x10000 | MappingTestIA 0x1418d8290 | 1 | Binding + `0xF000 {O573_ID=0x0573 u16, O573_REV=0 u16, O573_CRD_REQ=2 u32}` = `0573 0000 00000002` (32 B) [log] | primary STUN (UDP 3478) |
| 0x20000000 | MappingTestIV 0x1418d9220 | 1 | plain Binding | primary |
| 0x20000/0x2000000/0x8000000 | IB / ID / IF (0x1418d84c0) | 2 | Binding + RESPONSE-PORT(0x27){u16 port, PAD16} | needs OTHER-ADDRESS |
| 0x40000/0x4000000/0x10000000 | IC / IE / IG (0x1418d89b0) | 3 | like above | needs OTHER-ADDRESS |
| 0x80000 | MappingTestII 0x1418d8bc0 | 5 | plain Binding, new channel | **OTHER-ADDRESS IP : primary port** |
| 0x100000 | MappingTestIII 0x1418d8ef0 | 5 | plain Binding | OTHER-ADDRESS IP:port |
| 0x200000 | FilteringTestI 0x1418d49a0 | 5 | Binding + CHANGE-REQUEST(0x0003)=6 (change IP+port) + `0xF000{0573,0000,O573_CR_REQ=4,CHANGE_ADDRESS=4}` | channel to `[ntl+0x19c]` (from IA's 0xF000 reply) |
| 0x400000 | FilteringTestII (code obfuscated) | 5 | [inferred] CHANGE-REQUEST = 2 (change port) | same |
| 0x800000 | HairpingTest 0x1418d6f00 | 5 | Binding with own XOR-MAPPED(0x20)/REF_ADDR attrs | **own mapped address** (no server) |
| 0x1000000 | 0x1418d93d0 | 5 | ? | |

- **IA reply handling** (0x1418dc3fe) [static]: success (class 0x100) or error (0x10) accepted. Attributes:
  `0x0020`/`0x8020` XOR-MAPPED → test+0x10 (mapped); `0x0001` MAPPED → test+0x90; **`0x802C` OTHER-ADDRESS →
  ntl+0x11c** (only from IA); **`0xF000` → ntl+0x19c**. The 0xF000 reply is parsed by 0x1418f0cc0 as
  `u16 O573_ID, u16 O573_REV, 4 bytes, STUN address "O573_CR_XADDR"` (address parser 0x1418f0880). In IPv4 mode only its
  **port** is kept; the IP stays the primary server IP. Unknown attributes are logged "Skip Attr" and ignored.
- If ntl+0x11c / +0x19c stay empty (0x1411d6f00 = IsEmpty), the dependent tests set state 4 ("skipped", e.g.
  0x1418d8e82) and send nothing. The evaluator counts state 4 as executed + failed, so the run completes with event
  0xe5000701 → that is our 0x10307533 [static+log].
- Timeouts: per request 500 000 µs (0x1435163f8) × retries `[ntl+0x338]` = 3 (0x143516060).
  Keep-alive (UHP_KEEPALIVE-style 64-B Binding with 0x9090/0x9091) every 5 s [log].
- **GateInfo.php** (parser 0x1418e5b77) [static]: text lines `KEY: value`. `STATUS` must be **200**; otherwise NTL marks the
  API failed (ntl+0x15c=1, 0xe000003). `API_STATUS` 0 → done. If `API_STATUS`≠0 and `LOG_ACTIVE`≠0 and `PUT_LOG_URL`
  (< 256 chars) is present → **NTL uploads its logs** by POST `type=%s&prefix=…&ver=%d&dat=` to that URL.
  `SERVER_TIME` → global 0x1437bf674. Our reply (200/0/0/time) is correct. Debug option: `API_STATUS: 1`, `LOG_ACTIVE: 1`,
  `PUT_LOG_URL: http://<our server>/ntllog` would make clients send NTL debug logs (useful for UHP failures) [inferred].
- **What a complete server would answer**: RFC 5780 behaviour discovery on two IPs × two ports (A:3478, A:3479, B:3478,
  B:3479). IA reply: XOR-MAPPED + MAPPED + OTHER-ADDRESS(B:3479) + RESPONSE-ORIGIN + `0xF000 {0x0573, 0x0000, 4 bytes 0,
  O573_CR_XADDR = A:<filter port>}`. Honour CHANGE-REQUEST (answer from B and/or the other port) and RESPONSE-PORT. With one
  public IP only, OTHER-ADDRESS cannot be honest: leave it out (as docs/protocol.html already advises). Sending only
  the 0xF000 address would let the FilteringTests run. **Exact 0xF000 reply wire layout (the 4 bytes between REV and the
  address; whether the address is XOR'd) is not proven** → do not ship a guess. A wrong attribute only risks "Skip Attr" or a
  bogus FilteringTest destination.
- **Effect on matchmaking/connection**: none found in the client. Filtering "3" and the missing hairpin bit only change
  the reported number. Hole punching (UHP_CONNECT to host + reflexive candidates) runs regardless. **Recommendation: leave
  the STUN server as it is (IA only).** The real lever for NAT-hard pairs is TURN (net.md) plus the proto_opt timeouts.
- Confidence: high for the test plan, the IA attribute handling and the bit decode of our value; medium for the meaning
  of the extra tests (ID/IE/IF/IG/IV, 0x1000000).

## 5. "SC" game relay (MultiplaySessionStrategyCsClient, session type 0) — overview

- Chosen only for session type 0, and type 0 comes with `game_sycom "SC"`. It uses the same kps stack as P2P:
  msp `CsClientSession` → LlDomain/Clltcp → dlcp (RC4 key exchange with static key, wire.md) → lltcp reliability/congestion
  → UDP to `ip_addr:port` from CMD_GET_GAMERELAY. Presets `CS_*` turn on `TcpFallbackEnable` (10 s / 3 s) [static
  0x1411a7b0b]. CS has its own observer/match-server pieces (MatchOnlineObserverForPesServer, DEGRADE_SC/DISCONN_SC reasons).
- A relay endpoint would need: the dlcp handshake and record layer (partly known), **the undecoded lltcp header** (seq/ack/
  window, congestion), the msp CS session protocol (join, membership, fan-out), and possibly the match-command semantics the
  server is expected to apply. That is several unknown layers with no traffic to learn from. **Not plausible at reasonable
  cost. TURN is strictly better**: standard, opaque to dlcp, already supported by the client's failover logic.

## Server action items
1. Optional: add `proto_opt` (JSON text) to GET_SERVER_ENV for tuning. Without it nothing changes. First experiment:
   `{"P2P":{"Network":{"NetworkIoConnectionTimeoutUs":30000000}}}` (and, if needed,
   `"ProtocolTransmission":{"ConnectionTimeoutUs":30000000}`, `"Domain":{"LinkTimeoutUs":30000000}`).
2. Keep STUN IA-only. Do not add OTHER-ADDRESS / 0xF000 replies unless a second IP/port is really served.
3. Ignore `ntl_nat_type` in matchmaking (or only log it; `&0x10` = UDP reachable, `&0x20000` = symmetric).
4. For TURN: allocations must survive ≥ 90 min with 5 s keep-alives. The TLS connect must finish in < 10 s. Expect a switch
   to TURN within a second or two of allocation when P2P is down. Test co-op with a TURN list (possible 1-peer limit).
5. Optional debug: GateInfo `API_STATUS:1 / LOG_ACTIVE:1 / PUT_LOG_URL:<url>` to collect NTL client logs.

## Still unknown
- The level (HIGH/LOW/GIVE_UP_QUICKLY, cfg+0x20) used by Quick Match. Settle by tracing 0x14115c170 / 0x1411fc460 callers,
  or by sending distinct values per level profile and watching behaviour.
- Which 15 s timeout ends the failed connect (NetworkIo vs ProtocolTransmission vs Domain link). Settle with a live A/B
  test using proto_opt.
- The 0xF000 reply layout (bytes 4–7, XOR or not), FilteringTestII (obfuscated), and tests 0x1000000 / ID–IG / IV.
  Settle with a capture of real Konami traffic (none available) or by emulating the parser 0x1418f0cc0 on candidate blobs.
- NTL config flags behind 0x10000000 etc. (cfg+0xc/+0x10/+4), and UPnP check 0x1418f76e0 (bit 0x200).
- The meaning of WithTurn's config vector +0x170 (1-entry requirement).
- The failover packet header fields (0x14125a460) that feed route idle times.
