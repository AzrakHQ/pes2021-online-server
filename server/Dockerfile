FROM python:3.12-slim

RUN pip install --no-cache-dir msgpack==1.1.0 cryptography==44.0.1 \
 && apt-get update && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pesserver /app/pesserver
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# gate (NclMio) | HTTP web-API | HTTPS (NTL GateInfo.php) | STUN/NTL | game relay (placeholder)
EXPOSE 10000/tcp 8080/tcp 8443/tcp 3478/udp 7600/udp
VOLUME /data
ENTRYPOINT ["/app/docker-entrypoint.sh"]
