"""The host program on Linux (hostctl.py): install files, self-update from a release, restart/stop, autostart.

Uses a fake release folder (file:// URLs), a fake program file and a fake systemctl: nothing touches the
real systemd user session, the real program or GitHub."""
import asyncio, hashlib, json, os, stat
from types import SimpleNamespace

import pytest

from pesserver import events, hostctl, httpgate, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service

NEW_PROGRAM = "#!/bin/sh\n[ \"$1\" = selfcheck ] && echo 'pes2021-server ok 2099-01-01 (abcdef1)'\n"


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


@pytest.fixture
def release(tmp_path, monkeypatch):
    """A release folder with version.txt, SHA256SUMS and the program; a 'running program' to replace."""
    rel = tmp_path / "rel"; rel.mkdir()
    name = hostctl.asset() or "pes2021-server-linux-x64"
    (rel / name).write_text(NEW_PROGRAM)
    (rel / "version.txt").write_text("2099-01-01 (abcdef1)\n")
    (rel / "SHA256SUMS").write_text(f"{hashlib.sha256(NEW_PROGRAM.encode()).hexdigest()}  {name}\n")
    prog = tmp_path / "bin" / "pes2021-server"; prog.parent.mkdir(); prog.write_text("old program")
    monkeypatch.setenv("PESSERVER_RELEASE_URL", rel.as_uri())
    monkeypatch.setattr(hostctl, "program", lambda: str(prog))
    monkeypatch.setattr(hostctl, "asset", lambda: name)
    monkeypatch.setattr(hostctl, "manageable", lambda: (True, ""))
    ended = []
    monkeypatch.setattr(hostctl, "_end", lambda after: ended.append(after))
    monkeypatch.setattr(hostctl.time, "sleep", lambda s: None)
    hostctl.STATE.update(update="idle", error="", progress=0, target="", when="")
    return SimpleNamespace(rel=rel, name=name, prog=prog, ended=ended)


def _wait():
    t = hostctl._CTL["thread"]
    if t:
        t.join(10)


