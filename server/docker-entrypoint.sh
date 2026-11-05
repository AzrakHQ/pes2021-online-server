#!/bin/sh
# Starts the PES 2021 private server.
#   No configuration needed: with host networking it finds its LAN and public addresses, opens the
#   router's ports with UPnP (UPNP=0 to disable) and prints the admin page address and password.
#   PUBLIC_IP=<addr> pins the address handed to every client instead (bridge networking, VPS, VPN).
# On first start the server creates /data/certs: a private CA (players trust it via the /join script) and a
# TLS certificate for the Konami hostnames used by the game's network test (pesserver/certs.py).
set -eu
PUBLIC_IP="${PUBLIC_IP:-}"
HTTP_PORT="${HTTP_PORT:-8080}"
HTTPS_PORT="${HTTPS_PORT:-443}"
JOIN_PORT="${JOIN_PORT:-80}"
export PESSERVER_HTTP_PORT="$HTTP_PORT" PYTHONUNBUFFERED=1 PESSERVER_MAKE_CERTS=1
export PESSERVER_RXLOG="${PESSERVER_RXLOG:-/data/rx.jsonl}"
if [ -n "$PUBLIC_IP" ]; then
  export PESSERVER_PUBLIC_ADDR="$PUBLIC_IP"
  set -- --public-ip "$PUBLIC_IP"
else
  set -- --auto-net
fi
exec python3 -m pesserver --db /data/pesserver.db serve --host 0.0.0.0 --codec ncl "$@" \
  --gate-port 10000 --http-port "$HTTP_PORT" --https-port "$HTTPS_PORT" --join-port "$JOIN_PORT" --stun-port 3478
