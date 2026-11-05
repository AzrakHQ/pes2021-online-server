"""pesserver: private matchmaking/auth server for PES 2021 (work in progress)."""
import os as _os

# Stamped at build time: the program carries a generated _build.py (.github/workflows/program.yml);
# PESSERVER_VERSION / PESSERVER_COMMIT override it. "dev" when run from a checkout.
try:
    from ._build import VERSION as _V, COMMIT as _C
except ImportError:
    _V = _C = ""
VERSION = _os.environ.get("PESSERVER_VERSION") or _V or "dev"
COMMIT = _os.environ.get("PESSERVER_COMMIT") or _C
