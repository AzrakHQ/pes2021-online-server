#!/bin/sh
# Starts the PES 2021 private server.
#   No configuration needed: with host networking it finds its LAN and public addresses, opens the
#   router's ports with UPnP (UPNP=0 to disable) and prints the one-line command players run.
#   PUBLIC_IP=<addr> pins the address handed to every client instead (bridge networking, VPS, VPN).
# On first start it creates /data/certs: a private CA (players trust it via the /join script) and a TLS
# certificate for the Konami hostnames used by the game's network test.
set -eu
PUBLIC_IP="${PUBLIC_IP:-}"
HTTP_PORT="${HTTP_PORT:-8080}"
HTTPS_PORT="${HTTPS_PORT:-443}"
JOIN_PORT="${JOIN_PORT:-80}"
CERTS=/data/certs
mkdir -p "$CERTS"

if [ ! -f "$CERTS/ca.crt" ]; then
  echo "[entrypoint] creating the server certificate authority"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=PES2021 Private Server CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2>/dev/null
fi
CERT_ID="${PUBLIC_IP:-auto}"
# (Re)issue the server certificate when missing or when PUBLIC_IP changed; the CA stays the same.
if [ ! -f "$CERTS/server.crt" ] || [ "$(cat "$CERTS/public_ip" 2>/dev/null)" != "$CERT_ID" ]; then
  echo "[entrypoint] issuing the server certificate"
  SAN="DNS:*.konami.net,DNS:*.service.konami.net,DNS:*.cs.konami.net,DNS:ntl.service.konami.net,IP:127.0.0.1"
  case "$PUBLIC_IP" in
    *[!0-9.]*|"") ;;
    *) SAN="$SAN,IP:$PUBLIC_IP" ;;
  esac
  openssl req -newkey rsa:2048 -nodes -subj "/CN=ntl.service.konami.net" \
    -keyout "$CERTS/server.key" -out "$CERTS/server.csr" 2>/dev/null
  cat > "$CERTS/leaf.ext" <<EOF
subjectAltName=$SAN
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF
  openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -days 825 -sha256 -extfile "$CERTS/leaf.ext" -out "$CERTS/leaf.crt" 2>/dev/null
  cat "$CERTS/leaf.crt" "$CERTS/ca.crt" > "$CERTS/server.crt"
  rm -f "$CERTS/server.csr"
  echo "$CERT_ID" > "$CERTS/public_ip"
fi

export PESSERVER_HTTP_PORT="$HTTP_PORT" PYTHONUNBUFFERED=1
export PESSERVER_RXLOG="${PESSERVER_RXLOG:-/data/rx.jsonl}"
if [ -n "$PUBLIC_IP" ]; then
  export PESSERVER_PUBLIC_ADDR="$PUBLIC_IP"
  set -- --public-ip "$PUBLIC_IP"
else
  set -- --auto-net
fi
exec python3 -m pesserver --db /data/pesserver.db serve --host 0.0.0.0 --codec ncl "$@" \
  --gate-port 10000 --http-port "$HTTP_PORT" --https-port "$HTTPS_PORT" --join-port "$JOIN_PORT" --stun-port 3478
