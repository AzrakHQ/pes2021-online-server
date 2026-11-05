# Group "wire": transport layers we have not seen on our wire, and the P2P packet format

Static RE of PES2021.exe 1.01.00. Tags: **[static]** read in code at the address given, **[log]** seen in
our traffic, **[inferred]**, **[guess]**. Working files (asm dumps, helper scripts) are in `findings/wire/`.

## Overview: the three network stacks

```
(A) Server link (gate / ACCOUNT / MENU, TCP 10000):
    onlinesystem::Client -> fox::ncl NclMio (Blowfish tier)  or  NclEnhancedMio (AES-256 tier)
    msgpack {msgid, rqid, ...}

(B) Player-to-player match link (UDP, peer <-> peer, or through TURN):
    onlinesystem::P2pFullMeshSession / CsClientSession / ObserveSession
      -> kps::msp::{P2pFullMeshSession, CsClientSession}           (multi-session protocol)
      -> kps::domain::LlDomain + ClltcpConnectionInternalController (connection management)
      -> kps::lltcp::{FastTcp, ScalableTcp, pacing}                 (reliability / congestion)
      -> kps::dlcp link: key exchange + RC4 + record [hdr|csum16|payload]
      -> kps::socket::SocketInterface = onlinesystem::NtlNetworkIo / P2pTurnIo* -> ntl -> fox::nio::UdpSocket

(C) NAT traversal (ntl "NTL 1.11.11-Windows64", cobra/NatTraversal/NTL/ntl/NtlHelper.cpp):
    STUN-format messages (RFC 5389 header, magic 0x2112A442) with Konami methods and attributes:
    NAT test against the STUN server, keepalives, UDP hole punching (UHP_*) between peers, TURN.
```

The headline results:
1. **NclEnhancedMio is not tied to a server type.** It is a global tier that starts as soon as the client
   holds a 32-byte AES key. Only `CMD_SET_CURRENTPLAYER` or `CMD_RECONNECT` with `key_size == 32` installs
   that key. Our server sends `key_size 16`, so the client stays on Blowfish. **[static]**
2. **kps::dlcp is the per-link security layer of the kps UDP stack.** Both players run it, so it protects
   the P2P match link. It would also run on a CS game-relay link or an Observe session. Our server never
   needs it for P2P matches, and a standard TURN relay carries it opaquely. **[static chain + inferred]**
3. **The large-message framing is now pinned.** Messages over about 1015 bytes are sent as a 0x70 first
   frame, then 0x71 middle frames, then a 0x72 last frame. The client also accepts that sequence. The
   client rejects any frame body over 0x410 bytes. **Our server should fragment large replies the same
   way.** Today it sends one big 0x60 frame. **[static]**

---

## 1. NCL frame kinds (gate layer): every kind the client handles

The receive-side peek is 0x141854f10. It XORs the first 2 bytes with `5b 9f` and accepts a XOR'd kind from
jump table 0x141855004: {0x10, 0x20, 0x21, 0x22, 0x60, 0x70, 0x71, 0x72}. If the XOR'd kind is not one of
these, it reads the **un-XOR'd** kind and accepts {0x1060, 0x1070, 0x1071, 0x1072}. So enhanced frames are
**not XOR-masked**. Bit 0x1000 of the raw kind selects the enhanced parser (0x141853120 @0x141853290:
`and edi,0x1000`). **[static]**

| Kind | Tier | Meaning (encoder) | Body after decrypt | Decode (0x1418566c0) |
|---|---|---|---|---|
| 0x0060 | Blowfish | single-frame message (0x141856af0) | u32 data_len, u32 original_size, u8 zlib, data | complete → inflate → msgpack |
| 0x0070 | Blowfish | **first fragment** (0x141857190) | u32 **total_len**, u32 chunk_len, u32 original_size, u8 zlib, chunk | resets reassembly buffer, appends, returns "incomplete" |
| 0x0071 | Blowfish | **middle fragment** (0x141856d50) | u32 chunk_len, chunk | appends, "incomplete" |
| 0x0072 | Blowfish | **last fragment** (0x141856f70) | u32 chunk_len, chunk | appends → inflate(original_size) → msgpack |
| 0x0010 / 0x0020 / 0x0021 / 0x0022 | none | same as 60/70/71/72 when the codec's "encrypt" byte (+8) ≠ 1, i.e. no crypto object | same | on receive, still Blowfish-decrypted in tier 0 (0x1418547f0 has no kind check), so for the client 0x10 ≡ 0x60 |
| 0x1060 / 0x1070 / 0x1071 / 0x1072 | AES-256 | the same four roles in the enhanced tier | same envelope fields | same |

