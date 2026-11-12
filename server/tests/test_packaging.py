"""packaging/build.sh leaves parts of Python out of the program (SKIP). If server code starts importing one of
them, the program would fail where running from source works: this catches it before a release."""
import os, re, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_server_module_needs_what_the_program_leaves_out():
    sh = open(os.path.join(HERE, "packaging", "build.sh")).read()
    skip = re.search(r'SKIP="([^"]+)"', sh).group(1).split()
    assert "decimal" in skip and "_codecs_jp" in skip
    probe = ("import sys, importlib, pkgutil, pesserver\n"
             "for m in pkgutil.walk_packages(pesserver.__path__, 'pesserver.'): importlib.import_module(m.name)\n"
             "import shutil, asyncio, ssl, sqlite3, json, urllib.request, xml.etree.ElementTree, cryptography.x509, OpenSSL.SSL\n"
             f"print(' '.join(m for m in {skip!r} if m in sys.modules and m not in ('lzma', '_lzma', 'bz2', '_bz2')))\n")
    out = subprocess.run([sys.executable, "-c", probe], cwd=HERE, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", "the program leaves these out but the server imports them: " + out.stdout


def test_shutil_still_works_without_lzma_and_bz2():
    """shutil is the one user of lzma/bz2, and only tries them (try/except ImportError)."""
    probe = ("import sys; sys.modules['lzma'] = sys.modules['bz2'] = None\n"
             "import shutil, tempfile, os; d = tempfile.mkdtemp(); open(os.path.join(d, 'a'), 'w').write('x')\n"
             "shutil.copytree(d, d + '-copy'); shutil.rmtree(d); shutil.rmtree(d + '-copy'); print('ok')\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr
