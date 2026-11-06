#!/bin/bash
# copy 1 from the main menu (Kick Off tile, dialogs dismissed) -> Online Tournament -> competition -> France -> next screen
cd "$(dirname "$0")/../.."
K() { PES_INSTANCE=1 python3 tools/pes.py key "$@" >/dev/null; }
K Right; sleep 2; K Return; sleep 4; K Down sleep:1 Down sleep:1 Down sleep:1 Down; sleep 2; K Return; sleep 25
K Return; sleep 15          # "No new Live Update" -> competition list
K Return; sleep 6; K Right sleep:1 Return; sleep 8     # enter competition -> Yes
K Return; sleep 3; K Return; sleep 3; K Return; sleep 5; K Right sleep:1 Return; sleep 20   # notice, region, France, Yes
PES_INSTANCE=1 python3 tools/pes.py shot >/dev/null
