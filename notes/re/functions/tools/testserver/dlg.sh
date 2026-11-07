#!/bin/bash
# usage: dlg.sh TAG   (Kick Off list already open, cursor on Local Match) -> lobby entry, shots logs/dlg-TAG-N.png
cd "$(dirname "$0")/../.."
K() { PES_INSTANCE=1 python3 tools/pes.py key "$@" >/dev/null; }
S() { PES_INSTANCE=1 python3 tools/pes.py shot >/dev/null; cp logs/shot.png logs/dlg-$1-$2.png; }
K Return; sleep 3
K Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down sleep:1 Down; sleep 2; K Return; sleep 18; S $1 1
K Return; sleep 7; S $1 2; K Return; sleep 7; S $1 3
