# ktc TURN-over-DTLS leg: record framing (static RE, PES 2021 1.01.00)

> **Confirmed live (2026-11-06):** with the 2 trailer bytes removed before OpenSSL and every reply sent as a standard record whose plaintext ends with the type byte 0x00, both games completed 401 → authenticated Allocate → Refresh on the DTLS leg, closed the TLS leg, published `CMD_SEND_TURN_ADDRESS_DATA`, bound channels to each other's relayed address and exchanged ChannelData through the relay. Open: the whole TURN session restarts every 21.4 s (turncycle.md, if present; livetests.md).


Static only. No network traffic, no game launch. Addresses are VAs in PES2021.exe.

## Short answer

- The DTLS library is **stock OpenSSL 1.1.0i**, statically linked (paths `cobra\openssl\konami\openssl\...\openssl-1.1.0i\ssl\record\rec_layer_d1.c` and others). Its record writer `do_dtls1_write` (0x1421ebca0, error lines 1053/1069/1141 match 1.1.0i) is unmodified. **[static]**
- The odd shape comes from **two Konami wrapper layers in the ktc transport**, one on each side of OpenSSL. They are active only on the UDP/DTLS leg (transport mode `[t+0x30] == 0`). The TLS/TCP leg sends plain bytes. **[static 0x1412369c0, 0x141236520]**
  1. **Inside the encryption: a 1-byte "type" trailer is added to the plaintext.** Each STUN message is written to OpenSSL as `STUN || type`. Type is normally `0x00`. So the 65-byte plaintext is not strlen+1; it is a framing byte.
  2. **Outside the encryption: a 2-byte trailer is added after the last DTLS record, and that record's length field is raised by 2.** This is done on the finished ciphertext, after OpenSSL has built it (0x141235df0). OpenSSL's GCM covers only the real record, which is why the AAD length is 65 and the tag sits 2 bytes before the end.
- **The read path does NOT undo trailer 2.** Datagrams from the server go straight into OpenSSL's read BIO. So **server-to-client records must be plain, standard DTLS 1.2 records**, with no extra 2 bytes. After decryption the client **does** remove the 1-byte type trailer from the plaintext. **[static 0x141236902 → 0x1420732b0; 0x141236787..0x1412367e9]**
- Why both of your 401s failed:
  - The standard record had no type byte. The client took the last FINGERPRINT byte as "type": 253 times out of 256 it drops the message, otherwise it cuts it short. **[static 0x141236798]**
  - The client-shaped record had +2 in its length. The client's OpenSSL then looks for the GCM tag in the last 16 bytes of the record, the tag check fails, and DTLS drops the record silently. **[inferred: stock 1.1.0i behaviour on a bad record]**
- **What to send:** a normal OpenSSL `SSL_write(stun_reply + b"\x00")`. One STUN message per record. Nothing after the tag.

## Q1. Write path (client → server)

Call chain: STUN layer → ktc transport send pump `0x1412369c0` → `TlsSession` write thunk `0x14206c860` → `0x142073860` → `BIO_write(ssl_bio)` (`0x1421b2a20`) → OpenSSL → `0x14206c840` / `0x142073470` reads the network BIO (`SSL_get_wbio`, 0x1421b70d0) → trailer fixup `0x141235df0` → send `0x141236070`. **[static]**

The `TlsSession` uses OpenSSL with memory BIOs. The DTLS method is version `0x1ffff` = DTLS_ANY_VERSION (`0x1421b8cd0`). The TLS method is `0x10000` (`0x1421b8cf0`), chosen in `0x142072c40`. **[static]**

### Layer 1: plaintext type trailer (0x141236c60..0x141236d29) [static]
- `mode = 2` if `[t+0x17e] != 0 && word[t+0x17c] != 0`, else `mode = 0`. Header room is 5 bytes or 1 byte.
- The STUN message is read from the send queue (`0x1420616e0`).
- If mode is 2, 4 bytes are appended: `htonl(((key<<16)|key) ^ token)`, where key = `[t+0x17c]` and token = `[t+0x180]`. Then flag `[t+0x17e]` is cleared, so this happens once only.
- Then **1 byte `mode`** is appended (0x00 or 0x02).
- The ctor `0x141234ee0` zeroes key, flag and token (0x141234fdb..0x141234fe9). So the client always sends `STUN || 00` unless the server has sent it a key and a token first. That gives 64 + 1 = 65 bytes.

