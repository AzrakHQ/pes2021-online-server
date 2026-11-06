# Why TURN restarts every ~21.4 s (static RE, PES 2021 1.01.00)

> **Confirmed live (2026-11-06):** with a relay that moves a DTLS session to the client's new UDP port when a record from the same IP decrypts with that session's keys, the sessions moved every 10.0 s exactly as described, Refreshes were answered every 5 s, and the 21.4 s TURN restart cycle stopped (no new GET_TURN_SERVER_LIST for 70+ s).

Static only. No network traffic, no game launch. Addresses are VAs in PES2021.exe.
Tags: **[static]** read in code, **[inferred]**, **[guess]**.

## Short answer

1. **The client changes the UDP source port of the DTLS leg every 10 s.** The ktc UDP transport
   creates a new socket, binds it to local port 0 (a new ephemeral port) and swaps it in. This runs
   every 10 000 ms (a fixed value) once the transport is connected. It only runs when DSCP marking is on,
   and `Network.DscpEnable` is on by default. The DTLS session itself (OpenSSL with memory BIOs) is kept.
   Only the 5-tuple changes. **[static 0x141236333..0x1412364b2]**
2. Our relay looks up DTLS sessions by (ip, port). After the swap the client's records come from an unknown
   port, so the relay drops them without a trace. That is the "nothing on the DTLS leg after +5 s". The
   +10 s ping Refresh and the later ChannelData were sent, but from the new port. **[inferred]**
3. **Every 5 s P2pTurnIo sends a Refresh as an RTT ping** (`TurnNetworkIoPingIntervalMs`, default 5000).
   The ping sent after the swap gets no answer. **[static 0x14124a0fc → 0x141249a10 → 0x1411f0920 → 0x142061850]**
4. **Turn::Impl restarts when a request on the DTLS leg has been waiting ≥ `TurnNetworkIoConnectionTimeoutMs`
   (default 10 000 ms).** The restart drops both legs without close_notify. It clears the TURN
   credentials, which makes ConnectTurnTask fetch CMD_GET_TURN_SERVER_LIST again. Then the whole TLS → DTLS
   → SEND_TURN_ADDRESS_DATA cycle repeats. **[static 0x1411f3ed3..0x1411f3f03, 0x1411f42d0]**
5. Timing: swap at DTLS-transport-creation + 10 s; first ping after that at ≈ +10.5 s; restart 10 s later
   at ≈ +20.5 s; plus ≈ 1 s for HTTP + TLS + Allocate before the DTLS leg exists. That gives the 21.4 s
   cycle. **[inferred, fits the live numbers]**
6. **TaskMatchSession is not involved** (its states 9/0xa run once per match). TurnEnableDualRouteModeTime,
   the failover and the opponent are not involved either.
7. **Server fix: let a DTLS association follow its client to a new source port** (details below).
   Quick client-side workaround without a relay change: `proto_opt` `{"P2P":{"Network":{"DscpEnable":false}}}`
   turns the socket swap off.

## The restart chain, step by step

### A. Turn::Impl state machine (update 0x1411f1c60, state `[impl+8]`, jump table 0x1411f2050) [static]
| state | handler | role |
|---|---|---|
| 0 | 0x1411f41c0 | reset, go to 1 |
| 1 | 0x1411f3040 | wait for credentials and a server (0x1411ef710), open the TLS leg (0x1411ee050 dl=1) → 2 |
| 2 | 0x1411f3740 | wait for the TLS allocation |
| 3 | 0x1411f2980 | read the SOFTWARE directive. Field 3 == 0: open the DTLS leg (slot +0x78) → 4. Field 3 == 1/2: restart. Field 3 > 2 or DTLS connect failure: keep the TLS session as the main leg → 5 |
| 4 | 0x1411f3160 | wait for the DTLS allocation (ktc state 2), then close the TLS leg (0x1420615d0) → 5 |
| 5 | 0x1411f3cc0 | **running** (watchdog below) |
| 6/7 | 0x1411f2500 / 0x1411f2680 | close |

**Restart = 0x1411f42d0** [static]: marks the server failed (0x1411efea0), clears ParameterTurn `id`/`pw`
(0x1411f0a20 → setter 0x1411cca20 with ""), drops both sessions (slots +0x30, +0x78), clears the directive
host/port, then goes to state 1. Nothing is closed cleanly. That is why there is no close_notify.

**State 5 calls the restart when** [static 0x1411f3cc0]:
- the DTLS slot +0x78 is empty. The update loop empties it when the transport dies: receive pump
  0x141236520 fails, transport state 4, connect/alive check 0x141236140 fails, or send pump 0x1412369c0 fails
  (0x1411f1d04..0x1411f1d3e, 0x1411f1eed..0x1411f1f31);
