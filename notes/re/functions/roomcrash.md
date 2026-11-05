# Room crash after CREATEJOIN: partial static analysis (incomplete)

> **Live re-test (2026-11-05, 20:12):** with both fixes applied (own room pushed into the room list 50 ms after CREATEJOIN_ROOM, before the player list; `pname.psn_user_id` as a blob `{account_id, online_id, is_valid_cache}`; no `guest_info`), the game **still exits**, about 0.2 s after the player list. ENTRY_GAME is never sent. So the room-list-first rule and the blob format are requirements, but something else in the player-list entry (or right after it) still ends the process. Still open.


Tags: [static] read at the address, [log] our server logs, [inferred].
Dumps are in the working folder (not published) (fn_*.asm, range_lobby.asm, iat.txt).

## What the client needs before it sends ENTRY_GAME (owner path)
OnlineLobbyFreePostCreateJoinRoom 0x14137a8f0 (state is at +0xb8; jump table 0x14137ae80):
- st0: saves the current tick to +0xc0. Registers CmdReqwatchlRoomplayerlistNtl (0x1411287c0),
  CmdWatchDecideGameenvFree (0x141128900) and CmdWatchEntryGameFreeLobby (0x141128a40).
- st1: waits until `[[R+0x30]+0xd8] > start tick`. R = [[0x1436fced8]+0x20] is the lobby room model.
  The tick is written by the ROOMPLAYERLIST consumer 0x141096320 (at 0x14109660a). This works today [log].
- st2: needs R+0x38 (created by 0x14103d1d0). Then it calls 0x141040b50(own pid), which calls
  0x1410409a0 → 0x14104c950(R+0x20, room_id=[[R+8]+0x1c], pid).
  **R+0x20 is the ROOM-LIST model.** It is filled only by the REQWATCHL_ROOMLIST consumer 0x1410e06e0,
  which reads 0x338-byte elements and rebuilds the whole map on every message. So **our own room, with our
  pid in its `gamer` list, must already be in the room list**. If it isn't, the client reports error
  PostCreateJoinRoom.cpp:130 (0x14137ab38) and goes to state 6, then to the "return" flow
  (LobbyPrcLeaveRoomCreateFailedDlg).
  If the lookup succeeds, 0x14103fcf0 also checks the player list (0x14103fb80 → 0x14103e630) and then
  creates "CmdEntryGame".
- st3: polls the ENTRY_GAME reply. st4 creates "TaskLobbyRoomMainFree". st5 = proceed.

Our test server sends the room-list ADD only 3 s after CREATEJOIN, and only to subscribers. In every run the
game was gone by then [log server-lobby*.log]. **So state 2 can never succeed as things stand.**

## ROOMPLAYERLIST element: what parser 0x141bb80f0 reads [static]
- The list_data element and the values host/reflexive/relayed_addr, pname, guest_info and pname.psn_user_id
  are all unpacked again with 0x141c190a0. That function only accepts str/bin bytes, and its type is not
  checked. **0x141c190a0 leaves its output count and pointer unwritten when unpack fails**
  (0x141c19111 → 0x141c1915b). An inline array/map, or an empty value, at one of these keys therefore leaves
  stack garbage in the count and pointer. **Our `psn_user_id: []` (0x90) is such a case and is undefined
  behaviour. Do not send it.**
- pname = blob{name S → e+0x60, xuid_one S, psn_user_id blob{account_id N (u64), online_id S,
  is_valid_cache S}, is_deleted S}. is_valid_cache is read only inside psn_user_id; account_id and online_id
  at the pname top level are ignored.
- Other fields:
  - pid N → e+0x4c
  - room_player_id, room_entry_no, game_entry_no N → e+0x50..0x58. The client treats game_entry_no ≥ 0 as
    "entered".
  - lang, is_owner, side, is_quality_bad, has_guest: S
  - guest_info blob{pname blob, is_platform_guest S}
  - addresses: blob{address_family N, sa_data S, base64 into a 14-byte field}
  - ntl_nat_type N
- Consumer 0x141096320 copies pid, the addresses, the nat type, the pname name and is_deleted. If is_deleted
  is YES, the name is replaced by a localized string. It reads the guest only when has_guest == YES.

## Process exit: not found yet
- The exits I checked don't apply here: game-code ExitProcess callers are only libpng error paths
  (0x142097640…); exit() is called only at window init (0x1404058d0); PostQuitMessage only from the window
  proc.
- The error reporter 0x1411546b0 only queues errors.
- Timing [log]: when psn_user_id was a bin of an empty map (`c4 01 80`), the game died about 0.27 s after the
  first player list (lobby-2 and lobby-3 runs). With `[]` it died about 1.85 s after (lobby-4 and the last
  run). The exact exit path is still unknown.

## Server should send (corrected)
1. **Immediately on CREATEJOIN_ROOM**, before or together with the first player list, push to the owner (rqid 0):
   `CMD_REQWATCHL_ROOMLIST {result NOERR, list_type "ADD", list_key_name "room_id", list_num 1,
   list_data [room_entry]}`.
   - `room_entry` must contain `room_id` and `gamer: [blob{pid: owner, is_room_owner "YES", side "HOME",
     enter_no 1, game_no 0}]`, and `gamer_num 1`.
   - Send it even if the owner never subscribed.
2. ROOMPLAYERLIST element: send `pname` as a blob of
   `{name, xuid_one "", psn_user_id: bin(packb({account_id: pid, online_id: "", is_valid_cache: "YES"})),
   is_deleted "NO"}`.
   - Every nested value must be bin (or str) bytes. Never send an inline [] or {} there.
   - When has_guest is "NO", leave guest_info out.
   - Keep the address blobs as stored.
   - ntl_nat_type: check that 0x10307533 is really what SEND_IPANDPORT_NTL sent.
3. Answer the REQWATCHL_ROOMPLAYERLIST request with RELOAD after the room-list push. One player-list push is
   enough.

## Still unknown
- The mechanism that ends the process. A Wine log that keeps everything (+seh, plus the exit code of the
  process) from the next run would settle it.
- Whether the room-list fix alone stops the exit. Test: push the ADD with CREATEJOIN, then watch for
  ENTRY_GAME, or for LEAVE_ROOM (which would mean error 130 still happens).
