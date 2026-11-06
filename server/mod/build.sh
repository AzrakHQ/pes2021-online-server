#!/bin/sh
# Builds dinput8.dll (the PES 2021 Online mod) with the MinGW-w64 cross compiler.
#   Debian/Ubuntu: apt install gcc-mingw-w64-x86-64
set -eu
cd "$(dirname "$0")"
OUT="${1:-dinput8.dll}"
CC=x86_64-w64-mingw32-gcc
OBJ="$(mktemp -d)"
trap 'rm -rf "$OBJ"' EXIT
# The game's math (musl-math/README.md): own names, and flags that give the same bits on every PC.
for f in musl-math/*.c; do
  $CC -O2 -march=x86-64 -mno-fma -ffp-contract=off -fno-fast-math -fno-builtin -Imusl-math -Imusl-math/include -Imusl-math/mingw -include musl-math/pesm_names.h \
    -c "$f" -o "$OBJ/$(basename "$f" .c).o"
done
$CC -O2 -march=x86-64 -Wall -Wno-unused-function -ffp-contract=off -shared -o "$OUT" \
  pes2021online.c monocypher/monocypher.c monocypher/monocypher-ed25519.c minhook/src/hook.c minhook/src/buffer.c minhook/src/trampoline.c minhook/src/hde/hde64.c \
  "$OBJ"/*.o dinput8.def -lws2_32 -lwinhttp -lbcrypt -luser32 -lgdi32 -ladvapi32 -static-libgcc -s -Wl,--no-insert-timestamp
echo "built $OUT ($(wc -c < "$OUT") bytes)"