- the ktc session is gone, or its public state (0x142061120) is not 2 = allocated (0x1411f3e69). Internal
  state 6 is reached by "Lifetime expired" (0x1420662d0), "Failed to update request refresh" (0x14206ae70)
  or an error response that cannot be handled;
- **an outstanding request has waited ≥ `[impl+0x1c]` ms** (0x1411f3ed8..0x1411f3f03). `0x1411eec20` returns
  the ktc session info. Its first field (session +0x3d8/+0x3dc) is "a request is pending, elapsed ms". It is
  written every tick by 0x1420659e0 from the transaction poll 0x14206fa10, which uses
  `now − [txn+0x8a0]` (the send time, 0x14206fbde..0x14206fbff). It is cleared when a response arrives.
  `[impl+0x1c]` is `TurnNetworkIoConnectionTimeoutMs`, default **10 000**. Path: Network+0xbc/+0xc0
  (reader 0x1411a721d) → failover cfg+0x68 (0x14122dda8) → fo+0x3b8 → P2pTurnIo struct +0x34 (0x141252af2)
  → Turn cfg +8 (0x141249fea) → Impl+0x1c (ctor 0x1411ece30);
- a later SOFTWARE directive on the DTLS leg has field 3 == 2 → 0x1411f05f0 (we send none, so this never fires).

### B. Who re-fetches the TURN list [static]
`onlinesystem::ConnectTurnTask` (vt 0x142898ff0) runs a loop for the whole session: state 1 (0x1411e2810) is idle;
2 (0x1411e2640) does GET_TURN_SERVER_LIST; 3 (0x1411e28f0) does SEND_TURN_ADDRESS_DATA; 4 (0x1411e2860) waits 10 s and
retries, at most 3 times. Its per-tick watcher 0x1411e24e0 sets `+0xb0` when the ParameterTurn list is empty or
`id`/`pw` is empty (0x1411e2be0). State 1 then clears the list (0x1411cbfa0) and fetches it again (0x1411e277b).
The watcher sets `+0xb1` when the own relayed-address blob changed (0x1411e2b30), which sends a new
CMD_SEND_TURN_ADDRESS_DATA. So "HTTP fetch → … → SEND_TURN_ADDRESS_DATA" in every cycle is exactly what
follows a restart.
The server re-selection wait is `TurnNetworkIoReconnectServerTimeWaitMs` = 1000 ms (Impl+0x20, 0x1411ef7c3).

### C. The ping [static]
P2pTurnIo base update 0x14124a0a0: when Turn state == 2 (0x1411ef3a0), it calls 0x141249a10 with
`[cfg+0x24]` = `TurnNetworkIoPingIntervalMs` (5000), or `[cfg+0x28]` = QuickPing (1000) in quick mode
(0x141249900). The first ping goes out at once (no time stamp yet). Each later one goes out when ≥ interval
has passed since the last accepted one. It calls Turn → 0x1411f07c0 → ktc **0x142061850 → 0x142067270 =
Refresh**. That is the "Refresh (LIFETIME 60)" right after the DTLS allocation and the Refresh at +5 s.
The real lifetime refresh comes much later: when the remaining lifetime is ≤ min(L·0.5, 30 s), so ≈ +30 s for
L = 60 (0x14206af4c..0x14206afc7, cfg +0xd8 = 30000, +0xdc = 0.5).

### D. The socket swap (the root cause) [static 0x141236140]
In the transport "alive" function, for the UDP/DTLS leg only (`[t+0x30] == 0`) and in connected state
(`[t+0x178] == 2`):
```
if now - [t+0x188] > 10000 ms:                     # 0x141236356, fixed value
    s = new UDP socket (0x1411db950)
    if s and [t+0x2a] (DSCP on):                   # 0x141236389
        s.opt(4, [t+0x2b]=46)  (vtbl+0x70)          # DSCP/TOS [inferred meaning]
        register s with the two pollers (+0xe0, +0xd0)
        s.bind(0.0.0.0 : [t+0x28])  (vtbl+0x30)     # [t+0x28] = 0 → new ephemeral port
        on success: unregister the old socket, [t+0xd8] = s   # all later sends use the new port
    [t+0x188] = now
```
- `[t+0x188]` is set to "now" in the transport ctor (0x141234ff5). So the first swap comes 10 s after the DTLS leg
  is created, then every 10 s. **[static]**
