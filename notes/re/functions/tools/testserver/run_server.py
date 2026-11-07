"""Run the repo server in host mode with test add-ons from this folder. Test-only; the repo code is not changed.
Usage, from pes2021-private-server/server:
  UPNP=0 PES_CONSOLE=0 PYTHONUNBUFFERED=1 PYTHONPATH=$PWD python3 <this file> > <log> 2>&1
ADVERTISE_GATE_PORT=10000 tells the game that gate port (side-by-side tests: netshim maps it to the real one).
TEST_DATA=<dir> uses that data folder instead of the host's (own players, identity, admin password).
Add-ons (env):
  PATCH_CTL=<file.json>  replypatch.py: edit/err any real reply from a control file, log every reply (scenario.py)
  LOBBYTEST=1            lobbytest.py: the old test Friendly Match Lobby handlers instead of the server's pesrooms
                         (also enables its ctl.json "err"/"manner_warn" overrides)
  HELLO_MAX=2            mod keeps its same-math hooks off
  DTLS=1                 dtlsleg.py: the old test DTLS leg (the server has its own since turndtls.py; needs TURN=0)
  COOP=1                 cooptest.py: Online Co-op 1 vs 1 test handlers
  LIVEDATA=1             livedata.py: test Live Update from www/, switched by ctl.json"""
import os, sys
sys.argv = ["pesserver", "host"] + (["--data", os.environ["TEST_DATA"]] if os.environ.get("TEST_DATA") else [])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("LOBBYTEST") == "1":
    import lobbytest  # noqa: F401,E402  (installs the handlers)
if os.environ.get("HELLO_MAX"):
    from pesserver import identity  # noqa: E402
    identity.HELLO_VERSION = int(os.environ["HELLO_MAX"])
if os.environ.get("DTLS") == "1":
    import dtlsleg  # noqa: F401,E402
if os.environ.get("COOP") == "1":
    import cooptest  # noqa: F401,E402
if os.environ.get("LIVEDATA") == "1":
    import livedata  # noqa: F401,E402
if os.environ.get("ADVERTISE_GATE_PORT"):
    # side-by-side test server (probesession): tell the game the standard gate port, which netshim maps to ours.
    # The mod adds its id line (install id, "coop1", "math1") only on connections to port 10000.
    from pesserver import pescmds  # noqa: E402
    pescmds._port = lambda svc, _p=int(os.environ["ADVERTISE_GATE_PORT"]): _p
from pesserver.__main__ import main  # noqa: E402
if os.environ.get("LOBBYTEST") == "1":
    lobbytest.install_err_overrides()   # after every add-on registered its handlers
if os.environ.get("PATCH_CTL"):
    import replypatch  # noqa: F401,E402  (last: wraps pescmds.dispatch)
main()
