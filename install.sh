#!/bin/sh
# PES 2021 private server: one-command install for a Linux host (PC, home server, VPS, Raspberry Pi).
#
#   curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
#
# Installs Docker if it is missing, starts the server (it restarts by itself after a reboot), opens the
# router's ports with UPnP and prints the admin page (where the host downloads the mod for friends).
# Run it again to update.
# Optional env: PUBLIC_IP=<addr> (pin the address given to players), UPNP=0 (don't touch the router),
#               PES_IMAGE=<image> (default: the published image).
set -eu
IMAGE="${PES_IMAGE:-ghcr.io/mohamedalirashad/pes2021-private-server:latest}"
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

if [ "$(uname -s)" != Linux ]; then
  echo "This installer is for Linux. On Windows or macOS, use Docker Desktop:"
  echo "https://mohamedalirashad.github.io/pes2021-private-server/#faq-winhost"
  exit 1
fi
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"

if ! command -v docker >/dev/null 2>&1; then
  bold "Installing Docker (official script from get.docker.com)..."
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
fi
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"

bold "Starting the PES 2021 server..."
REPO="https://github.com/MohamedAliRashad/pes2021-private-server.git#main:server"
pulled=0
for try in 1 2 3; do
  if err="$($DOCKER pull -q "$IMAGE" 2>&1)"; then pulled=1; break; fi
  if [ "$try" -lt 3 ]; then echo "Download failed, retrying..."; sleep 3; fi
done
if [ "$pulled" = 0 ]; then
  if [ -n "${PES_IMAGE:-}" ] && $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
    :   # a local image given explicitly
  elif $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Could not download the latest version ($(printf '%s' "$err" | tail -n 1)); using the copy already on this computer."
  else
    echo "Could not download the server ($(printf '%s' "$err" | tail -n 1))."
    echo "Building it from source instead (one time, about a minute)..."
    $DOCKER build -q -t "$IMAGE" "$REPO" >/dev/null || { echo "Could not build the server image."; exit 1; }
  fi
fi
$DOCKER rm -f pesserver >/dev/null 2>&1 || true
$DOCKER run -d --name pesserver --network host --restart unless-stopped \
  -e PUBLIC_IP="${PUBLIC_IP:-}" -e UPNP="${UPNP:-1}" -v pesserver-data:/data "$IMAGE" >/dev/null

i=0
until out="$($DOCKER exec pesserver cat /tmp/pesserver-banner.txt 2>/dev/null)" && [ -n "$out" ]; do
  i=$((i + 1))
  if [ "$i" -gt 90 ]; then echo "The server did not start. See: $DOCKER logs pesserver"; exit 1; fi
  sleep 1
done
printf '%s\n\n' "$out"
