# GNCX_0006 and the kps address labels (static, PES 2021 1.01.00, 2026-11-06)

Static reading only. No network traffic, no game launched. Dumps are in `findings/gncx/` (not published).
Tags: **[static]** read at the address given, **[log]** seen live (livetests.md, mod notes), **[inferred]**, **[guess]**.

---------------------------------------------------------------------------------------------------
## Q1. What GNCX556 checks

### Answer
- **GNCX556 is the DATAPACK verdict.** It is the same compare that datacheck.md describes, the
  `param_check_sum` / `entry_check_sum` mismatch. It is not a second check, and the data pack is not hashed in
  the handshake.
- Two objects read the same verdict, and the one that reports first wins:
  - **The multiplay object reports it itself** (OnlineModeMultiplay.cpp), as GNCX_0004/5/6.
  - TaskMatchSession state 21 would report MXNV_0015..0019 for the same value. It polls one tick later.
  - The live LEAVE_ROOM therefore carried GNCX_0006, not MXNV_0017.
- Fix for datacheck.md's table: on the Quick Match and lobby path the codes the player sees are
  **GNCX_0006 = DATAPACK error** and **GNCX_0004 = revision-check timeout (60 s)**.

### Evidence
- Update function 0x1410001f0. `rbx` = multiplay+0x158, so `[rbx+0x46c]` = **multiplay+0x5c4** and
  `[rbx+0x70]` = the multiplay state [static].
- State 3, at 0x1410003a1, calls 0x141000ea0:
  - 0x141000ea0 runs the peer-record build 0x141002520, then the verdict 0x141001190.
  - The result goes into +0x5c4 (0x141000ec5). If the verdict is still 0 when the 60 s deadline `[mp+0x748]`
    passes, it writes 2 (0x141000ee0) [static].
  - The function returns true once +0x5c4 ≠ 0.
- The switch on +0x5c4 [static]:

| +0x5c4 | meaning (datacheck.md) | site | line / sub → code | global 0x1436fdbc0 |
|---|---|---|---|---|
| 1 | ok | 0x1410003bc | → state 4 | – |
| 2 | timeout 60 s | 0x14100057d | 548 / 4 → **GNCX_0004** | 6 |
| 3 | revision (dead code) | 0x1410004f0 | 552 / 5 → GNCX_0005 | 7 |
| 4 | **param or entry sum differs** | **0x141000467** | **556 / 6 → GNCX_0006** | 8 |
| other | – | – | no report | – |

  Every non-1 case then sets state 8 (0x14100058c), the failure path.
- TaskMatchSession state 21 reads the same field, `0x141000f10()->+0x5c4` (0x1411440c4), and maps it to
  MXNV_0015..0019 [static]. It only acts on a later tick, after the multiplay object has already reported
  GNCX556. The live code shows the first report is the one sent [log + inferred].
- The global 0x1436fdbc0 (6/7/8) is copied into an abnormal-end record at 0x1410df66f [static]. It is the
  "connection" error kind behind the "Unable to connect." dialog [inferred].
- Value 4 comes only from 0x141001190: an exact byte compare of `param_check_sum` (record +0xa0), then
  `entry_check_sum` (+0xc0), for every peer. Both come from the P2P `CmdRevisionCheck` exchange (datacheck.md)
  [static].
- Turning it off is unchanged: `is_revision_check` other than "YES" clears `mp+0x5c0` and the verdict returns 1
  [static, and confirmed live].

### Would a whole match with different data run?
**Probably not, if the changed data is used during play.** A desync is detected and ends the match with
SYNC_FAILED. It does not run on silently.
- PES online play is lockstep: each side runs its own simulation from the shared inputs.
  - The per-player command streams have 24 slots of 0x74 bytes each (dispatcher 0x142165900, loop 0x1421674d0)
    [static].
  - Player stats and rosters come from each side's own database [inferred]. If the DATASYNC team sync carried
    full player data, the DATAPACK check would not be needed [guess].
- **The SYNC_FAILED path** (class `match::OnlineCommandControllerImpl`, vtable 0x142bf4478, held at
  MatchOnline+0x38, ctor 0x1421641a0) [static]:
  1. vt+0xc0 = 0x142167250 sets the "sync failed" flag `+0x41`, once (only if `+0x48` and `+0x41` are both 0).
  2. 0x142167380 sees the flag, clears it, and puts a **command of type 0x66** into the outgoing command buffer
     of each local player (0x14216742a). So the peer is told too.
  3. The receiver dispatcher 0x142165900 sees type 0x66 (0x14216598c) and calls vt+0xc8 = **0x142166c30**.
     The sender also calls it locally (0x1421674a7) when `[0x14149a470()+0x12c]` is set.
  4. 0x142166c30 sets `+0x48` (once) and raises abnormal end **SYNC_FAILED** (enum 9, code 0x2000001, via
     0x14216d0d0). It then reports **RMYN393** (MatchOnlineCommandController.cpp line 0x189 = 393).
- **Not found statically:** the code that calls vt+0xc0, which is the actual divergence detector. I scanned
  every direct `call/jmp [reg+0xc0]` in the exe (472 sites) and none is clearly on this object. It is probably
  reached through a pointer that was stored earlier.
