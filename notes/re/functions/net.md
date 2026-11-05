# Group "net": P2P, NTL, TURN relay, game relay — PES 2021 1.01.00 (static RE)

## Corrections from later findings (2026-11-05)
- The "dual route" SOFTWARE directive is **required, not optional**. After allocating, Turn::Impl needs the relay's SOFTWARE to be `HOST:PORT,<text>,0`, otherwise it restarts the session every second. With it, the game opens a second leg over DTLS/UDP and only publishes its relay address once that works. (Live test, livetests.md.)
- The coturn run's instant disconnect was **not** caused by mobility: our relay granted mobility and the game still disconnected until the SOFTWARE directive was sent.
- Settled live: REQUESTED-TRANSPORT is UDP (0x11); the client sends MOBILITY-TICKET and SOFTWARE "ktc-0.0.0"; a failing TURN path does not break the match.
- The multiplay parameters come from the server (`GET_SERVER_ENV.proto_opt` JSON), not a game data file (nettune.md).


Working files (asm dumps, helper scripts): the working folder (dumps not published; general tools in `notes/re/functions/tools/`).
Tags: **[static]** read in code (address given), **[log]** seen in our traffic (logs/*.log), **[inferred]**, **[guess]**.

## How it works as a whole

1. **Login / mode entry**: the NTL library ("NTL 1.11.11-Windows64", `cobra\NatTraversal\NTL`) runs the NAT test against
   the STUN server (UDP 3478) + GateInfo.php, then UPnP. The client reports its addresses with
   `CMD_SEND_IPANDPORT_NTL` {host_addr, reflexive_addr, relayed_addr, ntl_nat_type}. [log]
2. **Matchmaking** pairs players. `CMD_WATCH_MATCHING` member blobs carry each peer's NTL addresses. [log]
3. **Game relay info**: `CMD_GET_GAMERELAY` (HTTP). `game_sycom` selects the transport: `"P2P"` or `"SC"`
   (server/client through the game relay). With "P2P" the gamerelay ip/port are **discarded**. [static 0x141092240]
4. **Match session task** (`OnlineModeTaskMatchSession.cpp`, task fn 0x1411430b0, state int `[task+0x70]`):
   GET_SERVER_ENV → **CMD_GET_TURN_SERVER_LIST** (state 9/0xa) → `SetConfig()` (0x141144d80, state 0xb) →
   CreateMultiplaySession (state 0x11, helper 0x1411444e0, which also starts the singleton **ConnectTurnTask**
   and registers the **CMD_WATCH_TURN_ADDRESS_DATA** watch, 0x14114e120) → wait for connection (state 0x15 polls
   `OnlineModeMultiplay[+0x5c4]`) → revision check → state 0x1a = connected. Error state = 0x17.
5. **Session strategy is chosen by whether the TURN list is empty** (factory 0x1411aa730):
   * TURN list **empty** (what our server does today) → `MultiplaySessionStrategyP2pFullMesh` = NTL UDP hole
     punching only. **No relay at all.** If the NATs don't allow a direct path the session fails → "Failed to
     connect with opponent" (EYRH1175-ish via `TaskQuickMatching::ActSubConnectWait`, or GAMERELAY_CHECK_RES
     `error_code ER_MATCHING_RETRY, failed_reason 2` after ~30 s [log]).
   * TURN list **non-empty** → `MultiplaySessionStrategyP2pFullMeshWithTurn` (debug var `turn_mode` default 0)
     which wraps the link in **`P2pTurnIoFailover`**: direct NTL P2P + a TURN relay path, switching between them.
6. **The TURN client is Konami's "ktc-0.0.0"** (namespaces `ktc::session`, `ktc::socket`, `ktc::lib::TlsSession`),
   a standard RFC 5766/8656 TURN client (Allocate / Refresh / CreatePermission / ChannelBind, 401+nonce long-term
   auth, 438 stale nonce, 437 allocation mismatch, 405 "Mobility forbidden"), linked against OpenSSL 1.1.0i.
   **It connects over TCP + TLS to `address:tls_port`** of an entry in the TURN list, authenticates with `id`/`pw`,
   trusts the DER certificate given in `server_certificate`.
7. Each client publishes its relay address with **CMD_SEND_TURN_ADDRESS_DATA**; the server must forward it to
   the opponent as push **CMD_WATCH_TURN_ADDRESS_DATA**; the peer then creates a permission/channel to it.
   Our server implements neither, so even with a TURN list the relay would not work yet.

```
client A                    our server (gate/HTTP)                 TURN server (coturn)             client B
GET_TURN_SERVER_LIST  ───►  {turn_server_list,id,pw,cert} 
TLS/TCP tls_port ─────────────────────────────────────────────►  Allocate(401→auth) → XOR-RELAYED A
SEND_TURN_ADDRESS_DATA{A} ─► push WATCH_TURN_ADDRESS_DATA{A} ─────────────────────────────────────────► B
                            ◄─ SEND_TURN_ADDRESS_DATA{B} ◄──────────────────────────────────────────── B
WATCH_TURN_ADDRESS_DATA{B}  ◄─
CreatePermission/ChannelBind(B relayed) ──────────────────────►  relays UDP A-alloc ⇄ B-alloc ◄─── B does the same
```

---

### CMD_GET_TURN_SERVER_LIST — TURN servers + credentials for the match
- Transport: HTTP web API, URL type `GET_TURN_SERVER_LIST`, form request, raw msgpack reply. [log]
- When: **every match**, twice per client ~250 ms apart: once by the match-session task (create at
  0x141143af1, result discarded, state 0xa→0xb) and once by `onlinesystem::ConnectTurnTask` (0x1411e2640), which only
  fetches if ParameterTurn's list is still empty (0x1411e24e0). Not at login. [static+log]
- Request: `pid`; `turn_account_param` = pid as decimal string (ParameterTurn::GetAccountParam 0x1411cc430, "%d" of
  `mgr->[0x10]+0x15c`); `turn_time_limit` = ParameterTurn+0x80, constant **60** (ctor 0x1411cbc3a / clear
  0x1411cbfff; no other writer found). [static 0x141212170, log: `turn_account_param=2&turn_time_limit=60`]
  Meaning [inferred]: hints for a TURN-REST style credential (username bound to the account, TTL 60 — seconds or
  minutes, unknown). The client never uses them itself.
- Reply (parser 0x141e4e650, apply 0x1412122a0 → 0x141238550 + 0x141238220, stored in `onlinesystem::ParameterTurn`
  = OnlineSystemManager+0x78, mutex at +0xd0):
  * `turn_server_list`: array of inline maps `{zone NUM, address STR, port NUM, tls_port NUM}` (Result+0x1d0 count,
    +0x1d8 array of 0x30-byte entries: zone@0, address@8, port@0x28, tls_port@0x2c). **An entry is dropped unless
    `port != 0 AND tls_port != 0`** (0x1412385a3..0x1412385b5). Kept entries → ParameterTurn+8 vector of 0x48
    `{u32 zone, u16 port @4, u16 tls_port @6, char address[0x3f] @8}` (0x1411cbf30). [static]
  * **Only `tls_port` is used to connect** (WithTurn 0x14122e8ca and TurnOnly 0x14122cffc pass entry+6 to
    0x1411f0e30). `port` is only a validity check. Servers are grouped in a map by `zone`; any zone value works,
    servers that fail are marked with a time stamp and skipped for a while (0x1411ef710). [static]
  * `id` (STR) / `pw` (STR) → TURN long-term-credential username / password (ParameterTurn+0x40 / +0x60,
    setter 0x1411cca20). Ignored if empty or `"NotImplement"`. **If either is empty the TURN connect is silently
    skipped** (0x1411f30a0..0x1411f30b9). [static]
  * `origin` (STR) → ParameterTurn+0x100 → ktc config (STUN ORIGIN attribute, RFC 7635-style tenant selector)
    [inferred from name]; optional — skipped if empty (0x141235660). [static]
  * `server_certificate` (STR): **base64 of a DER X.509 certificate** (decoder 0x1418574d0 = base64, ignores CR/LF —
    so a PEM body without the `-----BEGIN/END-----` lines works), decoded into a 0x800-byte buffer → ParameterTurn+0x88
    (0x1411cc960). `server_certificate_data_size` (NUM) = number of DER bytes (Result+0xa44). The TLS layer does
    `d2i_X509` (0x1421b43f0) → `X509_STORE_add_cert(SSL_CTX_get_cert_store(ctx), x)` (0x1421be560 / 0x1421b3c70,
    x509_lu.c) = **added as a trust anchor**; invalid DER aborts the TLS session. [static 0x142074565..0x1420746d6]
    Whether peer verification is enforced and whether the host name/IP is checked: **not determined**.
- Empty / bare `{result:NOERR}` reply (today): ParameterTurn list stays empty → strategy `P2pFullMesh` (no relay),
  ConnectTurnTask ends (state 5). Nothing errors. [static 0x1411aadb9, log]
- Errors: none found for a non-NOERR result in the match task (it does not read the result). [static]
- **Server should**: (see Recommendation) return one entry with the TURN server's public IP, `port` 3478,
  `tls_port` 5349 (both non-zero), `id`/`pw` valid for coturn, `server_certificate` = base64(DER of coturn's cert),
  `server_certificate_data_size` = DER length, omit `origin`. Only enable when a TURN server is really running.
- Confidence: high for parsing/selection/TLS-on-tls_port; medium for credential semantics.

### TURN client details (ktc-0.0.0) — what the TURN server sees
- Config built in Turn::Impl connect 0x1411ee050 (dl=1 from initial connect 0x1411f3040): server address+`tls_port`,
  username (+0x58), password (+0x78), origin (+0x98), CA cert (+0xb8), transport mode `[cfg+0x30]=1`.
  In ktc create (0x141235550): mode 1 → TCP stream socket (0x1412056d0) + `connect(address, port)` (vtbl+0x48) and a
  `TlsSession` with dtls=false (0x14206c760/0x141234d20); mode 0 → UDP socket bound to a local port (0x1411db950,
  vtbl+0x30) with the same TlsSession object (dtls flag = 1 → **DTLS**). [static]
- So the primary transport is **TURN over TLS/TCP** (RFC 5766 §2.1). Relay transport requested is presumably UDP
  (REQUESTED-TRANSPORT 17) [guess: not traced]. ktc defaults: lifetime 60, timers 60000/30000/10000 ms, 10 retries
  (0x142063635..0x1420636cc) [static, meaning inferred].
- Optional "dual route": after the TLS allocation succeeds, Turn::Impl reads a string from the ktc session
  (0x14205fab0, ≤0x78 bytes; which STUN attribute is unknown — candidates SOFTWARE, since the stats report has
  `TURN_SESSION_ATTRIBUTE_SOFTWARE`) and parses it as `host:port,<str>,<int>` (comma-separated, 0x1411ed0f0). If valid it
  opens a second UDP(DTLS) leg to that host:port (0x1411f2980 → 0x1411ee050 dl=0, 0x1411f2e08). A normal coturn
  SOFTWARE value ("Coturn-4.x 'name'") has no comma → no second leg [inferred]. Params `TurnEnableDualRouteModeTime`,
  `TurnNetworkIoMigrateTimeWaitMsMax`, stats `ROUTE_TCP_MS/ROUTE_UDP_MS` belong to this. Konami-specific extension.
- Server-side TURN error handling present: 401 (wrong credentials / unauthorized), 438 stale nonce, 437 allocation
  mismatch, 405 mobility forbidden, allocate/refresh/channel-bind retry limits (strings in 0x142067d90, 0x1420699c0).
- The **NTL library also has its own TURN client** (`ntl::TurnClient`, `## AllocTurnPort` 0x1418e2590, events
  ALLOC_TURN_PORT_*, Konami vendor attributes O573_*). It is what would fill `relayed_addr` in SEND_IPANDPORT_NTL.
  In every log `relayed_addr` is all zeros (family 0) [log]; I found no configuration path for it → treat as unused
  [inferred].

### CMD_SEND_TURN_ADDRESS_DATA — client publishes its TURN relayed address
- Transport: gate, client→server. Request (packer 0x141e4f850): `svrtype MENU, client NETCLIENT, pid, address,
  address_data_size`. Reply: `{result}` only.
- When: ConnectTurnTask step 2 (0x1411e28f0) once ParameterTurn+0xa0 (own address blob) is non-empty. The blob is
  set by WithTurn/TurnOnly (0x14122f790 / 0x14122da10) when the TURN client reaches state 2 (allocated), from
  Turn::Impl 0x1411ee9a0 (serialization of the ktc relayed address; byte format not decoded — treat as opaque).
  Non-NOERR reply → ConnectTurnTask fails (state 5), success → state 1/done. [static]
- **Server should**: store it per pid and push `CMD_WATCH_TURN_ADDRESS_DATA {result:"NOERR", address:<same bytes>,
  address_data_size:<same int>}` to the other member(s) of the room (kind 0x60, rqid 0, like the other WATCH pushes).
  Keep `address` byte-exact (msgpack str with non-UTF-8 bytes → surrogateescape, like `sa_data`). If the peer's
  address arrives before the opponent is in a room, queue it. Reply `{result:NOERR}`. Confidence: medium-high.

### CMD_WATCH_TURN_ADDRESS_DATA — server push: the opponent's TURN relayed address
- Transport: gate push. Watch registered at multiplay-session creation (0x14114e120). Reply fields (parser 0x141e4cc40):
  `address` STR (Result+0x1d0), `address_data_size` NUM (+0x1e8). **No pid field** — fine for 1v1; for >2 players the
  blob presumably identifies the peer (unknown).
- Handler 0x141212010: if size>0 → ParameterTurn::SetPeerAddress (+0xb8, 0x1411cc840). WithTurn polls it
  (0x14122f680) and gives it to Turn::Impl (0x1411f1220) → permission/channel to the peer's relayed address. [static]
- Server should: see SEND_TURN_ADDRESS_DATA. Confidence: medium-high.

### CMD_WATCH_PUNCHING_DATA — push with a peer's NTL addresses
- Reply fields: `host_addr`, `reflexive_addr` (objects {address_family, sa_data}), `ntl_nat_type`. Only referenced by the
  command factory; no quick-match user found. Probably lobby/room flows. Not needed for Quick Match. [static, low]

### CMD_SEND_IPANDPORT_NTL / CMD_GET_IPANDPORT_NTL — NTL candidates
- SEND (gate): `host_addr, reflexive_addr, relayed_addr` (each {address_family, sa_data}), `ntl_nat_type`,
  `file_hash_list(_num)`. Sent by onlinesystem 0x1411e3050 after the NAT test. [static+log]
- `sa_data` layout seen [log]: 14 bytes = port (big-endian) | 2 zero bytes | IPv4 | 6 zero, e.g.
  `61f3 0000 c0a80164 …` = 192.168.1.100:25075. address_family 1 = IPv4, 0 = empty.
- `ntl_nat_type` values seen [log]: `1` (prefix where NAT test had not completed / failed) and `0x10307533` after a good
  NAT test. It is a packed NTL descriptor (NTL logs it as `%08x`, "CoreNatTypeUpdate", "TurnInitializedNatType");
  byte meaning not decoded. **It does not influence the session-strategy choice** (factory uses only the TURN list,
  debug var turn_mode, and a "direct online" mode flag) [static 0x1411aa730].
- GET: reply {pid, ntl_nat_type, host/reflexive/relayed_addr}; no quick-match user found.
- **Important practical consequence [log+inferred]**: `reflexive_addr` is whatever our STUN server observes. When
  our server runs inside one player's LAN, that player's reflexive address is their **LAN** address (logs: host_addr ==
  reflexive_addr == 192.168.1.100). A player in another home then gets an unreachable private address for them, so
  NTL hole punching can only work if the remote side's packets happen to open the path. This alone can explain
  "Failed to connect with opponent" between homes even when both NATs are friendly. Mitigation without TURN: when
  pairing a LAN-side player with an internet player, rewrite the LAN player's `reflexive_addr` IP to the server's
  public IP (keep the port; works only if the router preserves source ports, which many do). TURN is the robust fix.

### CMD_GET_GAMERELAY — relay/transport mode for the match
- Transport: HTTP, URL type `GET_GAMERELAY`, request `pid`. Reply {gamerelay_index, ip_addr, port, grid, game_sycom}.
  Handler 0x1410bb4c0 → 0x141092240(matchinfo+0x58, game_sycom, {index, ip_addr, port, grid}).
- `game_sycom` is compared with a 2-entry table built at 0x140218e90: **index 0 = "P2P" → mode 1, index 1 = "SC" →
  mode 2**, anything else (incl. our "") → mode 0. Only for "SC" are ip_addr/port/index/grid kept; otherwise they are
  cleared (that is why the client's CHECK_RES carries `gamerelay {index 0, ip_addr "", port 0, grid 0}`). [static+log]
- "SC" = server/client: the match would go through the game relay with Konami's own protocol
  (`MultiplaySessionStrategyCsClient`, created for session type 0 in 0x1411aadf3) [inferred]. Our UDP 7600 has
  never received a packet in any log [log]. The gamerelay is therefore **not** a TURN-style fallback for P2P and not a
  latency probe in Quick Match; it is an alternative full relay mode whose wire protocol is unknown.
- **Server should**: keep `game_sycom "P2P"` (explicit, matches the client's own default in CHECK_RES; "" also worked
  [log]). The ip_addr/port values are irrelevant in P2P mode; UDP 7600 is not needed. Do **not** send "SC".

### CMD_GET_GAMERELAY_QUALITYCHECKLIST, CMD_SET_GAMERELAY_QUALITY, CMD_SET_GAMERELAY_MATCH_QUALITY
- QUALITYCHECKLIST (HTTP): reply {gamerelay_index, ip_addr, port, grid, quality, last_update, quality_list(_num),
  quality_check_data_list(_num), quality_threshold_1/2}; used by `TaskQualityCheckList` (0x14111ec80); SET_QUALITY
  (0x14111f790) reports {quality_list, gamerelay_index, quality, last_update}. A relay-quality measurement for SC mode.
  Never requested in our logs [log]. SET_GAMERELAY_MATCH_QUALITY: called from 0x14139d570 (lobby code). Bare NOERR is
  fine for all three. LOCAL_GAMERELAY / CMD_CHECK_LOCAL_GAMERELAY_PLAYER / CMD_SET_LOCAL_GAMERELAY_LIST belong to the
  lobby "LocalGamerelayDialog" (0x14138aa90). Not needed for Quick Match. [static, medium]

### CMD_SET_GAMERELAY_CHECK_RES / CMD_WATCH_GAMERELAY_CHECK_RES — connection result handshake
- SET (gate, from TaskQuickMatching 0x14111d430 / TaskCoopSession 0x141134110): {pid, game_sycom "P2P", error_code,
  gamerelay{…}, failed_reason, is_special_match_confirm}. Seen: `NOERR/0` on success; `ER_MATCHING_RETRY/2` when the
  connection did not come up within ~30 s (16:28:48 → 16:29:18) [log]; `ER_MATCHING_RETRY/0` instantly when the
  GET_GAMERELAY URL type was missing [log].
- WATCH push {error_code}: the server's verdict. Today we always push NOERR after both reported.
- Server should: push `error_code "NOERR"` only if both reported NOERR; otherwise push `ER_MATCHING_RETRY` so both
  clients go back to searching instead of one failing later [inferred]. Confidence: medium.

### Session strategies and failover
- Factory 0x1411aa730 (input: session type `[cfg+0x18]`: 0 CsClient, 1 P2P, 3 Observe):
  * if `(0x14149a470())->[+0xf0] == 2` ("direct online" mode, probably LAN/ad-hoc [guess]) → debug var
    `direct_online_turn_mode`: 0 Raw, 1 TurnOnly, 2 WithTurn.
  * else if ParameterTurn list empty → **P2pFullMesh** (NtlNetworkIo only).
  * else `turn_mode` (debug var, 0x141b34850, default 0): 0/2/other → **WithTurn**, 1 → TurnOnly, 3 → P2pFullMesh.
- WithTurn (`GQBD` file, init 0x14122e7c0): registers every list entry (zone, address, tls_port) with Turn::Impl,
  starts the TURN connect (0x1411f0bb0), creates P2P-TURN IO type 3 = **P2pTurnIoFailover** (factory 0x14124ac50:
  1 AutoChangeover, 2 Backup, 3 Failover). Init fails (→ "Error CreateMultiplaySession", MXNV431) if no entry could be
  registered. TurnOnly (`LISV`) uses the relay exclusively.
- Changeover thresholds are read from the local multiplay parameter tree, group "Network" (0x1411a6c00), not from the
  server: `ChangeoverPeerReceiveIdleTimeMsThreshold`, `ChangeoverToTurnReceiveIdleTimeMsThreshold`,
  `ChangeoverToTurnBufferCriticalConditionTimeMsThreshold`, `ChangeoverToP2pFromBufferCriticalConditionTimeWaitMs`,
  `P2pModeTurnSendEnable/IntervalMs`, `TurnModeP2pSendIntervalMs`, `TurnReconnectWaitTimeMs`, `NtlReconnectWaitTimeMs`,
  `TurnNetworkIo{Ping,QuickPing}IntervalMs`, `TurnNetworkIoTcpConnectionTimeoutMs`, `TurnNetworkIoConnectionTimeoutMs`,
  `TurnNetworkIoReconnectServerTimeWaitMs`, `TurnNetworkIoDegraded*`, `TcpFallbackEnable/TimeoutMs`. Values live in a
  game data file (not located). Failover logic (0x1412525a0…) has no strings; not traced in detail.
- Match-session retry: P2P connect failure with `[task+0xa8]==0` → one retry (`[task+0x78]` counter, wait 15 s =
  `cmp rax,0x3a98` at 0x141143ccd). State 0x15 results: 1 ok, 2 "Revison check TIMEOUT", 3 "Revision error",
  4 "DATAPACK error", 5 "Other user disconnected", else "UNKNOWN" (MXNV542). [static]

### m_TurnQuality* / TURN_QUALITY_* strings
- `m_TurnQuality*`, `m_TurnSession*`, `m_p2pTurnIo*` (0x142881c18…0x142882790): no code references → reflection/
  debug names. `TURN_QUALITY_*`, `TURN_SESSION_*`, `TURN_CHANGEOVER_REASON/TIME`, `ROUTE_TCP_MS/ROUTE_UDP_MS`,
  `DISABLE_P2P` are keys of the post-match network statistics record (0x1411b7490, alongside STATS_FORMAT, RX_LOSS_RATE_…).
  Telemetry only, not thresholds. Useful later to confirm in uploaded stats whether TURN was used. [static]

---

## Recommendation: what our server must do for relay fallback

1. **Run a TURN server reachable from the internet** — coturn (high confidence that a standard TURN-over-TLS server
   is what the client speaks; medium that coturn is accepted without tweaks):
   ```
   tls-listening-port=5349          # the client uses ONLY tls_port, over TCP+TLS
   listening-port=3479              # plain port must be non-zero in the reply but is not used; 3478 is taken by our STUN
   cert=/data/turn.crt  pkey=/data/turn.key   # self-signed, CN + subjectAltName IP = advertised address
   lt-cred-mech
   user=pes:<random-password>        # static creds are simplest (TURN-REST secret also possible, see below)
   realm=pes2021
   fingerprint
   external-ip=<public-ip>/<lan-ip>  # when behind NAT
   min-port=49160
   max-port=49200                    # UDP relay range: forward these + TCP 5349 on the router (UPnP)
   no-cli
   ```
   Both clients allocate on the same server and send to each other's relayed address; coturn then sends from one of
   its relay ports to its own public IP → if coturn sits behind a home router this needs NAT hairpinning [inferred
   risk]; a public VPS avoids it.
2. **Answer CMD_GET_TURN_SERVER_LIST** (HTTP msgpack), only when the TURN server is configured:
   ```
   {result:"NOERR",
    turn_server_list:[{zone:1, address:"<public IP literal>", port:3479, tls_port:5349}],
    id:"pes", pw:"<password>",
    server_certificate:"<base64 of turn.crt DER, no PEM header lines>",
    server_certificate_data_size:<DER length>}
   ```
   (omit `origin`). For TURN-REST instead of a static user: `id = "<unix_expiry>:<pid>"`,
   `pw = base64(HMAC-SHA1(secret, id))`, coturn `use-auth-secret`/`static-auth-secret`; use an expiry of hours, not the
   client's `turn_time_limit=60`, because refreshes during a 90-minute match re-check the username [inferred].
   For a LAN-only setup, leave the list empty (behaviour unchanged).
3. **Implement CMD_SEND_TURN_ADDRESS_DATA → push CMD_WATCH_TURN_ADDRESS_DATA** to the opponent(s) with the bytes
   unchanged (`{result:"NOERR", address, address_data_size}`), queue if the room isn't ready.
4. Keep `CMD_GET_GAMERELAY` with `game_sycom:"P2P"`; UDP 7600 can go (never used). Push WATCH_GAMERELAY_CHECK_RES
   `ER_MATCHING_RETRY` if either side reports a failure.
5. Cheap improvement even without TURN: fix the LAN-side player's `reflexive_addr` (public IP instead of LAN IP) when
   their opponent is remote.

## Live test 2026-11-05 (two copies on one LAN, coturn 4.6.1, branch protocol-fixes) [log]
- The game **accepted our GET_TURN_SERVER_LIST reply** (list of inline maps, REST credentials, base64 DER
  certificate) and connected to coturn over **TLS 1.2 / TCP** on tls_port (cipher ECDHE-ECDSA-AES256-GCM-SHA384).
  So the self-signed certificate passed as a trust anchor is enough.
- Allocate flow per attempt: ALLOCATE → 401 (normal) → ALLOCATE with credentials → **405 Mobility Forbidden**
  (the client sends MOBILITY-TICKET, RFC 8016) → ALLOCATE again → **success** → the client closes the TLS
  connection at once (coturn: "TLS/TCP socket buffer operation error", rp=3 sp=3) and starts over with a new list
  fetch. ~200 cycles per game in 15 min; no CreatePermission/ChannelBind, no SEND_TURN_ADDRESS_DATA.
  Fix being tried: coturn `mobility` option (branch commit 2e821b8), untested.
- **A failing TURN path does not break the match**: the direct link came up in 0.3 s (START/END notices),
  GAMERELAY_CHECK_RES NOERR at +27 s, both reached the match menu while TURN kept retrying.
- schemas.json types for GET_TURN_SERVER_LIST are wrong (turn_server_list "STR", server_certificate "NUM"):
  extractor bug; the game parsed our list/str values.

## Still unknown (and what would settle it)
- Whether ktc enforces peer verification / host-name check when `server_certificate` is empty or mismatched (find
  `SSL_CTX_set_verify` in ktc TLS init; or live test with an empty cert). Supply the cert to be safe.
- REQUESTED-TRANSPORT (UDP vs TCP relay), whether ORIGIN/SOFTWARE/MOBILITY-TICKET are sent, and the byte format of the
  `address` blob — a single live run against coturn with `verbose` + a capture on the server would show all of it.
- Which ktc string triggers the "dual route" UDP/DTLS leg (0x14205fab0) and whether coturn's SOFTWARE value could
  accidentally match `host:port,…` (it shouldn't; `no-software-attribute` removes the risk).
- P2pTurnIoFailover switching rules and the parameter values (in the game's multiplay parameter data file).
- Whether a TURN connect failure (server down) breaks the match or just leaves P2P — test by publishing a list with
  a dead TURN address on LAN.
- Meaning of `ntl_nat_type` bits (0x10307533); why the LAN success case took ~27 s from TURN list to CHECK_RES.
- The "SC" game-relay protocol (CsClient) — not needed if TURN works.
