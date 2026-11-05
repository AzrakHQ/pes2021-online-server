#!/bin/sh
# PES 2021 server on your own computer (Linux, Steam Deck, Mac): no Docker, no sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/MohamedAliRashad/pes2021-private-server/main/run.sh | sh
#
# Downloads the newest version of the server program (only when there is a new one), then runs it in this
# window. Ctrl+C stops it. Next time, run the same line: you always get the newest version.
# Options go after "sh -s --", e.g. ... | sh -s -- --data ~/pes-server-data
# Always-on server (VPS, home server, Pi) instead? Use install.sh (Docker): it starts by itself after a reboot.
set -eu
REL="https://github.com/MohamedAliRashad/pes2021-private-server/releases/latest/download"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)                PLATFORM=linux-x64 ;;
  Linux-aarch64|Linux-arm64)   PLATFORM=linux-arm64 ;;
  Darwin-arm64)                PLATFORM=macos-arm64 ;;
  *) echo "There's no ready-made server program for this computer ($(uname -sm))."
     echo "Use the Docker install instead: https://mohamedalirashad.github.io/pes2021-private-server/"
     exit 1 ;;
esac
if [ "$(uname -s)" = Darwin ]; then
  DIR="$HOME/Library/Application Support/PES2021 Server"
else
  DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pes2021-server"
fi
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
exec "$BIN" "$@"
