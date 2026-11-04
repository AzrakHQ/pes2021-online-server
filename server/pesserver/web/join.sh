#!/bin/bash
# PES 2021 private server - player setup for Linux / Steam Deck (Steam + Proton).
#   join:  curl -fsSL http://@@SERVER@@/join | bash
#   undo:  curl -fsSL http://@@SERVER@@/leave | bash
# Points the game's Konami hostnames at the server (/etc/hosts, needs sudo) and trusts the server's
# certificate inside the game's Proton prefix only. Optional: PES_PREFIX=/path/to/pfx.
set -euo pipefail
SERVER="@@SERVER@@"
BASE="@@BASE@@"
UNINSTALL=@@UNINSTALL@@
MARK="# pes2021-private-server"
NAMES="pes21-x64-gate.cs.konami.net pes21-x64-stun.cs.konami.net ntl.service.konami.net
ntleu.service.konami.net ntlus.service.konami.net ntljp.service.konami.net"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsS "$BASE/install-ca-proton.py" -o "$TMP/ca.py"
curl -fsS "$BASE/ca.crt" -o "$TMP/ca.crt"

if [ "$UNINSTALL" = 1 ]; then
  python3 "$TMP/ca.py" --remove --ca "$TMP/ca.crt" ${PES_PREFIX:+"$PES_PREFIX"}
  [ "${PES_SKIP_HOSTS:-0}" = 1 ] || sudo sed -i "/$MARK/d" /etc/hosts
  echo "Done. PES 2021 no longer uses $SERVER."
  exit 0
fi

if [[ "$SERVER" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then IP="$SERVER"
else IP="$(getent ahostsv4 "$SERVER" | awk 'NR==1{print $1}')"; fi
[ -n "$IP" ] || { echo "Could not resolve $SERVER"; exit 1; }

python3 "$TMP/ca.py" --ca "$TMP/ca.crt" ${PES_PREFIX:+"$PES_PREFIX"}
if [ "${PES_SKIP_HOSTS:-0}" != 1 ]; then
  echo "Pointing the game's Konami hostnames at $IP (sudo may ask for your password)..."
  { sudo sed -i "/$MARK/d" /etc/hosts
    for n in $NAMES; do echo "$IP  $n  $MARK"; done | sudo tee -a /etc/hosts >/dev/null; }
fi
echo
echo "Done! Start PES 2021 -> eFootball -> Quick Match to play on $SERVER."
echo "Undo any time:  curl -fsSL $BASE/leave | bash"
