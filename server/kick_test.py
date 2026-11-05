#!/usr/bin/env python3
"""Kick an online player with a chosen result code, to see which error message PES 2021 shows.

Run it on the server computer while a game is signed in to eFootball:

    python3 server/kick_test.py                         # list who is online
    python3 server/kick_test.py ERR_ALREADYLOGGEDIN     # kick the first player online with that code
    python3 server/kick_test.py ERR_SERVERNOTREADY 2    # ...or the player with pid 2

The server answers the kicked game's next request (heartbeats come about every 17 s) with the code,
then disconnects it 2 s later (or after 25 s if nothing arrives). The admin password is read from the
pesserver container; pass PES_ADMIN_PASSWORD=... to use another one.
"""
import json, os, subprocess, sys, urllib.request

BASE = os.environ.get("PES_ADMIN_URL", "http://127.0.0.1")
pw = os.environ.get("PES_ADMIN_PASSWORD") or subprocess.check_output(
    ["docker", "exec", "pesserver", "cat", "/data/admin_password"]).decode().strip()


def call(path, body=None, tok=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {tok}"} if tok else {})
    return json.loads(urllib.request.urlopen(req, timeout=10).read() or b"{}")


tok = call("/admin/api/login", {"password": pw})["token"]
try:
    online = call("/admin/api/state", tok=tok)["online"]
    print("online:", ", ".join(f'{p["pid"]}: {p["name"]} ({p["status"]})' for p in online) or "nobody")
    if len(sys.argv) > 1 and online:
        target = next((p for p in online if len(sys.argv) > 2 and str(p["pid"]) == sys.argv[2]), online[0])
        call("/admin/api/kick", {"pid": target["pid"], "result": sys.argv[1]}, tok)
        print(f'kicked {target["name"]} with {sys.argv[1]}: watch the game for up to ~20 s')
finally:
    call("/admin/api/logout", {}, tok)