- **The live evidence that a divergence is caught:** the first Windows-vs-Proton match ended **1.6 s after
  kick-off** with SYNC_FAILED / RMYN393 on both sides, while the CHECK_CHEAT sums were equal. The cause was
  different float math. With the mod's same-math hooks: 42 s of play and no SYNC_FAILED (mod-install-id notes)
  [log].
- Expected outcome:
  - With different data, the match runs until a changed value first affects the simulation, then both sides
    get SYNC_FAILED / RMYN393 and the match is abandoned [inferred].
  - A stat change for a player who is not on the pitch may never diverge [guess].
  - Roster or entry differences change who plays, so they should diverge almost at once [inferred].
  - Separately, the watchdog arms its own 180 s SYNC_FAILED timer (match.md), a stall timeout, not a compare.

### Live test that settles the desync question (stop at the first signal)
1. Setup:
   - Both copies on mod 0.4 same-math. This removes the float cause.
   - `is_revision_check:"NO"`.
   - Copy 1 with a Live Update that changes a **starting** player's Speed by a large amount. For example
     `mklivedata.py … --player <Messi id>:<Speed>:+30`. Copy 2 stock.
   - Friendly lobby. Both pick Barcelona (or any team where that player starts), autoplay on both.
2. **Proof of desync:** the server log shows `CMD_SEND_ABNORMALEND {reason "SYNC_FAILED", error_code
   "RMYN393…"}` from both copies after CHANGE_GAMEPHASE `1ST`. Note the seconds after kick-off. Stop there.
3. **Proof that it runs:** no SYNC_FAILED by `1ST_15MIN`. Stop there.
4. Optional second run (only if 2 fired): pick two teams without the changed player. No SYNC_FAILED by
   `1ST_15MIN` means only data that is actually used matters.

---------------------------------------------------------------------------------------------------
## Q2. The two labels and the server rule

### What the labels are
- **Own label = the player's own *reflexive* address**, as its own NTL measured it from our STUN server:
  `[[OnlineSystem+0x10]+0xa8]` ("NTL info") [static].
  - SetConfig 0x141144d80 calls 0x14116e3c0 (0x141144e7a). That copies the 20-byte block at NTL-info +0xa8
    and formats its IP (0x1411cb6f0, "a.b.c.d").
  - The port comes from 0x14116e5f0 (the same +0xa8 block) through 0x1411c9f30 (type 1 = P2P: one entry)
    [static 0x14116e622, 0x1411c9f5a].
  - Proof that +0xa8 is the reflexive address: the SEND_MATCHING_RESULT `member` filler 0x1410c2b40 copies
    NTL-info +0x28 → `host_addr` (cmd+0x1ec) and **+0xa8 → `reflexive_addr`** (cmd+0x204). The packer
    0x141c7e5a0 writes host_addr from +0x1ec, then reflexive_addr [static]. The NTL info holds 0x80-byte
    sockaddr slots: host +0x28, reflexive +0xa8, relayed +0x128.
  - It is **not** the host or LAN address, not the socket's local address, and not the address NTL ends up
    connecting to.
  - The server knows it: it is the `reflexive_addr` the client sent in CMD_SEND_IPANDPORT_NTL [inferred: same
    NTL-info block].
- **Peer label = the `reflexive_addr` the server sent for that peer, after any rewrite**:
  - Quick Match: the WATCH_MATCHING member.
  - Lobby: the player-list element, or `room_owner` in JOIN_ROOM_NTL.
  - SetConfig walks the peer vector `[0x140ff0a20()+0x58]+0x60` (0x30-byte entries) and formats elem+0x18..+0x2b
    (0x1411451a2..0x1411451c9 → 0x1411cb630 IP, 0x1411cb750 port). In that entry, host is +4..+0x17 and
    **reflexive is +0x18..+0x2b** (coop2.md layout; same as the NTL peer conversion in 0x141092720) [static].
  - **`host_addr` is never used as a label.**
- Format: `"a.b.c.d:port"` (decimal, no padding; `[v6]:port` for IPv6). The two labels are sorted as
  **strings**, byte by byte, then by length (msp start 0x141ff53e0, turnlinkup.md) [static]. So "156…" < "192…"
  < "41…".
- Labels do not have to be reachable addresses:
  - NtlNetworkIo send 0x14122c140 → 0x14114bea0 looks the destination label up in two string maps at io+0x48
    and io+0x58. 0x14114c3b0 fills them from the same NTL peer record: the host label from peer+4, the
    reflexive label from peer+0x84 [static]. The direct path then goes wherever NTL actually punched through.
  - TURN ignores the label (turnlinkup.md).
  - So a rewritten reflexive_addr is still consistent for sending. But it also removes or changes that peer's
    reflexive *candidate* for NTL hole punching [inferred].

### Does it apply to normal P2P?
**Yes, to every P2P match** (Quick Match, lobby, with or without TURN):
- All four strategies create the same `onlinesystem::P2pFullMeshSession` (ctor 0x141245290):
  P2pFullMesh 0x14122bedc, Raw 0x14122c8fc, TurnOnly 0x14122d454, WithTurn 0x14122ebf7.
- That session's start (vt+8 = 0x141245a50) and link set-up (vt+0x28 = 0x141246070) run the msp role rule
  (0x141ff53e0 / 0x141ff55e0) [static].
- If both sides end up passive, the match ends with MXNV_0013 after `P2P.Session.ConnectionTimeoutUs` (10 s),
  even when NTL itself connected [inferred from turnlinkup.md].

### The condition
Let `L_X` be X's own reported reflexive address and `S_X→Y` the reflexive address the server sends to Y for X.
- A opens the link iff `L_A < S_B→A`.
- B opens it iff `L_B < S_A→B`.
- It works iff **exactly one** of the two holds.
- If the server sends everyone's reflexive address unchanged (`S = L`), exactly one always holds, unless the
  two strings are equal [static rule; inferred consequence].

### Cases
- **(a) Two players on one LAN** (works live): nothing is rewritten. Both labels are the real LAN (or one
  shared public) IP with different ports, so they are consistent. One side opens [log + inferred].
- **(b) Two homes, A 192.168.1.5 / 156.204.152.194, B 192.168.0.7 / 41.33.10.20:**
  - Server on the internet: L_A = "156.204.152.194:pa", L_B = "41.33.10.20:pb", both sent unchanged.
    A: "156…" < "41…", so A opens. B sees "156…" < own, so B waits. **No deadlock** [inferred].
  - Server in A's home (the usual set-up here): our STUN sees A over the LAN, so L_A = "192.168.1.5:pa".
    Today `_visible_addr` (pesmatch.py) sends B the value S_A→B = "156.204.152.194:pa". A: "192…" < "41…",
    so A opens. B: "156…" < "41…", so B waits. **This pair works, by luck.**
  - **Deadlock window** for that set-up: any B whose label sorts between "156.204.152.194:pa" and
    "192.168.1.5:pa". That is about every public IP starting 16x–19x (for example 178.x, 185.x, 188.x), and a
    few 156.2xx / 192.0–192.168.1.x. Both sides wait, then MXNV_0013 after 10 s [inferred].
  - **Both-open window:** a host LAN such as 10.0.0.5 with a peer label between "10.0.0.5" and the host's
    public IP (for example 12.x, 100–155.x). Both sides open. What happens next is not known; probably a
    failure [guess].
- **(c) Relay only between homes:** the same rule applies, because kps over TURN uses the same labels.
  - With unchanged reflexive addresses it works. This is proven live with a consistent label pair.
  - The rewrite windows above apply equally.

### Server rule
1. **Send every player's `reflexive_addr` exactly as that player reported it** (CMD_SEND_IPANDPORT_NTL) to every
   viewer: player-list elements, JOIN_ROOM_NTL `room_owner`, WATCH_MATCHING members. That value is that
   player's own label, so the order always agrees and exactly one side opens.
2. **To make a LAN-side player reachable for a remote viewer, rewrite `host_addr` instead.** Put the advertised
   public (or Tailscale) IP into it and keep the port. host_addr is an NTL candidate (UHP is sent to host and
   reflexive, wire.md), but it is never a label [static for "never a label"; inferred that NTL punches the
   public host candidate as well as it did the rewritten reflexive one].
3. If rule 2 turns out not to punch through, keep the reflexive rewrite, but **check the order per pair**:
   - require `(L_A < S_B→A) != (L_B < S_A→B)`, with Python `str` compare of `"ip:port"`;
   - if it fails, drop the rewrite for that pair (send `L`), so the pair stays consistent and the link can
     still come up over TURN.
   - Another option: have our STUN server answer LAN clients with the advertised public IP. Then `L` itself
     is public and nothing needs rewriting [inferred; it changes the NAT test result].
4. Equal labels (both reflexive addresses equal, for example both 0.0.0.0:0 after a failed NAT test) cannot be
   ordered by the server. Warn, or refuse the pairing [guess on the tie outcome].
5. Forced-relay tests: keep using a consistent pair (owner shown low, guest shown high, relative to each one's
   own L). That is what worked live.

### Live tests (stop at the first signal)
- **Rule 2:**
  - Server in the host's home, friend remote. Send reflexive unchanged and the public IP in the host's
    host_addr to the friend. The friend's IP must fall in the deadlock window, or simulate it on one PC by
    using copy 2's own L with a high first digit.
  - Proof: SYSTEM_NOTICE END from both, then CHANGE_GAMEPHASE DATASYNC. Stop there.
- **Deadlock confirmation** (no WAN needed, one PC, two copies, TURN on, proto_opt Session 30 s):
  - In what copy 2 receives, give copy 1 the reflexive address `0.0.0.1:9`. In what copy 1 receives, give
    copy 2 the reflexive address `0.0.0.2:9`. Each copy then sees a peer label below its own 192.168.1.100.
  - Predicted: no ChannelData, MXNV_0013 at bind + 30 s. Swapping one of the two (copy 1 sees copy 2 as
    `240.0.0.1:9`) gives a 14-byte first ChannelData from copy 1.