### Layer 2: post-encryption record trailer (0x141235df0, called at 0x141236bda only in DTLS mode) [static]
Inputs: `buf`, `n` = datagram length from OpenSSL, the capacity, and `key = word[t+0x17c]` (0 at start).
- It only acts if `n > 3 && buf[0] == 0x17 && buf[1] == 0xfe && ((buf[2]+3) & 0xfd) == 0`. That means application data with DTLS 1.2 (`fefd`) or DTLS 1.0 (`feff`). Handshake, CCS and alert datagrams are untouched, which is why the handshake works with stock OpenSSL.
- It walks the records via their length fields (`ntohs` at +11). The walk must end exactly at `n`, otherwise the function returns 0. Only the **last** record is changed.
- Last record (original length `L`): its length field becomes `L+2`. Then 2 bytes are appended at `buf[n]`, and the new datagram length is `n+2`.
- Trailer value (`s16` = big-endian word at `buf[9..10]`, the low 16 bits of the first record's sequence number):
  - `L == 0`: `v = s16`
  - `L == 1`: `v = buf[n-1] * 0x0101`
  - otherwise: `rem = s16 % (L // 2)`, then `v = BE16(buf[n-2-rem : n-rem])`, a word copied from the tag/ciphertext area.
  - trailer = `BE16(v ^ key)`.
- Your capture matches: `L = 8 + 65 + 16 = 89`, the length field says 91, and `rem = s16 % 44`.
- **Offline check, no game needed:** on any captured client datagram `d`, take `o = d[:-2]` and `n = len(o)`. Then `d[-2:]` should equal `o[n-2-rem : n-rem]` with `rem = int.from_bytes(o[9:11], 'big') % (89 // 2)`. If it matches, the model is confirmed.

## Q2. Read path (server → client)

Receive pump `0x141236520` (DTLS branch from 0x1412366fb). **[static]**
1. `0x141235f50` receives a datagram (up to 0x800 bytes). In mode 0 it **drops any datagram whose source address is not the directive's host:port** (compare `0x1403faec0`; it retries up to 0x400 times).
2. The datagram is passed **unchanged** to `BIO_write(SSL_get_rbio(ssl))` (`0x14206c830` → `0x1420732b0`: `BIO_ctrl(bio, BIO_C_GET_SSL=0x6e)`, then `0x14165dff0` = `ssl->rbio`, then `0x1421b2a20`). **There is no trailer strip and no length fix.** The record must be valid stock DTLS: 13-byte header, then 8-byte explicit nonce, ciphertext and 16-byte tag, with nothing after it. AAD = `epoch|seq, type, version, plaintext length`. OpenSSL's own `SSL_write` produces exactly this.
3. The plaintext is read through the SSL BIO (`0x14206c850` → `0x142073650`, up to 0x800 bytes, one record per read). Then the **last byte is a type** (0x141236787):
   - `0x00`: drop that 1 byte.
   - `0x01`: the 2 bytes before it are a new `key` (`ntohs`, stored in `[t+0x17c]`). Drop 3 bytes. **[static 0x1412367ba]**
   - `0x02`: the 4 bytes before it are a `token` (`ntohl`, stored in `[t+0x180]`, flag `[t+0x17e]=1`). Drop 5 bytes. **[static 0x14123679d]**
   - any other value: **the whole record is dropped**. **[static 0x1412367e9]**
   - The remaining bytes, if any, go to the STUN layer (`0x142061460`).
4. **Exact bytes to send:** a stock DTLS 1.2 record `17 fe fd | epoch(2) | seq(6) | len(2) | explicit_nonce(8) | GCM(STUN || 00) | tag(16)`, with `len = 8 + len(STUN) + 1 + 16`. Use the normal OpenSSL `SSL_write` of `STUN || 00`. Do not add the 2-byte trailer. **[static]**
5. **Server receive side:** for a client datagram starting `17 fe fd`/`17 fe ff`, walk the records, subtract 2 from the last record's length and drop the last 2 bytes. Only then pass it to OpenSSL. After `SSL_read`, remove the last byte (expect `00`; if it is `02`, also remove the 4 bytes before it). Do this only on the UDP leg. **[static]**
6. Never send type 1 or 2. Then key and token stay 0 and the client keeps sending `STUN || 00` with an unkeyed trailer. **[inferred]**

## Q3. Other checks on the DTLS leg

- **Source address filter:** replies must come from exactly the directive's `HOST:PORT` (0x141235f50 / 0x1403faec0). Use an IP literal in the directive; the host goes through `0x1403fb100` and the port through `0x1403fb210`. The handshake already passes this filter, so the same socket is fine. **[static; IP-literal advice inferred]**
- **Credentials:** the DTLS leg is a new ktc session built by the same connect function (`0x1411ee050` with dl=0, from `0x1411f2e15`). It uses the same `id`/`pw`/`origin`/cert from ParameterTurn (`0x1411cc3b0`, `0x1411cc3f0`, `0x1411cc230`, `0x1411cc2d0`/`0x1411cc280`). Only the transport mode is 0 and the address is the directive's. Its first Allocate has no USERNAME, so it expects the normal **401 {REALM, NONCE} → authenticated Allocate** flow, as on the TLS leg. It does not reuse the TLS allocation. **[static config; flow inferred from your capture]**
- **SOFTWARE on this leg:** the directive is parsed from the session in slot `+0x30`, the TLS leg (`0x1411f2980` → `0x14205fab0`). The DTLS session goes in slot `+0x78` (`0x1411ee428`). So a SOFTWARE value on DTLS replies is probably not parsed. **[inferred]** The directive's 3rd field must be `0` to open the leg. Values 1–2 and >2 take other paths (0x1411f2cd3, 0x1411f2ef4). **[static]**
- **Sequence, epoch, replay:** this is stock OpenSSL 1.1.0i DTLS: epoch 1 after CCS, a 64-record replay window, and bad records silently dropped. No custom checks were found in the read path. **[static/inferred]**
- **Size:** keep each STUN reply plus the type byte under 0x800 bytes (buffer checks at 0x141236603, 0x14123687e, 0x141236b7b). **[static]**
- **FINGERPRINT/MESSAGE-INTEGRITY** cover only the STUN message. The type byte is added below the STUN layer. **[inferred]**

## Live test that settles it

1. Offline first: run the trailer formula from Q1 on the captured datagrams.
2. Server: strip the 2 bytes as in Q2 step 5, `SSL_read`, and remove the `00`. Reply with `SSL_write(401 + b"\x00")`. Change nothing else.
3. **Proving signal:** the client stops repeating the bare Allocate within about 1 s. Its next DTLS record decrypts to an Allocate with USERNAME, REALM, NONCE and MESSAGE-INTEGRITY. Stop the test there.
4. Next step (later test): answer with an Allocate success (`… || 00`). The proof is `CMD_SEND_TURN_ADDRESS_DATA` in the server log.
