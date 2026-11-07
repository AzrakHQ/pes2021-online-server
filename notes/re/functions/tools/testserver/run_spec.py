"""Run the repo server (host mode, its own lobby/relay) plus the spectating add-on (spectest.py next to this file).
From pes2021-private-server/server:  UPNP=0 PES_CONSOLE=0 PYTHONPATH=$PWD python3 <this file> > <log> 2>&1
Optional: LIVEDATA=1 adds livedata.py (switchable Live Update listing). Test-only; the repo code is not changed."""
import os, sys
sys.argv = ["pesserver", "host"]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pesserver import pescmds, pesmatch, pesrooms  # noqa: E402,F401  (register the built-in handlers first)
try:
    from pesserver import pescoop  # noqa: F401,E402
except ImportError:
    pass
import spectest  # noqa: F401,E402
if os.environ.get("LIVEDATA") == "1":
    import livedata  # noqa: F401,E402
from pesserver.__main__ import main  # noqa: E402
main()
