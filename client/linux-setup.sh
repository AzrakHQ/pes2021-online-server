#!/bin/bash
# PES 2021 private server - player setup for Linux / Steam Deck (Steam + Proton).
#   usage:  ./linux-setup.sh SERVER_IP            (asks for sudo to edit /etc/hosts)
#           ./linux-setup.sh --uninstall
# Optional env: PES_PREFIX (default: Steam's prefix for PES 2021), PROTON_WINE (Proton's wine binary).
# Close the game before running this.
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
MARK="# pes2021-private-server"
PREFIX="${PES_PREFIX:-$HOME/.steam/steam/steamapps/compatdata/1259970/pfx}"
NAMES="pes21-x64-gate.cs.konami.net pes21-x64-stun.cs.konami.net ntl.service.konami.net
ntleu.service.konami.net ntlus.service.konami.net ntljp.service.konami.net"

[ -d "$PREFIX" ] || { echo "Game prefix not found: $PREFIX (start the game once with Proton, or set PES_PREFIX)"; exit 1; }
sudo sed -i "/$MARK/d" /etc/hosts

if [ "${1:-}" = "--uninstall" ]; then
  CA="$HERE/.ca.crt"
  [ -f "$CA" ] && python3 "$HERE/install-ca-proton.py" --remove --ca "$CA" "$PREFIX"
  echo "Removed the hosts entries and the server certificate."
  exit 0
fi
SERVER="${1:?usage: $0 SERVER_IP}"

curl -fsS "http://$SERVER:8080/ca.crt" -o "$HERE/.ca.crt"
python3 "$HERE/install-ca-proton.py" --ca "$HERE/.ca.crt" "$PREFIX"
for n in $NAMES; do echo "$SERVER  $n  $MARK"; done | sudo tee -a /etc/hosts >/dev/null
echo "Done. PES 2021 will now go online through $SERVER.  Undo: $0 --uninstall"
