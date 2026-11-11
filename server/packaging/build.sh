#!/bin/sh
# Builds the downloadable program: packaging/build.sh <output name>  ->  server/dist/<output name>
# Needs: pip install pyinstaller msgpack cryptography pyopenssl, and mod/dinput8.dll (mod/build.sh, or the CI artifact).
# Linux only (hosts run Linux). --strip drops debug symbols from Python and the libraries: 28 MB -> about 14 MB.
set -eu
cd "$(dirname "$0")/.."
[ -f mod/dinput8.dll ] || { echo "mod/dinput8.dll is missing: build it with mod/build.sh first"; exit 1; }
S=":"
pyinstaller --onefile --console --strip --noconfirm --clean --name "${1:-pes2021-server}" \
  --paths . --collect-submodules pesserver \
  --add-data "pesserver/web${S}pesserver/web" --add-data "pesserver/schemas.json${S}pesserver" --add-data "pesserver/team_list.json${S}pesserver" \
  --add-data "mod/dinput8.dll${S}mod" --add-data "mod/minhook/LICENSE.txt${S}mod" \
  --distpath dist --workpath build packaging/pes2021-server.py
