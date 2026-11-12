#!/bin/sh
# PES 2021 server on your own Linux computer (or Steam Deck): no sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/AzrakHQ/pes2021-online-server/main/run.sh | sh
#
# Run it once. It downloads the newest server program, sets it up to run in the background (a systemd user
# service that starts when you log in), adds "PES 2021 Server" to your apps menu and opens the admin page.
# From then on you update, restart and stop the server on the admin page (Server). Running the line again
# repairs the setup and updates the program; players, results and friends' mods are kept.
# Options go after "sh -s --":  --here (run in this window instead, Ctrl+C stops it),
#   --no-autostart (don't start when you log in), --data DIR (where players and results are kept).
set -eu
REL="https://github.com/AzrakHQ/pes2021-online-server/releases/latest/download"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)                PLATFORM=linux-x64 ;;
  Linux-aarch64|Linux-arm64)   PLATFORM=linux-arm64 ;;
  *) echo "There's no ready-made server program for this computer ($(uname -sm))."
     echo "You can run it from source instead: https://github.com/AzrakHQ/pes2021-online-server/tree/main/server"
     exit 1 ;;
esac
DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pes2021-server"
mkdir -p "$DIR"
BIN="$DIR/pes2021-server"
VERFILE="$DIR/program-version.txt"
have="$(cat "$VERFILE" 2>/dev/null || true)"
latest="$(curl -fsSL --max-time 20 "$REL/version.txt" 2>/dev/null | head -n 1 || true)"

if [ -n "$latest" ] && { [ "$latest" != "$have" ] || [ ! -x "$BIN" ]; }; then
  if [ -n "$have" ] && [ -x "$BIN" ]; then echo "Updating the PES 2021 server: $have  ->  $latest"
  else echo "Downloading the PES 2021 server, version $latest..."; fi
  if curl -fL --progress-bar --max-time 600 "$REL/pes2021-server-$PLATFORM" -o "$BIN.new" && [ -s "$BIN.new" ]; then
    chmod +x "$BIN.new" && mv -f "$BIN.new" "$BIN" && printf '%s\n' "$latest" > "$VERFILE"
  else
    rm -f "$BIN.new"
    [ -x "$BIN" ] || { echo "The download failed. Check the internet connection and run the line again."; exit 1; }
    echo "The download failed; starting the version you have ($have)."
  fi
elif [ -z "$latest" ]; then
  [ -x "$BIN" ] || { echo "Can't reach GitHub to download the server. Check the internet connection and run the line again."; exit 1; }
  echo "Couldn't check for a newer version (offline?); starting the version you have ($have)."
fi
if [ "${1:-}" = "--here" ]; then shift; exec "$BIN" host "$@"; fi   # in this terminal, even when the service is installed
exec "$BIN" install "$@"
