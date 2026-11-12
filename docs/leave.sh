#!/bin/bash
# PES 2021 Online - undo the join line on Linux / Steam Deck, without needing the server.
#   curl -fsSL https://azrakhq.github.io/pes2021-online-server/leave.sh | bash
# Removes the /etc/hosts lines (sudo) and every PES2021 server certificate from the game's Proton prefix.
# Close the game first. Optional: PES_PREFIX=/path/to/pfx
set -euo pipefail
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsS https://raw.githubusercontent.com/AzrakHQ/pes2021-online-server/main/server/pesserver/web/install-ca-proton.py -o "$TMP/ca.py"
sudo sed -i "/# pes2021-private-server/d" /etc/hosts
echo "Removed the hosts-file lines."
python3 "$TMP/ca.py" --remove-all ${PES_PREFIX:+"$PES_PREFIX"} \
  || echo "Note: the certificate step was skipped (game folder not found, or the game is running). Close the game and run this again to remove it."
echo "Done. PES 2021 is back to normal on this PC."