- `[t+0x28]` (local port) comes from Turn cfg +0, which is 0 (0x1411ecfb0). Nothing writes the bound port back.
  **[static; "nothing writes it" = medium-high]**
- `[t+0x2a]/[t+0x2b]` = Impl+0x16/+0x17 (0x1411ee22a) = Network `DscpEnable`/`Dscp`. The code defaults are
  `01 01 01 2e` = present/true, present/46 (0x1411a78db). Our server sends no proto_opt, so they are on.
  **[static]**
- The TLS/TCP leg is not touched. The NTL game socket is a different object. **[static for TCP; NTL not checked]**
- The receive side keeps working on the client: it still accepts only datagrams from the directive's host:port
  (0x141235f50). So **replies just need to go to the new source port**. **[static, ktcdtls.md]**
- Why Konami does this is not proven. Their relay probably tracked associations independently of the port.
  The ktc record trailer is keyed by a server-provided 16-bit `key` (type-1 message), which looks like a
  connection ID. **[guess]**

## What the client expects, and from where
- For **every** STUN request on the DTLS leg (pings every 5 s, refreshes, ChannelBind): a response (success or
  error) with the same transaction ID. It must arrive within **10 s**, from the directive's `HOST:PORT`, inside the
  existing DTLS association. Framing as in ktcdtls.md: `SSL_write(stun || 00)`. Missing that deadline means a
  restart. Nothing from the opponent and no ChannelData is needed to keep the session. **[static]**
- The relay must accept the association **from a new client source port every 10 s**. It must send its replies
  (and relayed ChannelData) to the newest address. **[inferred]**

## Server-side fix
1. **DTLS association migration (recommended).** When a datagram arrives on udp 5350 from an unknown (ip, port)
   and starts `17 fe fd` (application data, epoch ≥ 1):
   - Strip the ktc 2-byte trailer as usual (ktcdtls.md Q2 step 5).
   - Try the live associations whose client IP matches. Try the most recently active first; there are 2 when both
     game copies share one IP. Feed the datagram to that association's SSL read BIO and call `SSL_read`. A
     record from the wrong association fails the GCM check and is dropped silently, without changing that
     session's state. Stock OpenSSL behaviour; check this in the relay's own tests.
   - On the first successful decrypt, rebind the association (and its TURN allocation's client address) to the
     new (ip, port). Send every later reply and every relayed ChannelData there. Keep the old key for a few
     seconds for records that are late or reordered.
   - Optional speed-up: after the handshake, give each association a unique 16-bit key by sending the type-1
     record `SSL_write(BE16(key) + b"\x01")`. Then `trailer XOR v` (v as in ktcdtls.md Q1) gives the key directly.
     Only do this after a live check; ktcdtls.md advises against type-1/2 until it is proven.
2. **Workaround, no relay change:** send `proto_opt` = `{"P2P":{"Network":{"DscpEnable":false}}}` in
   GET_SERVER_ENV. Then `[t+0x2a] = 0`, and the swap block exits before binding (0x141236389). Side effect: no
   DSCP-EF marking on sockets that use this flag. That is harmless on home internet. **[static gate; that the
   Friendly-lobby session uses profile "P2P" is inferred]**
3. Do **not** try to "fix" this with longer timeouts. `TurnNetworkIoConnectionTimeoutMs` only moves the restart.
   The leg stays dead after the first swap.

## Live tests that settle it (stop at the first signal)
- **Test 1 (no behaviour change):** in the relay, log every datagram on 5350 from an (ip, port) without a
  session: source, length, first 13 bytes. **Proof:** about 10 s after each DTLS ClientHello, records
  `17 fe fd 00 01 …` start to arrive from the same IP with a **new source port**. The next
  CMD_GET_TURN_SERVER_LIST from that game comes ≈ 10 s after the first such record.
- **Test 2 (fix):** add migration (fix 1), or only send the proto_opt (fix 2). **Proof:** for > 60 s there is no
  repeated CMD_GET_TURN_SERVER_LIST / SEND_TURN_ADDRESS_DATA, and ping Refreshes keep coming every 5 s and are
  answered. With fix 1 the relay log shows "migrated" about every 10 s. With fix 2 the source port stays the same.

## Confidence
- High: the restart path (0x1411f42d0), the pending-request watchdog and its 10 s tunable, the 5 s ping as a
  Refresh, ConnectTurnTask re-fetching the list, and the 10 s socket swap gated by DscpEnable with bind port 0.
- Medium: that the dropped datagrams from the new port are what the relay misses. This fits every live number
  (21.4 s, Refresh only at +5, no close_notify, ChannelData stops), but Test 1 has not been run.
