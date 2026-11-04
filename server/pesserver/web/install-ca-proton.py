#!/usr/bin/env python3
"""Trust a PES 2021 private server's CA inside the game's Proton prefix only (Linux / Steam Deck).

The game's NAT test fetches https://ntl.service.konami.net/ntl/api/GateInfo.php through WinHTTP with
certificate validation, so the server's CA must be in Wine's machine Root store:
HKLM\\Software\\Microsoft\\SystemCertificates\\Root\\Certificates\\<SHA1> "Blob" =
[u32 CERT_CERT_PROP_ID=32][u32 1][u32 len][DER]. This script writes that key straight into the
prefix's system.reg (no Wine/Proton needed). Nothing outside the prefix is touched.

usage: install-ca-proton.py [--remove] --ca ca.crt [PREFIX]
  PREFIX defaults to Steam's prefix for PES 2021 (compatdata/1259970/pfx in any Steam library).
Close the game first: Wine rewrites system.reg when it exits.
"""
import glob, hashlib, os, re, ssl, struct, subprocess, sys, time

APPID = "1259970"
KEY = r"Software\\Microsoft\\SystemCertificates\\Root\\Certificates"


def steam_libraries():
    roots = ["~/.steam/steam", "~/.local/share/Steam", "~/.steam/root",
             "~/.var/app/com.valvesoftware.Steam/.local/share/Steam", "~/snap/steam/common/.local/share/Steam"]
    seen = []
    for r in roots:
        r = os.path.realpath(os.path.expanduser(r))
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        libs = [r] + (re.findall(r'"path"\s+"([^"]+)"', open(vdf, errors="replace").read())
                      if os.path.exists(vdf) else [])
        for lib in libs:
            lib = os.path.realpath(lib.replace("\\\\", "\\"))
            if lib not in seen and os.path.isdir(os.path.join(lib, "steamapps")):
                seen.append(lib)
    return seen


def find_prefix():
    for lib in steam_libraries():
        p = os.path.join(lib, "steamapps", "compatdata", APPID, "pfx")
        if os.path.isfile(os.path.join(p, "system.reg")):
            return p
    return None


def reg_block(thumb, blob):
    hexs = [f"{b:02x}" for b in blob]
    lines, line = [], '"Blob"=hex:'
    for i, h in enumerate(hexs):
        piece = h + ("," if i < len(hexs) - 1 else "")
        if len(line) + len(piece) > 78:
            lines.append(line + "\\")
            line = "  "
        line += piece
    lines.append(line)
    return f"[{KEY}\\\\{thumb}] {int(time.time())}\n" + "\n".join(lines) + "\n"


def strip_block(text, thumb):
    """Remove our key (header line through the blank line that ends it)."""
    pat = re.compile(r"\n\[" + re.escape(KEY) + r"\\{1,2}" + thumb + r"\][^\n]*\n(?:[^\n]+\n)*", re.I)
    return pat.sub("\n", text)


CA_CN = b"PES2021 Private Server CA"


def strip_ours(text):
    """Remove every Root certificate whose blob is a PES2021 Private Server CA (any server)."""
    pat = re.compile(r"\n\[" + re.escape(KEY) + r"\\{1,2}([0-9A-Fa-f]{40})\][^\n]*\n((?:[^\n]+\n)*)")

    def drop(m):
        hexs = re.sub(r"[^0-9a-f,]", "", m.group(2).split("=hex:", 1)[-1].lower())
        try:
            blob = bytes(int(h, 16) for h in hexs.split(",") if h)
        except ValueError:
            return m.group(0)
        return "\n" if CA_CN in blob else m.group(0)
    return pat.sub(drop, text)


def game_running():
    try:
        return subprocess.run(["pgrep", "-f", "PES2021.exe"], capture_output=True).returncode == 0
    except OSError:
        return False


def main():
    args = sys.argv[1:]
    remove = "--remove" in args
    if "--ca" not in args:
        sys.exit(__doc__)
    i = args.index("--ca"); ca = args[i + 1]; del args[i:i + 2]
    prefixes = [os.path.abspath(os.path.expanduser(a)) for a in args if a != "--remove"] or [find_prefix()]
    if prefixes == [None]:
        sys.exit("PES 2021's Proton prefix was not found. Start the game once from Steam, close it, and run "
                 "this again (or set PES_PREFIX=/path/to/compatdata/1259970/pfx).")
    if game_running():
        sys.exit("PES 2021 is running. Close the game, then run this again.")
    der = ssl.PEM_cert_to_DER_cert(open(ca).read())
    thumb = hashlib.sha1(der).hexdigest().upper()
    blob = struct.pack("<III", 32, 1, len(der)) + der
    for pfx in prefixes:
        reg = os.path.join(pfx, "system.reg")
        if not os.path.isfile(reg):
            sys.exit(f"not a Wine prefix (no system.reg): {pfx}")
        text = strip_ours(strip_block(open(reg, encoding="utf-8", errors="surrogateescape").read(), thumb))
        if not remove:
            text = text.rstrip("\n") + "\n\n" + reg_block(thumb, blob)
        tmp = reg + ".pes-tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)
        os.replace(tmp, reg)
        print(f"{'removed' if remove else 'trusted'} server certificate {thumb[:12]}… in {pfx}")


if __name__ == "__main__":
    main()
