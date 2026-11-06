# Test-server add-ons (reference code, not part of the released server)

Used for the live tests in `../../livetests.md`. They run the repo server in host mode and patch it at runtime, so
the repo code is not changed. Copy this folder anywhere outside the repo and run, from `server/`:

    UPNP=0 PES_CONSOLE=0 PYTHONPATH=$PWD python3 /path/to/testserver/run_server.py > server.log 2>&1

- `lobbytest.py`: Friendly Match Lobby handlers (blocks, room list, create/join, player list, entry, owner start,
  return to the room). Follows the rules in `../../rooms.md` corrections: RELOAD only as a reply, nested
  `psn_user_id` blobs, `game_env` echoed into the room list. `ctl.json` next to it can trigger one-off pushes and
  extra GET_SERVER_ENV keys.
- `dtlsleg.py` (`TURN=1 DTLS=1`): the TURN relay's second leg, TURN over DTLS 1.2 on UDP 5350, with the game's ktc
  framing (`../../ktcdtls.md`) and the dual-route SOFTWARE directive on the TLS leg. Needs pyOpenSSL ≥ 23 and
  `cryptography`.
- `livedata.py` (`LIVEDATA=1`): lists `www/<name>.cpk` in CMD_GET_DLC_FILELIST while `ctl.json` says
  `{"livedata": "<name>.cpk"}` and serves it on port 8090. Build a test CPK with `../mklivedata.py`.
- `cooptest.py` (`COOP=1`): Online Co-op 1 vs 1 handlers (force_alone, one WATCH_COOP_MATCHING push); `ctl.json` `coop_echo` echoes SYNC_PROGRESS, `gamerelay_p2p` answers GET_GAMERELAY with `game_sycom "P2P"`.
- `ctl.json` `{"err": {"CMD_X": "ERR_Y"}}` replies that error instead (dialog photos), `manner_warn`, `bad_p2p` (unreachable P2P addresses), `server_env`, one-off `push`.
- `HELLO_MAX=2`: answer the mod's hello with version 2 (same-math hooks off).
- `to_lobby.sh`: drives one game copy into the lobby, gated on server-log events (needs the game folder's
  `tools/pes.py`).
