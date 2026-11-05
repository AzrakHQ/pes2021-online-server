# The PES 2021 Online mod (a Windows DLL players put next to PES2021.exe), cross-compiled here and
# served by the server at /mod.zip with the server's address filled in.
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS mod
RUN apt-get update && apt-get install -y --no-install-recommends gcc-mingw-w64-x86-64 \
 && rm -rf /var/lib/apt/lists/*
COPY mod /mod
RUN /mod/build.sh /mod/dinput8.dll

FROM python:3.12-slim

RUN pip install --no-cache-dir msgpack==1.1.0 cryptography==44.0.1 \
 && apt-get update && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*
# cloudflared (Cloudflare's official client) gives the server a free https://<words>.trycloudflare.com
# address for its web pages. TUNNEL=0 turns it off.
ARG CLOUDFLARED_VERSION=2025.8.1
RUN arch="$(dpkg --print-architecture)" && python3 -c "import urllib.request,sys; \
urllib.request.urlretrieve(sys.argv[1], '/usr/local/bin/cloudflared')" \
 "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${arch}" \
 && chmod +x /usr/local/bin/cloudflared && cloudflared --version

WORKDIR /app
COPY pesserver /app/pesserver
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
COPY --from=mod /mod/dinput8.dll /app/mod/dinput8.dll
COPY mod/minhook/LICENSE.txt /app/mod/MinHook-LICENSE.txt
RUN chmod +x /app/docker-entrypoint.sh

# player pages | HTTPS (NTL GateInfo.php) | HTTP web-API | gate (NclMio) | STUN | game relay (placeholder)
EXPOSE 80/tcp 443/tcp 8080/tcp 10000/tcp 3478/udp 7600/udp
VOLUME /data
ENTRYPOINT ["/app/docker-entrypoint.sh"]