- Send path is 0x1418537d0. The payload goes in one frame if it is ≤ 0x3f7 bytes (Blowfish) or ≤ 0x3d7
  (AES). Otherwise the first chunk is 0x3f3/0x3d3 bytes and later chunks are 0x3fc/0x3dc. The field
  values come from encoder 0x141857190: `[+0x70](total)`, `[+0x70](chunk)`, `[+0x70](orig)`,
  `[+0x58](zlib)`. **[static]**
- The client always sets codec+8 = 1 before sending (0x1418538c0), so **the client only ever sends 0x6x/0x7x
  (or 0x106x/0x107x)**. **[static]** We have only ever seen 0x60. Our 0x10 pushes worked. **[log]**
- The client's parser bounds the body to ≤ 0x410 bytes (0x1418548c0 @0x141854a49 `cmp dx, r12w`, r12 = 0x410).
  **[static]**
- **Server should:** reassemble 0x70/0x71/0x72 by this exact layout. The `_read_rest` heuristic in ncl.py
  guesses A/B layouts. With the real layout, plain[:4] of the first frame is total_len, not data_len.
  Replies whose packed (or zlib'd) payload is over 1015 bytes should go out as 0x70 + 0x71… + 0x72 with the
  layout above. One oversize 0x60 frame breaks the client's 0x410 bound.

## 2. fox::ncl::NclEnhancedMio + NclCryptAes: the "enhanced" tier

**What turns it on [static]**
- Send tier = `NclDaemon.aes_key_len (+0x144) > 0 ? 1 : 0`, computed per send (0x1412168b0 @0x141216c75
  `cmp [daemon+0x144],0; seta dl`). That flag goes to queue job+0x18 (0x141214920), then to the encoder's
  tier argument (0x141217fd0 → 0x141853770 → 0x1418537d0 r9d). **The flag is global, not per server type
  or per connection.**
- The AES key is installed by:
  - SET_CURRENTPLAYER handler **0x1410f9ca0**. It reads Result+0x1f0 key and +0x22c key_size: if key_size == 0x20
    it calls 0x14184f8a0 (copy to daemon+0x124, len to +0x144). If key_size is non-zero and not 0x20, it calls
    0x14184f8d0, an obfuscated thunk to the Blowfish-key setter. If key_size is 0, nothing happens.
  - RECONNECT handler **0x141233ee0**. It uses Result+0x1d0 / +0x20c with the same logic.

  The parsers store key_size at 0x141d6b481 and 0x141e52ea2. The key bytes are base64-decoded (that the
  base64 decode feeds the Blowfish path is confirmed live).
- The key is cleared only in the NclDaemon constructor (0x14184f110 @0x14184f216). **Once a 32-byte key
  arrives, every later frame on every connection (new GATE/ACCOUNT/MENU sessions included) is AES until the
  game restarts.** A later SET_CURRENTPLAYER with key_size 16 does *not* clear the AES length. **[static]**
- Receive tier is chosen per frame from kind bit 0x1000. An enhanced frame arriving while there is no AES key
  fails (0x141853120 @0x1418532fe, ebx stays −0xca). **[static]**
- The msg+0x168 == "COMPOUND" check in 0x1418537d0 (it builds a string from daemon+0x10) is
  **vestigial**: the encoders never read r9. It is an MGSV leftover. **[static]**

**Crypto [static]**
- NclCryptAes is 0x68 bytes; ctor 0x1418522a0 copies a 32-byte key. Decrypt is 0x141852420 and encrypt is
  vtable slot 1 (0x141852670). Both use Windows BCrypt with `"AES"` and `"ChainingMode"="ChainingModeCBC"`
  (UTF-16 at 0x142a4e228 / 0x142a4df88), flags 0, no BCrypt padding. That is **AES-256-CBC**.
- Padding is manual PKCS#7 (pad byte = pad count, 1..16), computed so that
  `(0x24 + body + pad) % 16 == 0` (0x141856200). On receive the client strips it if every one of the last
  n bytes equals n ≤ 16 (0x141855f2d).
- IV: 16 bytes = first 16 bytes of `SHA256(xorshift32(seed))` (0x141853010, seed from 0x14032b3e0), sent
  in clear in the header. It is weak but does not matter to us.
- Integrity: plain software SHA-256 (0x1418573f0, init constants 0x6a09e667… at 0x1415d19d4).

**Frame layout (0x1418562a0 build / 0x141855cc0 parse) [static]**
```
off  len  field (big-endian)                       encrypted?
0    2    kind 0x1060/0x1070/0x1071/0x1072  (NOT XOR'd)   no
2    2    body_len (incl. PKCS#7 pad), <= 0x410            no
4    16   IV                                               no
20   4    nonce = xorshift32(random) (field "seq")         AES-CBC  -+
24   32   SHA256( bytes[0:24] (plaintext) || body )        AES-CBC   | one CBC stream:
56   n    body = data_len|orig|zlib|data (same envelope)   AES-CBC   | 36 + n bytes, n+36 ≡ 0 mod 16
          + PKCS#7 pad                                              -+
```
- There is no MD5 and no XOR. Tier-0 finalize slot +0x148 is skipped when tier ≠ 0 (0x141856c6a). The minimum
  frame is 0x38 bytes; "[EMIO] msgbuff_len < MSG_ENHANCED_HEADER_LEN" is the 0x38 check at 0x141855d60.
- Server side: `decrypt(key, IV, frame[20:56+n])` → check SHA-256 → strip pad → same envelope as easy.
  Reply the same way (kind 0x1060, or 0x107x for fragments).

**Why we never saw it:** our SET_CURRENTPLAYER sends `key_size = 16` (pescmds.py:268). **Is any server type
expected to use it?** Nothing in code ties it to GATE/ACCOUNT/MENU/VDPCHAT or to a svrlist field. Konami's
server picked the tier only through the key length it handed out in SET_CURRENTPLAYER / RECONNECT.
**Recommendation:** keep 16-byte keys. AES gives us nothing, and once on, it sticks for the whole process.
Confidence high.

## 3. kps::dlcp: where it is really used

**Creation chain [static]:** the LinkController ctor 0x14203c730 (sets Idle state vt 0x142bc88e0) is
called from 0x14203bf70 ← 0x142029430 ← 0x142029ea0 ← 0x142000cb0 ← 0x1420139f0 / 0x142013a20 ←
0x1420119d0. That last function is reached only through thunks 0x142011c20 / 0x142011c30 = **vtable slots 13
and 19 of `kps::domain::detail::ClltcpConnectionInternalController<kps::domain::LlDomain>`** (vt 0x142bc53a8).
Idle-state slot 3 (0x142049650) creates KeyExchangeActive and slot 4 (0x142049220) creates
KeyExchangePassive. **So every Clltcp connection in an LlDomain owns a dlcp link.**

**Users of LlDomain [static]:** `kps::msp::protocoldependent::LlDomainController` (ctors 0x141199de0, 0x14119d8b0).
It is created from 0x1411aa730 (CreateMultiplaySession, strings "turn_mode" / "direct_online_turn_mode"). That
function also picks the session strategy:
- session type (cfg+0x18): **0 → MultiplaySessionStrategyCsClient** (onlinesystem::CsClientSession →
  kps::msp::CsClientSession, a client↔server game relay); **1 → P2P full mesh**; **3 → Observe**; 2 → fail.
- For P2P with an empty TURN list → `P2pFullMesh` (plain). With TURN servers → tunable `turn_mode` (default 0) →
  `P2pFullMeshWithTurn`; 1 → TurnOnly; 3 → plain. The "direct online" mode uses `direct_online_turn_mode`:
  0 Raw, 1 TurnOnly, 2 WithTurn. 0x141b34850 is a generic tunable lookup with default 0, so it is not a server
  field. **[static / inferred]**
- Protocol presets exist for both kinds: "P2P", "P2P_LOW/HIGH/GIVE_UP_QUICKLY_LEVEL", "CS_*",
  "P2P_ADHOC_*" (0x14119bc00). **[static]**

**Conclusion:** dlcp protects the **player↔player UDP link** (and would protect a CS relay or observer link).
It is neither the gate nor VDPCHAT. A private server needs it only to *be* a CS game-relay endpoint (session
type 0) or an observer. A TURN relay forwards the encrypted datagrams without understanding them.
Confidence high for "kps stack, not gate", medium-high for "UDP P2P link".

**dlcp wire format [static unless noted]**
- Key exchange (SimpleExchangeKeyExchanger vt 0x142bcb080, step 0x142053ec0):
  - Passive side, state 0 (0x142053a10): takes a ≤4-byte peer token, RC4-decrypts it with the static key
    `a977d392 db0e55b9 9d1641c2 d4f82739` (.rdata 0x142bcaf10), and uses ntohl(token) + clock through xorshift32
    to generate the 16-byte session key K.
  - Active side, state 1 (0x142053f90): RC4(static)-decrypts the reply and requires exactly 16 bytes
    (LinkController+0x40 = 0x10 set at 0x14203be42, per prior notes). K = those 16 bytes, taken verbatim
    (0x142053e10).
  - Exact token direction and framing are **[inferred]**.
- Secure state (0x14204c050): the record is RC4(K)-encrypted (encryptor vt slot 1) and sent through
  0x142049a90 → SocketInterface vt+0x10. Prior notes say both directions are keyed with RC4_KSA(K).
- Record (encoder 0x142052f20, decoder 0x142052be0):
  `[hdr u8][csum u16 BE][payload]`
  - hdr bit7 = a flag taken from the link object (bit0 of [obj]), bit6 = zlib, bits0-5 = random
    (xorshift32(clock) & 0x3f).
  - csum is computed over the whole record with the csum field zeroed (0x142052e90, RFC 1071 per prior
    notes) and htons'd.
  - If compress2(level 9) of `csum|payload` fits in len−1 bytes, the output is `hdr|0x40` followed by the
    zlib output. The decoder inflates (0x142053710) when bit 0x40 is set.
- Reliability, sequence and ack fields live one layer up in `kps::lltcp` / Clltcp. That header was **not
  decoded** (the code is string-poor). What is known: congestion classes FastTcp/ScalableTcp, delay-based
  detection, Bps/Pps pacing, and stats counters (Rx loss/dup/reorder,
  `kps::domain::protocoloption::NetworkPerformanceInfo`). **[static RTTI]**

## 4. NAT traversal and peer handshake (ntl)

- ntl uses RFC 5389 STUN framing for everything. Method-name table 0x1418e07a0 and attribute-name table
  0x1418e0290 **[static]**:
  - Standard methods 0x001 BINDING, 0x003 ALLOCATE, 0x004 REFRESH, 0x006 SEND, 0x007 DATA,
    0x008 CREATEPERMISSION, 0x009 CHANNELBIND, 0x00a–0x00c TCP-TURN.
  - **Konami methods:** 0x801 RT_ACK, 0x802 RP_HEARTBEAT, 0x803 RP_ENTRY, 0x804 RP_INFO, 0x805 RP_APPROVAL,
    0x806 RP_EXIT, 0x807 RP_DATA_CAST, 0x808 RP_SYNC, 0x809 RP_P2P_SYNC, **0x80a UHP_FINALIZE,
    0x80b UHP_CONNECT, 0x80c UHP_KEEPALIVE** (UHP = UDP hole punching).
  - **Konami attributes:** 0x9090 RP_P2P_HEADER, 0x9091 RP_RELIABILITY, 0x9092 RP_AMF_DATA,
    0x9093 RP_JSON_DATA, 0x9094 RP_HOST_ADDRESS, 0x9095 RP_REFLEXIVE_ADDRESS, 0x9096 RP_RELAYED_ADDRESS,
    0x9097 RP_NAT_TYPE, 0x9098 RP_SYNC_POINT, 0x9099 RP_ENTRY_IDX, 0x909a RP_PEER_STATUS, 0x909b RP_RAND_SEED,
    0x909c RP_RELIABILITY_ECHO, 0x909d UHP_FINALIZE_RET, 0x9999 TERM, 0xf000 ATTR_EXTENSION.
  - Also standard 0x8020 XOR_MAPPED_ADDRESS_3489, 0x8022 SOFTWARE, 0x8023 ALTERNATE_SERVER, 0x8028 FINGERPRINT,
    0x802b RESPONSE_ORIGIN, 0x802c OTHER_ADDRESS.
- NAT tests: MappingTestIA/IB…IV, FilteringTestI/II, HairpingTest (strings 0x142a61790+). We already answer
  0xF000 MappingTestIA. **[log]**
- Keepalive to the STUN server **[log]** (server-*.log, every 5 s):
  `Binding req 0x0001 len 0x2c` + `9090/0x18: 10000001 00000000 3?000000 …` + `9091/0x0c: 0001 0104 00000000 <serial u32>`.
  - The RP_P2P_HEADER byte at offset 8 is 0x31 / 0x32 / 0x00. It looks like the ASCII player id ('1'/'2').
    **[guess]**
  - RP_RELIABILITY ends with a counter that goes up by 1 per answered keepalive.
- Peer handshake: the ntl events START_UDP_HOLE_PUNCHING_{COMPLETE,ERROR,PROGRESS,ADVICE_CANCEL_HAIRPIN/LOCAL,
  TRANSPORT_FINALIZE}, KEEP_UDP_HOLE_PUNCHING_*, STOP_UDP_HOLE_PUNCHING_* and host classes
  HOST_DIRECT/HOST_RELAY/PEER_HOST/PEER_REFLEXIVE/PEER_RELAYED/PEER_TURN (0x142a62828+) show the sequence:
  UHP_CONNECT to the peer's host and reflexive (and relayed) addresses from WATCH_MATCHING
  `host_addr / reflexive_addr / relayed_addr`, then UHP_KEEPALIVE, then UHP_FINALIZE / UHP_FINALIZE_RET.
  After that, `onlinesystem::NtlNetworkIo` (vt 0x1428b2628) is the kps SocketInterface, so dlcp/lltcp
  datagrams share that UDP socket. **[inferred from names]**
- Ports **[log]**: netshim shows peers sending to each other's ntl UDP ports (e.g. 192.168.1.100:25075 and :49126)
  in packets of **48 B** and **256 B**. 48 B = 20 STUN header + 28 (one RP_P2P_HEADER attribute), which fits a UHP
  message **[guess]**. Port choice is ntl::PortSelectPolicyAuto/Manual. netshim records sizes only, no
  payload bytes, so nothing more can be matched.
- START_SERVER_UDP_SESSION_COMPLETE and PUT_LOG_URL / LOG_ACTIVE / API_STATUS / SERVER_TIME (GateInfo.php keys,
  0x142a637f8+) exist. PUT_LOG_URL is where ntl uploads logs ("type=%s&prefix=…&dat="). **[static strings]**
- **Server should:** nothing new for direct P2P. For players behind symmetric NAT or CGNAT, provide a real
  RFC 5766 TURN service: GET_TURN_SERVER_LIST, then the client's ALLOCATE / CREATEPERMISSION / CHANNELBIND.
  The UDP-7600 stub is not enough. dlcp inside stays opaque to TURN.

## 5. VDPCHAT server type (voice chat)

- "VDPCHAT" is one of four svrtype constants (static init 0x14021e3f0: VDPCHAT, MENU, ACCOUNT, GATE).
- The svrlist add function 0x1411c71f0 (called from the GATE list builders 0x1411c6b70 / 0x1411de7c0 /
  0x1410f02a8 / 0x1412341a8) stores every entry at +0x1c8. When svrtype == "VDPCHAT" it also pushes the
  port/extra into a vector at **+0x4070**. The only other references to +0x4070 are a clear (0x1411c7959)
  and the destructor (0x1411c6fe0, called from 0x141149d20). **No consumer found.** **[static]**
- Voice chat itself is `onlinesystem::VoiceChatImpl` (vt 0x1428994d8), which calls the ktVoiceIn/ktVoiceOut codec
  (0x142055…, vt 0x142bcb430/0x142bcb4d8). It also has TaskVoiceChat, OnlineLobbyMultiStartVoiceChat (MOM lobby)
  and the "VoiceChatEnable" tunable. Neither path opens a VDPCHAT server connection, so the voice frames
  most likely ride the msp session. **[inferred]**
- **Server should:** not advertise VDPCHAT. If it ever appears in svrlist, it is stored and ignored. Confidence
  medium (an indirect reader through a copied pointer cannot be ruled out).

## 6. Match-layer P2P (MatchOnline*.cpp)

These files are compiled in: MatchOnlineSession, MatchOnlineCommandController, MatchOnlineDataSync,
MatchOnlineNegotiator, MatchOnlinePkOrderSynchronizer, MatchOnlineObserverForPesServer. Their messages travel
inside the msp session (classes MatchCommandP2pFullMeshSessionBufferingControl Ver1–4, vt 0x1428b1418…0x1428b1cf8).
**Their packet header was not decoded in this pass.**
- **MatchOnlineObserverForPesServer** with MultiplaySessionStrategyObserve (session type 3) suggests Konami
  could attach a server-side observer to a match. Our Quick Match uses type 1, and the observer is not
  needed. **[inferred]**

## Peer link capture 2026-11-05 (netshim NETSHIM_HEX=64, two copies on one LAN) [log]
- **Hole punching** = STUN-format messages (magic cookie 2112a442, 12-byte transaction id). Types 0x080b /
  0x080c / 0x080a are requests, 0x090b / 0x090c / 0x090a the success responses (class bit 0x100), in the order
  connect (0x0b) → keepalive (0x0c) → finalize (0x0a), matching UHP_CONNECT / KEEPALIVE / FINALIZE. One 256-byte
  0x080c carries a PADDING attribute (0x0026, probably a path-MTU probe). Vendor attributes: 0x9090 (24 bytes,
  `02 00 00 01 00 00 00 00` + the sender's pid as ASCII, e.g. 0x31 = "1"), 0x9091 / 0x909c (12 bytes,
  `00 01 01 04 ...` + a counter), 0x909d (4 bytes) in finalize responses.
- **Match link datagrams**: `u32 sequence` (big-endian, per direction, +1 per datagram) | `u16 flags`
  (0x0000 almost always; 0x8000, 0x4000, 0xc000 seen once each: control packets) | `u8 0xff` | payload that
  looks encrypted. No ack field in the clear header, so acks are inside the payload.
- **Key exchange direction**: sequence 0 goes from the host (pid 1, HOME/owner) first, a 7-byte payload;
  the guest answers with a 259-byte payload 5 ms later. Then both stream (27–63-byte datagrams during setup).
- Capture file (first 400 datagrams each way): research folder logs/netshim.log, from the 2026-11-05 16:00 run.

## Server action items (from this group)
1. Implement NCL fragmentation both ways: 0x70 {total, chunk_len, orig, zlib, chunk}, then 0x71/0x72
   {chunk_len, chunk}. Chunks are ≤ 0x3f3 for the first frame and ≤ 0x3fc after. Never send a frame body over 0x410.
2. Keep `key_size` ≠ 32 in SET_CURRENTPLAYER / RECONNECT, or else implement the AES tier above.
   Never send 0x10xx kinds unless a 32-byte key was handed out.
3. For NAT-hard players, run a real TURN server. dlcp / P2P needs no server code.
4. Do not list VDPCHAT.

## Still unknown
- The kps::lltcp / Clltcp datagram header (sequence, ack, window). It sits between dlcp and msp; settling it needs
  a peer↔peer payload capture. netshim logs sizes only. Adding a hex dump of the first ~64 bytes per sendmsg
  would settle it.
- Exact UHP_* attribute contents and the STUN type-code encoding of methods 0x80a–0x80c.
  Settle with the same capture during matchmaking.
- How session type (cfg+0x18: CS vs P2P vs Observe) is chosen. It is probably set by the matching/game-relay
  replies (CMD_GET_GAMERELAY `game_sycom`), which belong to the matchmaking group.
- Whether Konami's real server ever sent 32-byte keys (that is, whether retail traffic was AES).
- The direction of the dlcp 4-byte token and the 16-byte reply over UDP. Static reading is consistent with
  Active→token, Passive→RC4(static,K). Not traced end to end.
