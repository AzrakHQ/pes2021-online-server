#!/bin/sh
# Builds the downloadable program: packaging/build.sh <output name>  ->  server/dist/<output name>
# Needs: pip install pyinstaller msgpack cryptography pyopenssl, and mod/dinput8.dll (mod/build.sh, or the CI artifact).
# Linux only (hosts run Linux). --strip drops debug symbols from Python and the libraries: 28 MB -> about 14 MB.
set -eu
cd "$(dirname "$0")/.."
[ -f mod/dinput8.dll ] || { echo "mod/dinput8.dll is missing: build it with mod/build.sh first"; exit 1; }
# Parts of Python the server never loads (about 1 MB): Chinese/Japanese/Korean text codecs, decimal,
# multiprocessing, readline, and the lzma/bz2 archive formats (shutil works without them).
# tests/test_packaging.py keeps a server import from needing any of them.
SKIP="_codecs_cn _codecs_hk _codecs_iso2022 _codecs_jp _codecs_kr _codecs_tw _multibytecodec
      decimal _decimal _pydecimal multiprocessing _multiprocessing readline lzma _lzma bz2 _bz2"
EXCLUDE=$(for m in $SKIP; do printf -- "--exclude-module %s " "$m"; done)
# shellcheck disable=SC2086
pyinstaller --onefile --console --strip --noconfirm --clean --name "${1:-pes2021-server}" $EXCLUDE \
  --paths . --collect-submodules pesserver \
  --add-data "pesserver/web:pesserver/web" --add-data "pesserver/schemas.json:pesserver" --add-data "pesserver/team_list.json:pesserver" \
  --add-data "mod/dinput8.dll:mod" --add-data "mod/minhook/LICENSE.txt:mod" \
  --distpath dist --workpath build packaging/pes2021-server.py
