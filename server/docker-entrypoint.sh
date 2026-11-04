#!/bin/sh
# Starts the PES 2021 private server. On first start it creates /data/certs (a private CA + a TLS
# certificate for the Konami hostnames and $PUBLIC_IP; the certificate is reissued if PUBLIC_IP changes). Players must trust /data/certs/ca.crt
# (it is also downloadable from http://<server>:8080/ca.crt).
set -eu
: "${PUBLIC_IP:?set PUBLIC_IP to the address players use to reach this server (LAN or public IP)}"
HTTP_PORT="${HTTP_PORT:-8080}"
HTTPS_PORT="${HTTPS_PORT:-8443}"   # 443 when the container uses host networking
CERTS=/data/certs
mkdir -p "$CERTS"

if [ ! -f "$CERTS/ca.crt" ]; then
  echo "[entrypoint] creating the server CA (players trust this once: http://$PUBLIC_IP:$HTTP_PORT/ca.crt)"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=PES2021 Private Server CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" 2>/dev/null
fi
# (Re)issue the server certificate when missing or when PUBLIC_IP changed; the CA stays the same.
if [ ! -f "$CERTS/server.crt" ] || [ "$(cat "$CERTS/public_ip" 2>/dev/null)" != "$PUBLIC_IP" ]; then
  echo "[entrypoint] issuing server certificate for $PUBLIC_IP"
  openssl req -newkey rsa:2048 -nodes -subj "/CN=ntl.service.konami.net" \
    -keyout "$CERTS/server.key" -out "$CERTS/server.csr" 2>/dev/null
  cat > "$CERTS/leaf.ext" <<EOF
subjectAltName=DNS:*.konami.net,DNS:*.service.konami.net,DNS:*.cs.konami.net,DNS:ntl.service.konami.net,IP:$PUBLIC_IP,IP:127.0.0.1
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF
  openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial \
    -days 825 -sha256 -extfile "$CERTS/leaf.ext" -out "$CERTS/leaf.crt" 2>/dev/null
  cat "$CERTS/leaf.crt" "$CERTS/ca.crt" > "$CERTS/server.crt"
  rm -f "$CERTS/server.csr"
  echo "$PUBLIC_IP" > "$CERTS/public_ip"
fi

export PESSERVER_PUBLIC_ADDR="$PUBLIC_IP" PESSERVER_HTTP_PORT="$HTTP_PORT" PYTHONUNBUFFERED=1
export PESSERVER_RXLOG="${PESSERVER_RXLOG:-/data/rx.jsonl}"
exec python3 -m pesserver --db /data/pesserver.db serve \
  --host 0.0.0.0 --public-ip "$PUBLIC_IP" --codec ncl \
  --gate-port 10000 --http-port "$HTTP_PORT" --https-port "$HTTPS_PORT" --stun-port 3478
