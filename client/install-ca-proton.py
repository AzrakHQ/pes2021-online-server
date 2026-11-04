#!/usr/bin/env python3
"""Trust the server's CA inside the game's Proton prefix only (Linux / Steam Deck players).

The NTL library fetches https://ntl.service.konami.net/ntl/api/GateInfo.php through WinHTTP with default
certificate validation, so our TLS endpoint's CA must be trusted by Wine's crypt32. Wine reads the
machine Root store from HKLM\\Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\<SHA1>, value
"Blob" = serialized cert element: [u32 CERT_CERT_PROP_ID=32][u32 1][u32 len][DER].
Nothing outside the prefix (no host trust store) is touched.  Undo: --remove.

usage: install-ca-proton.py [--remove] --ca ca.crt PREFIX
  PREFIX for Steam: ~/.steam/steam/steamapps/compatdata/1259970/pfx   (1259970 = PES 2021)
  Proton's wine: $PROTON_WINE, default ~/.steam/steam/steamapps/common/Proton 10.0/files/bin/wine
Run it while the game is closed.
"""
import hashlib, os, ssl, struct, subprocess, sys, tempfile

PROTON = os.environ.get("PROTON_WINE") or os.path.expanduser(
    "~/.steam/steam/steamapps/common/Proton 10.0/files/bin/wine")
KEY = r"HKEY_LOCAL_MACHINE\Software\Microsoft\SystemCertificates\Root\Certificates"


def main():
    args = sys.argv[1:]
    remove = "--remove" in args
    if "--ca" not in args:
        sys.exit(__doc__)
    i = args.index("--ca"); ca = args[i + 1]; del args[i:i + 2]
    prefixes = [os.path.abspath(os.path.expanduser(a)) for a in args if a != "--remove"]
    if not prefixes or not all(os.path.isdir(p) for p in prefixes):
        sys.exit("give an existing Proton prefix directory (…/compatdata/1259970/pfx)")
    if not os.path.exists(PROTON):
        sys.exit(f"Proton wine not found at {PROTON}; set PROTON_WINE")
    der = ssl.PEM_cert_to_DER_cert(open(ca).read())
    thumb = hashlib.sha1(der).hexdigest().upper()
    blob = struct.pack("<III", 32, 1, len(der)) + der
    if remove:
        reg = f"Windows Registry Editor Version 5.00\r\n\r\n[-{KEY}\\{thumb}]\r\n"
    else:
        hexs = ",".join(f"{b:02x}" for b in blob)
        reg = f"Windows Registry Editor Version 5.00\r\n\r\n[{KEY}\\{thumb}]\r\n\"Blob\"=hex:{hexs}\r\n"
    with tempfile.NamedTemporaryFile("w", suffix=".reg", delete=False, encoding="utf-16") as f:
        f.write("﻿" + reg); path = f.name
    for pfx in prefixes:
        env = {**os.environ, "WINEPREFIX": pfx, "WINEDEBUG": "-all", "WINEFSYNC": "1"}
        r = subprocess.run([PROTON, "regedit", "/S", path], env=env, capture_output=True, text=True, timeout=120)
        if r.returncode:
            print(r.stdout[-800:], r.stderr[-800:])
            sys.exit(f"FAILED to update {pfx} (regedit rc={r.returncode}); is the game closed?")
        print(f"{'removed' if remove else 'installed'} CA {thumb} in {pfx}")
    os.unlink(path)


if __name__ == "__main__":
    main()
