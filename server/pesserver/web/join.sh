#!/bin/bash
# PES 2021 private server - player setup for Linux / Steam Deck (Steam + Proton).
#   join:  curl -fsSL @@BASE@@/join | bash
#   undo:  curl -fsSL @@BASE@@/leave | bash
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

if [ "$UNINSTALL" = 1 ]; then
  python3 "$TMP/ca.py" --remove-all ${PES_PREFIX:+"$PES_PREFIX"}
  [ "${PES_SKIP_HOSTS:-0}" = 1 ] || sudo sed -i "/$MARK/d" /etc/hosts
  echo "Done. PES 2021 no longer uses $SERVER."
  exit 0
fi

# The game needs an IP address. Use the first of the server's addresses that answers as this server
# (its LAN address when you're in the same house, its internet address otherwise).
ADDRS="@@ADDRS@@"
IP=""
for a in $ADDRS; do
  if curl -fsS -m 3 "http://$a:8080/status" 2>/dev/null | grep -q '"id": "@@SID@@"'; then IP="$a"; break; fi
done
[ -n "$IP" ] || IP="${ADDRS##* }"

curl -fsS "$BASE/ca.crt" -o "$TMP/ca.crt"
python3 "$TMP/ca.py" --ca "$TMP/ca.crt" ${PES_PREFIX:+"$PES_PREFIX"}
if [ "${PES_SKIP_HOSTS:-0}" != 1 ]; then
  echo "Pointing the game's Konami hostnames at $IP (sudo may ask for your password)..."
  { sudo sed -i "/$MARK/d" /etc/hosts
    for n in $NAMES; do echo "$IP  $n  $MARK"; done | sudo tee -a /etc/hosts >/dev/null; }
fi
echo
echo "Done! Start PES 2021 -> eFootball -> Quick Match to play on $SERVER."
echo "Undo any time:  curl -fsSL $BASE/leave | bash"
