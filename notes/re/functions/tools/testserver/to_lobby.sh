#!/bin/bash
# Drive one copy from the main menu (cursor on the Kick Off tile) into the Friendly Match Lobby, gated on server-log
# events instead of fixed sleeps.   usage: to_lobby.sh INSTANCE PID create|join|list
cd "$(dirname "$0")/../.."
I=$1; P=$2; M=$3; L=logs/server.log
K() { PES_INSTANCE=$I python3 tools/pes.py key "$@" >/dev/null; }
mark() { wc -l < $L; }
waitfor() {  # waitfor START PATTERN TIMEOUT
  local s=$1 pat=$2 t=$3 end=$(( $(date +%s) + $3 ))
  while [ $(date +%s) -lt $end ]; do tail -n +$s $L | grep -aq -- "$pat" && return 0; sleep 1; done
  echo "TIMEOUT waiting for: $pat"; return 1; }
s=$(mark); K Return; sleep 3
K Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down; sleep 2; K Return
waitfor $s "CMD_GET_DLC_FILELIST" 60 && { sleep 4; s=$(mark); K Return; }
waitfor $s "LOBBY RX CMD_REQWATCHL_ROOMLIST pid=$P" 60 || exit 1
sleep 3
if [ "$M" = create ]; then
  s=$(mark); K Return; sleep 12; K Return; sleep 4; K Return; sleep 4; K Return
  waitfor $s "LOBBY RX CMD_ENTRY_GAME pid=$P" 40 && echo "room created"
elif [ "$M" = join ]; then
  s=$(mark); K Down sleep:1 Return
  waitfor $s "LOBBY RX CMD_REQWATCHL_ROOMPLAYERLIST_NTL pid=$P" 30 && { sleep 3; s=$(mark); K Return; }
  waitfor $s "LOBBY RX CMD_ENTRY_GAME pid=$P" 30 && echo "joined"
fi
PES_INSTANCE=$I python3 tools/pes.py shot >/dev/null; cp logs/shot.png logs/lobby-$I.png
