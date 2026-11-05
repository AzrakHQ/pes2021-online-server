#!/bin/sh
# PES 2021 private server: one-command install for a Linux host (PC, home server, VPS, Raspberry Pi).
#
#   curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/install.sh | sh
#
# Installs Docker if it is missing, starts the server (it restarts by itself after a reboot), opens the
# router's ports with UPnP and prints the admin page (where the host downloads the mod for friends).
# Run it again to update (it keeps PUBLIC_IP, ADMIN_REMOTE, ADMIN_PASSWORD and UPNP from the previous run).
# Optional env: PUBLIC_IP=<addr> (pin the address given to players), UPNP=0 (don't touch the router),
#               ADMIN_REMOTE=1 (admin page from any network, e.g. a cloud server; it isn't encrypted),
#               ADMIN_PASSWORD=<pw> (choose the admin password instead of a generated one),
#               PES_IMAGE=<image> (default: the published image).
set -eu
HOME="${HOME:-/root}"                      # cloud start-up scripts may run without it
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
if ! $DOCKER info >/dev/null 2>&1; then          # installed but not running (e.g. Arch): start it
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || $SUDO service docker start >/dev/null 2>&1 || true
  sleep 2
  $DOCKER info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start it (sudo systemctl start docker) and run this again."; exit 1; }
fi

bold "Starting the PES 2021 server..."
REPO="https://github.com/MohamedAliRashad/pes2021-private-server.git#main:server"
pulled=0
# A local image given explicitly (PES_IMAGE=name, already built here) needs no download.
if [ -n "${PES_IMAGE:-}" ] && [ "${IMAGE%%/*}" = "$IMAGE" ] && $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then pulled=1; fi
for try in 1 2 3; do
  [ "$pulled" = 1 ] && break
  if err="$($DOCKER pull -q "$IMAGE" 2>&1)"; then pulled=1; break; fi
  if [ "$try" -lt 3 ]; then echo "Download failed ($(printf '%s' "$err" | tail -n 1)), retrying..."; sleep 5; fi
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
# PES 2021 on this computer? Share only those game folders (and their Wine prefixes) with the
# server, so the admin page can put the mod in with one click.
prefix_for() {
  case "$1" in
    */steamapps/common/*)
      p="${1%%/steamapps/common/*}/steamapps/compatdata/1259970/pfx"
      [ -f "$p/user.reg" ] && { printf '%s' "$p"; return; } ;;
  esac
  for c in "$1/../proton/pfx" "$1/../pfx"; do
    [ -f "$c/user.reg" ] && { (cd "$c" && pwd -P); return; }
  done
}
GAMES_LIST="$(mktemp)"
{
  for root in "$HOME/.steam/steam" "$HOME/.local/share/Steam" "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"; do
    [ -f "$root/steamapps/libraryfolders.vdf" ] || continue
    printf '%s\n' "$root"
    sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$root/steamapps/libraryfolders.vdf"
  done | while IFS= read -r lib; do
    [ -d "$lib/steamapps/common" ] && find "$lib/steamapps/common" -maxdepth 2 -name PES2021.exe 2>/dev/null
  done
  [ -d "$HOME/Games" ] && find "$HOME/Games" -maxdepth 6 -name PES2021.exe -not -path '*/pfx/*' 2>/dev/null
} | while IFS= read -r exe; do (cd "$(dirname "$exe")" && pwd -P); done | sort -u > "$GAMES_LIST"
set --
n=0
while IFS= read -r d; do
  n=$((n + 1))
  set -- "$@" -v "$d:/games/$n" -e "PES_GAME_$n=$d"
  p="$(prefix_for "$d")"
  [ -n "$p" ] && set -- "$@" -v "$p:/prefixes/$n" -e "PES_PREFIX_$n=$p"
done < "$GAMES_LIST"
rm -f "$GAMES_LIST"

# Updating: keep the settings the server was started with unless they're given again (PUBLIC_IP= clears one).
if $DOCKER inspect pesserver >/dev/null 2>&1; then
  prev() { $DOCKER inspect -f '{{range .Config.Env}}{{println .}}{{end}}' pesserver 2>/dev/null | sed -n "s/^$1=//p" | head -n 1; }
  kept=""
  if [ -z "${PUBLIC_IP+x}" ]; then PUBLIC_IP="$(prev PUBLIC_IP)"; [ -n "$PUBLIC_IP" ] && kept="$kept PUBLIC_IP=$PUBLIC_IP"; fi
  if [ -z "${ADMIN_REMOTE+x}" ]; then ADMIN_REMOTE="$(prev ADMIN_REMOTE)"; [ -n "$ADMIN_REMOTE" ] && kept="$kept ADMIN_REMOTE=$ADMIN_REMOTE"; fi
  if [ -z "${ADMIN_PASSWORD+x}" ]; then ADMIN_PASSWORD="$(prev ADMIN_PASSWORD)"; [ -n "$ADMIN_PASSWORD" ] && kept="$kept ADMIN_PASSWORD=(your chosen one)"; fi
  if [ -z "${UPNP+x}" ]; then UPNP="$(prev UPNP)"; [ "${UPNP:-1}" != 1 ] && kept="$kept UPNP=$UPNP"; fi
  [ -n "$kept" ] && echo "Keeping your earlier settings:$kept (to clear one, run the install line with it empty, e.g. PUBLIC_IP=)"
fi
$DOCKER rm -f pesserver >/dev/null 2>&1 || true
$DOCKER run -d --name pesserver --network host --restart unless-stopped \
  -e PUBLIC_IP="${PUBLIC_IP:-}" -e UPNP="${UPNP:-1}" -e ADMIN_REMOTE="${ADMIN_REMOTE:-}" -e ADMIN_PASSWORD="${ADMIN_PASSWORD:-}" \
  -v pesserver-data:/data "$@" "$IMAGE" >/dev/null

i=0
until out="$($DOCKER exec pesserver cat /tmp/pesserver-banner.txt 2>/dev/null)" && [ -n "$out" ]; do
  i=$((i + 1))
  if [ "$i" -gt 90 ]; then echo "The server did not start. See: $DOCKER logs pesserver"; exit 1; fi
  sleep 1
done
printf '%s\n' "$out"
[ "$n" -gt 0 ] && printf '   PES 2021 found on this computer: install the mod from the admin page (The server computer).\n'
echo
