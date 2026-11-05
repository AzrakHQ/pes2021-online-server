#!/bin/sh
# Builds dinput8.dll (the PES 2021 Online mod) with the MinGW-w64 cross compiler.
#   Debian/Ubuntu: apt install gcc-mingw-w64-x86-64
set -eu
cd "$(dirname "$0")"
OUT="${1:-dinput8.dll}"
x86_64-w64-mingw32-gcc -O2 -Wall -Wno-unused-function -shared -o "$OUT" \
  pes2021online.c monocypher/monocypher.c monocypher/monocypher-ed25519.c minhook/src/hook.c minhook/src/buffer.c minhook/src/trampoline.c minhook/src/hde/hde64.c \
  dinput8.def -lws2_32 -lwinhttp -lbcrypt -luser32 -lgdi32 -static-libgcc -s -Wl,--no-insert-timestamp
echo "built $OUT ($(wc -c < "$OUT") bytes)"
