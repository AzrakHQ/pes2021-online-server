"""Run the repo server in host mode with the test Free-lobby handlers (lobbytest.py next to this file).
Usage, from pes2021-private-server/server:
  UPNP=0 PES_CONSOLE=0 PYTHONUNBUFFERED=1 PYTHONPATH=$PWD python3 <this file> > <log> 2>&1
Test-only; the repo code is not changed."""
import os, sys
sys.argv = ["pesserver", "host"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lobbytest  # noqa: F401,E402  (installs the handlers)
if os.environ.get("HELLO_MAX"):
    from pesserver import identity  # noqa: E402
    identity.HELLO_VERSION = int(os.environ["HELLO_MAX"])   # 2 = mod keeps its same-math hooks off
if os.environ.get("DTLS") == "1":
    import dtlsleg  # noqa: F401,E402  (TURN-over-DTLS second leg; needs TURN=1)
if os.environ.get("COOP") == "1":
    import cooptest  # noqa: F401,E402  (Online Co-op 1 vs 1 test handlers)
if os.environ.get("LIVEDATA") == "1":
    import livedata  # noqa: F401,E402  (test Live Update from www/, switched by ctl.json)
from pesserver.__main__ import main  # noqa: E402
lobbytest.install_err_overrides()   # after every add-on registered its handlers
main()
