# The PES 2021 Online mod (a Windows DLL players put next to PES2021.exe), cross-compiled here and
# served by the server at /mod/dinput8.dll with the server's identity and addresses written in.
FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS mod
RUN apt-get update && apt-get install -y --no-install-recommends gcc-mingw-w64-x86-64 \
 && rm -rf /var/lib/apt/lists/*
COPY mod /mod
RUN /mod/build.sh /mod/dinput8.dll

FROM python:3.12-slim

RUN pip install --no-cache-dir msgpack==1.1.0 cryptography==44.0.1 \
 && apt-get update && apt-get install -y --no-install-recommends openssl \
 && rm -rf /var/lib/apt/lists/*
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
