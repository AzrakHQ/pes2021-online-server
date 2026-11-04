#!/bin/sh
# PES 2021 private server: one-command install for a Linux host (PC, home server, VPS, Raspberry Pi).
#
#   curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
#
# Installs Docker if it is missing, starts the server (it restarts by itself after a reboot), opens the
# router's ports with UPnP and prints the one-line command players run. Run it again to update.
# Optional env: PUBLIC_IP=<addr> (pin the address given to players), UPNP=0 (don't touch the router),
#               PES_IMAGE=<image> (default: the published image).
set -eu
IMAGE="${PES_IMAGE:-ghcr.io/mohamedalirashad/pes2021-private-server:latest}"
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

if [ "$(uname -s)" != Linux ]; then
  echo "This installer is for Linux. On Windows or macOS, install Docker Desktop and follow"
  echo "https://github.com/MohamedAliRashad/pes2021-private-server#windows-or-macos-host"
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
if ! $DOCKER pull -q "$IMAGE" >/dev/null 2>&1; then
  if [ -n "${PES_IMAGE:-}" ] && $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
    :   # a local image given explicitly
  else
    echo "Building the server from source (one time, about a minute)..."
    $DOCKER build -q -t "$IMAGE" "$REPO" >/dev/null || { echo "Could not build the server image."; exit 1; }
  fi
fi
$DOCKER rm -f pesserver >/dev/null 2>&1 || true
$DOCKER run -d --name pesserver --network host --restart unless-stopped \
  -e PUBLIC_IP="${PUBLIC_IP:-}" -e UPNP="${UPNP:-1}" -v pesserver-data:/data "$IMAGE" >/dev/null

i=0
until out="$(curl -fsS http://127.0.0.1:8080/banner 2>/dev/null)" && [ "$out" != starting ]; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then echo "The server did not start. See: $DOCKER logs pesserver"; exit 1; fi
  sleep 1
done
echo "$out"
echo " Manage it:  $DOCKER logs -f pesserver   |   $DOCKER restart pesserver   |   $DOCKER rm -f pesserver"
echo

# Playing on this PC too? Offer to set up the local game (needs a terminal to answer).
for d in "$HOME/.steam/steam" "$HOME/.local/share/Steam"; do
  if [ -d "$d/steamapps/compatdata/1259970/pfx" ] && [ -r /dev/tty ]; then
    printf "PES 2021 is installed on this PC. Set it up to play on this server too? [Y/n] "
    read -r ans < /dev/tty || ans=n
    case "$ans" in
      [nN]*) ;;
      *) lan="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
         curl -fsSL "http://${lan:-127.0.0.1}/join" | bash ;;
    esac
    break
  fi
done