def test_unit_and_menu_entry_text(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    u = hostctl.unit_text("/home/me/.local/share/pes2021-server/pes2021-server", "/home/me/.local/share/pes2021-server")
    assert 'ExecStart="/home/me/.local/share/pes2021-server/pes2021-server" host --data' in u
    assert "PES_SERVICE=1" in u and "Restart=on-failure" in u and "WantedBy=default.target" in u
    d = hostctl.desktop_text("/x/pes2021-server")
    assert 'Exec="/x/pes2021-server" open' in d and "Name=PES 2021 Server" in d
    assert hostctl.unit_path() == str(tmp_path / "cfg" / "systemd" / "user" / "pes2021-server.service")


def test_from_source_it_says_why_it_cannot_update(svc):
    ok, why = hostctl.manageable()
    assert not ok and why
    st, res = hostctl.admin_api(svc, "/admin/api/program/update", "POST", b'{"when": "now"}')
    assert st.startswith("409") and res["error"] == why
    st, res = hostctl.admin_api(svc, "/admin/api/program", "GET", b"")
    assert st.startswith("200") and res["can_update"] is False and res["version"]


def test_update_now_swaps_the_program_and_restarts(svc, release):
    hostctl.update(svc, "now"); _wait()
    assert release.prog.read_text() == NEW_PROGRAM and os.stat(release.prog).st_mode & stat.S_IXUSR
    assert (release.prog.parent / "program-version.txt").read_text().strip() == "2099-01-01 (abcdef1)"
    assert release.ended == ["restart"] and hostctl.STATE["update"] == "restarting"
    assert any("Updating the server to 2099-01-01" in f["text"] for f in events._st(svc)["feed"])


def test_a_bad_download_is_never_swapped_in(svc, release):
    (release.rel / "SHA256SUMS").write_text(f"{'0' * 64}  {release.name}\n")
    hostctl.update(svc, "now"); _wait()
    assert release.prog.read_text() == "old program" and release.ended == []
    assert hostctl.STATE["update"] == "error" and "checksum" in hostctl.STATE["error"]
    assert not os.path.exists(str(release.prog) + ".new")


def test_a_program_that_fails_its_selfcheck_is_never_swapped_in(svc, release):
    bad = "#!/bin/sh\nexit 3\n"
    (release.rel / release.name).write_text(bad)
    (release.rel / "SHA256SUMS").write_text(f"{hashlib.sha256(bad.encode()).hexdigest()}  {release.name}\n")
    hostctl.update(svc, "now"); _wait()
    assert release.prog.read_text() == "old program" and "self-check" in hostctl.STATE["error"]


def test_already_newest(svc, release, monkeypatch):
    monkeypatch.setattr(hostctl, "current", lambda: "2099-01-01 (abcdef1)")
    hostctl.update(svc, "now"); _wait()
    assert release.prog.read_text() == "old program" and release.ended == [] and hostctl.STATE["update"] == "idle"


def test_update_when_nobody_plays_waits_for_the_match(svc, release, monkeypatch):
    events._st(svc)["live"][7] = {"phase": "2ND", "home": 1, "away": 2}
    monkeypatch.setattr(hostctl, "IDLE_POLL", 0.01)
    checks = []
    real = hostctl.busy

    def busy(s):
        checks.append(1)
        if len(checks) > 3:
            events._st(svc)["live"][7]["phase"] = "Full time"     # the match ends while it waits
        return real(s)
    monkeypatch.setattr(hostctl, "busy", busy)
    hostctl.update(svc, "idle"); _wait()
    assert len(checks) > 3 and release.ended == ["restart"] and release.prog.read_text() == NEW_PROGRAM
    assert any("waits until nobody is playing" in f["text"] for f in events._st(svc)["feed"])


def test_a_waiting_update_can_be_cancelled(svc, release, monkeypatch):
    events._st(svc)["live"][8] = {"phase": "1ST", "home": 1, "away": 2}
    monkeypatch.setattr(hostctl, "IDLE_POLL", 0.01)
    hostctl.update(svc, "idle")
    for _ in range(500):
        if hostctl.STATE["update"] == "waiting":
            break
        hostctl.time.monotonic()
    hostctl.update(svc, "cancel"); _wait()
    assert release.prog.read_text() == "old program" and release.ended == [] and hostctl.STATE["update"] == "idle"
    assert not os.path.exists(str(release.prog) + ".new")


def test_autostart_writes_the_unit_and_enables_it(svc, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls = []
    enabled = {"on": False}

    def fake(*args, timeout=15):
        calls.append(args)
        if args[0] == "enable": enabled["on"] = True
        if args[0] == "disable": enabled["on"] = False
        if args[0] == "is-enabled": return 0, "enabled" if enabled["on"] else "disabled"
        return 0, ""
    monkeypatch.setattr(hostctl, "_systemctl", fake)
    monkeypatch.setattr(hostctl, "systemd_ok", lambda: True)
    monkeypatch.setattr(hostctl, "program", lambda: "/opt/pes/pes2021-server")
    st, res = hostctl.admin_api(svc, "/admin/api/program/autostart", "POST", b'{"on": true}')
    assert st.startswith("200") and res["autostart"] is True
    assert '"/opt/pes/pes2021-server" host --data' in open(hostctl.unit_path()).read()
    assert ("daemon-reload",) in calls and ("enable", hostctl.UNIT_NAME) in calls
    st, res = hostctl.admin_api(svc, "/admin/api/program/autostart", "POST", b'{"on": false}')
    assert res["autostart"] is False and ("disable", hostctl.UNIT_NAME) in calls


def test_restart_and_stop_end_the_server_loop(svc, monkeypatch):
    loop = asyncio.new_event_loop()
    ev = asyncio.Event()
    hostctl.attach(loop, ev)
    monkeypatch.setattr(hostctl, "program", lambda: "/opt/pes/pes2021-server")
    try:
        assert hostctl.admin_api(svc, "/admin/api/program/restart", "POST", b"{}")[0].startswith("200")
        loop.run_until_complete(asyncio.wait_for(ev.wait(), 2))
        assert hostctl._CTL["after"] == "restart"
        ev.clear()
        assert hostctl.admin_api(svc, "/admin/api/program/stop", "POST", b"{}")[0].startswith("200")
        loop.run_until_complete(asyncio.wait_for(ev.wait(), 2))
        assert hostctl._CTL["after"] == "stop"
    finally:
        hostctl._CTL.update(stop=None, loop=None, after=None)
        loop.close()


def test_clean_env_drops_the_bundles_library_path(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "/x")
    env = hostctl.clean_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib" and "_PYI_ARCHIVE_FILE" not in env and "LD_LIBRARY_PATH_ORIG" not in env


def test_program_routes_through_the_web_handler(svc):
    svc.admin_password = "pw"
    tok = httpgate._sessions(svc).new(True)
    hdr = {"host": "127.0.0.1:8080", "authorization": "Bearer " + tok}
    st, out, _ = asyncio.run(httpgate._admin(svc, "GET", "/admin/api/program", hdr, b"", "127.0.0.1"))
    res = json.loads(out)
    assert st.startswith("200") and {"version", "can_update", "update", "autostart", "log"} <= set(res)


def test_install_sets_up_the_service_menu_entry_and_command(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    prog = home / ".local/share/pes2021-server/pes2021-server"; prog.parent.mkdir(parents=True); prog.write_text("x")
    calls = []
    monkeypatch.setattr(hostctl, "program", lambda: str(prog))
    monkeypatch.setattr(hostctl, "systemd_ok", lambda: True)
    monkeypatch.setattr(hostctl, "_systemctl", lambda *a, timeout=15: (calls.append(a), (0, "inactive" if a[0] == "is-active" else ""))[1])
    up = iter([False, True])                                   # not running yet; answering after the start
    monkeypatch.setattr(hostctl, "_answering", lambda port=8080, timeout=1.5: next(up, True))
    monkeypatch.setattr(hostctl, "_open_page", lambda port=8080: ("http://localhost:8080/admin", True))
    hostctl.cmd_install(SimpleNamespace(data=str(prog.parent), no_autostart=False))
    assert ("enable", hostctl.UNIT_NAME) in calls and ("start", hostctl.UNIT_NAME) in calls
    assert f'ExecStart="{prog}" host' in open(hostctl.unit_path()).read()
    assert 'open' in (home / ".local/share/applications/pes2021-server.desktop").read_text()
    assert os.readlink(home / ".local/bin/pes2021-server") == str(prog)
    assert "running in the background" in capsys.readouterr().out


def test_install_refuses_while_a_server_runs_in_a_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(hostctl, "program", lambda: str(tmp_path / "pes2021-server"))
    monkeypatch.setattr(hostctl, "systemd_ok", lambda: True)
    monkeypatch.setattr(hostctl, "_systemctl", lambda *a, timeout=15: (0, "inactive"))
    monkeypatch.setattr(hostctl, "_answering", lambda port=8080, timeout=1.5: True)
    with pytest.raises(SystemExit) as e:
        hostctl.cmd_install(SimpleNamespace(data=str(tmp_path / "data"), no_autostart=False))
    assert "already running in another window" in str(e.value)
