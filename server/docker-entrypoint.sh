#!/bin/sh
# Starts the PES 2021 private server.
#   No configuration needed: with host networking it finds its LAN and public addresses, opens the
#   router's ports with UPnP (UPNP=0 to disable) and prints the admin page address and password.
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

# The CA is name-constrained to the game's four NTL hostnames (the only HTTPS names the game uses):
# players trust it, but it cannot vouch for any other website, Konami's real sites included. A CA from
# an older version (no or broader constraint) is replaced; players set up with the old join line rerun it (mod users: nothing to do).
NTL="ntl.service.konami.net ntleu.service.konami.net ntlus.service.konami.net ntljp.service.konami.net"
if [ ! -f "$CERTS/ca.crt" ] || ! openssl x509 -in "$CERTS/ca.crt" -noout -ext nameConstraints 2>/dev/null | grep -q "DNS:ntljp.service.konami.net"; then
  if [ -f "$CERTS/ca.crt" ]; then
    echo "[entrypoint] replacing the old certificate authority with a narrower one (players set up with the old join line: run it again; mod users: nothing to do)"
    mv "$CERTS/ca.crt" "$CERTS/ca.crt.old"
    date +%s > /tmp/pesserver-ca-replaced   # the startup banner tells the host players must rejoin
  fi
  echo "[entrypoint] creating the server certificate authority"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=PES2021 Private Server CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,permitted;DNS:ntl.service.konami.net,permitted;DNS:ntleu.service.konami.net,permitted;DNS:ntlus.service.konami.net,permitted;DNS:ntljp.service.konami.net,excluded;IP:0.0.0.0/0.0.0.0,excluded;IP:0:0:0:0:0:0:0:0/0:0:0:0:0:0:0:0" \
    -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2>/dev/null
  rm -f "$CERTS/server.crt"
fi
# The server certificate covers the four NTL hostnames only (no wildcards, no IP addresses).
if [ ! -f "$CERTS/server.crt" ]; then
  echo "[entrypoint] issuing the server certificate"
  openssl req -newkey rsa:2048 -nodes -subj "/CN=ntl.service.konami.net" \
    -keyout "$CERTS/server.key" -out "$CERTS/server.csr" 2>/dev/null
  cat > "$CERTS/leaf.ext" <<EOF
subjectAltName=DNS:ntl.service.konami.net,DNS:ntleu.service.konami.net,DNS:ntlus.service.konami.net,DNS:ntljp.service.konami.net
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF
  openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -days 825 -sha256 -extfile "$CERTS/leaf.ext" -out "$CERTS/leaf.crt" 2>/dev/null
  cat "$CERTS/leaf.crt" "$CERTS/ca.crt" > "$CERTS/server.crt"
  rm -f "$CERTS/server.csr"
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
