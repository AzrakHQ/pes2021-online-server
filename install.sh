#!/bin/sh
# PES 2021 server, always on: for a Linux server (VPS, home server, Raspberry Pi) with systemd.
# (Hosting from your own PC while you play? Use run.sh / run.ps1 instead: same program, in a window.)
#
#   curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
#
# Downloads the server program, runs it as a service that starts by itself after a reboot, opens the
# router's ports with UPnP and prints the admin page (where the host downloads the mod for friends).
# Run it again to update: it says which version you had and which you have now.
# Optional settings (kept for later runs; give one empty to clear it, e.g. PUBLIC_IP=):
#   PUBLIC_IP=<addr>      pin the address given to players (VPN, some cloud servers)
#   ADMIN_REMOTE=1        admin page from any network (it isn't encrypted)
#   ADMIN_PASSWORD=<pw>   choose the admin password instead of a generated one
#   UPNP=0                don't touch the router
#   UPDATE_CHECK=0        no "newer version" notice
# Remove: sudo systemctl disable --now pes2021-server && sudo rm -rf /opt/pes2021-server
#         /var/lib/pes2021-server /var/lib/private/pes2021-server /etc/pes2021-server.env /etc/systemd/system/pes2021-server.service
set -eu
REL="https://github.com/MohamedAliRashad/pes2021-private-server/releases/latest/download"
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

[ "$(uname -s)" = Linux ] && command -v systemctl >/dev/null 2>&1 || {
  echo "This installer is for Linux servers with systemd. On your own PC use the one-line start instead:"
  echo "https://mohamedalirashad.github.io/pes2021-private-server/#host"; exit 1; }
case "$(uname -m)" in
  x86_64) PLATFORM=linux-x64 ;;
  aarch64|arm64) PLATFORM=linux-arm64 ;;
  *) echo "There's no ready-made server program for this computer ($(uname -m))."; exit 1 ;;
esac
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"

# Settings: keep the earlier ones unless given again.
ENVF=/etc/pes2021-server.env
NEW="$(mktemp)"
for k in PUBLIC_IP ADMIN_REMOTE ADMIN_PASSWORD UPNP UPDATE_CHECK; do
  if eval "[ -n \"\${$k+x}\" ]"; then
    eval "v=\${$k}"
    [ -n "$v" ] && printf '%s=%s\n' "$k" "$v" >> "$NEW"
  elif [ -f "$ENVF" ]; then
    $SUDO sed -n "s/^$k=/&/p" "$ENVF" >> "$NEW"
  fi
done
kept="$(sed 's/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=(your chosen one)/' "$NEW" | tr '\n' ' ')"
[ -f "$ENVF" ] && [ -n "$kept" ] && echo "Settings: $kept"
$SUDO install -m 600 "$NEW" "$ENVF"; rm -f "$NEW"

# The program (downloaded only when there's a newer one).
DIR=/opt/pes2021-server
$SUDO mkdir -p "$DIR"
have="$(cat "$DIR/version.txt" 2>/dev/null || true)"
latest="$(curl -fsSL --max-time 20 "$REL/version.txt" | head -n 1)" || { echo "Can't reach GitHub to download the server."; [ -x "$DIR/pes2021-server" ] || exit 1; latest="$have"; }
if [ "$latest" != "$have" ] || [ ! -x "$DIR/pes2021-server" ]; then
  bold "Downloading the PES 2021 server, version $latest..."
  $SUDO curl -fL --progress-bar --max-time 600 "$REL/pes2021-server-$PLATFORM" -o "$DIR/pes2021-server.new"
  $SUDO chmod 755 "$DIR/pes2021-server.new" && $SUDO mv -f "$DIR/pes2021-server.new" "$DIR/pes2021-server"
  printf '%s\n' "$latest" | $SUDO tee "$DIR/version.txt" >/dev/null
fi

# The service: its own unprivileged user, allowed to use ports 80 and 443, restarts by itself.
$SUDO tee /etc/systemd/system/pes2021-server.service >/dev/null <<'EOF'
[Unit]
Description=PES 2021 server
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/opt/pes2021-server/pes2021-server host --data /var/lib/pes2021-server
EnvironmentFile=-/etc/pes2021-server.env
Environment=PES_SERVICE=1
DynamicUser=yes
StateDirectory=pes2021-server
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
$SUDO systemctl daemon-reload
$SUDO systemctl enable -q pes2021-server
BANNER=/var/lib/pes2021-server/banner.txt
$SUDO rm -f "$BANNER"                         # so only the new start's banner is shown
$SUDO systemctl restart pes2021-server
i=0
until $SUDO test -s "$BANNER"; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo "The server did not start. See: sudo journalctl -u pes2021-server -n 50"; exit 1; }
  sleep 1
done
$SUDO cat "$BANNER"
if [ -z "$have" ]; then bold "   Version $latest"
elif [ "$have" = "$latest" ]; then bold "   You have the newest version ($latest)."
else bold "   Updated: $have  ->  $latest"; fi
printf '   Always on: starts by itself after a reboot. Run this line again to update.\n\n'
