"""The rest of the 2026-11-12 review, host side: an update that doesn't come up is undone, a start that fails on a
taken port isn't retried forever, two update clicks start one update, a failed download leaves no file, the
network is followed after startup, and closing the router's ports can't hold up a stop."""
import json, os, threading, time

import pytest

from pesserver import events, hostctl, netsetup, pescmds
from pesserver.accounts import Accounts
from pesserver.service import Service
from test_hostctl import release, _wait  # noqa: F401  (fixture: a fake release and a fake running program)


@pytest.fixture
def svc(tmp_path):
    s = Service(Accounts(str(tmp_path / "a.db")))
    s.data_dir = str(tmp_path)
    pescmds._db(s)
    return s


# ---- updates -----------------------------------------------------------------------------------------
def test_an_update_keeps_the_old_program_until_the_new_one_is_up(svc, release, monkeypatch):
    prog = hostctl.program()
    monkeypatch.setattr(hostctl, "current", lambda: "2026-11-01 (1111111)")
    hostctl.update(svc, "now"); _wait()
    assert open(prog + ".old").read() == "old program" and "selfcheck" in open(prog).read()
    assert json.load(open(prog + ".trying")) == {"version": "2099-01-01 (abcdef1)", "from": "2026-11-01 (1111111)", "starts": 0}
    hostctl.trial_begin()                                # the new version starts...
    assert json.load(open(prog + ".trying"))["starts"] == 1
    hostctl.trial_passed(svc)                            # ...and is up
    assert not os.path.exists(prog + ".old") and not os.path.exists(prog + ".trying")
    assert not any("went back" in f["text"] for f in events.snapshot(svc)["feed"])


def test_a_new_version_that_does_not_come_up_is_undone(svc, release, monkeypatch, capsys):
    prog = hostctl.program()
    monkeypatch.setattr(hostctl, "current", lambda: "2026-11-01 (1111111)")
    hostctl.update(svc, "now"); _wait()
    ran = []
    monkeypatch.setattr(hostctl.os, "execve", lambda p, argv, env: ran.append(p))
    hostctl.trial_begin(); hostctl.trial_begin()         # two starts that never got as far as "up"
    assert not ran and "selfcheck" in open(prog).read()
    hostctl.trial_uncount(); hostctl.trial_begin()       # (a start that failed on a taken port doesn't count)
    assert not ran
    hostctl.trial_begin()                                # the third real one: back to the old program
    assert ran == [prog] and open(prog).read() == "old program"
    assert not os.path.exists(prog + ".old") and not os.path.exists(prog + ".trying")
    assert "didn't start on this computer" in capsys.readouterr().out
    assert open(os.path.join(os.path.dirname(prog), "program-version.txt")).read().strip() == "2026-11-01 (1111111)"
    hostctl.trial_passed(svc)                            # the old program is up again: the admin is told why
    assert any("update to 2099-01-01 (abcdef1) didn't start" in f["text"] and "2026-11-01" in f["text"]
               for f in events.snapshot(svc)["feed"])
    assert not os.path.exists(prog + ".undone")
    hostctl.trial_begin()                                # no update pending: nothing to do
    assert ran == [prog]


def test_in_the_service_an_undone_update_asks_for_a_restart(svc, release, monkeypatch):
    monkeypatch.setenv("PES_SERVICE", "1")
    hostctl.update(svc, "now"); _wait()
    hostctl.trial_begin(); hostctl.trial_begin()
    with pytest.raises(SystemExit) as e:
        hostctl.trial_begin()
    assert e.value.code == hostctl.RESTART_EXIT and open(hostctl.program()).read() == "old program"


def test_a_failed_download_leaves_no_file_behind(svc, release, tmp_path):
    (tmp_path / "rel" / "SHA256SUMS").write_text("0" * 64 + f"  {hostctl.asset()}\n")
    hostctl.update(svc, "now"); _wait()
    assert hostctl.STATE["update"] == "error" and not os.path.exists(hostctl.program() + ".new")
    assert not os.path.exists(hostctl.program() + ".old") and open(hostctl.program()).read() == "old program"


def test_two_update_clicks_at_once_start_one_update(svc, release, monkeypatch):
    started, gate = [], threading.Event()
    real = hostctl.download

    def slow(cancel=None, want=None):
        started.append(1); gate.wait(5)
        return real(cancel, want)
    monkeypatch.setattr(hostctl, "download", slow)
    clicks = [threading.Thread(target=hostctl.update, args=(svc, "now")) for _ in range(4)]
    for t in clicks:
        t.start()
    for t in clicks:
        t.join()
    time.sleep(0.2)
    gate.set(); _wait()
    assert started == [1] and hostctl.STATE["update"] == "restarting"


def test_the_unit_stops_retrying_when_a_port_is_taken(tmp_path, monkeypatch):
    unit = hostctl.unit_text("/opt/pes/pes2021-server", str(tmp_path))
    assert f"RestartPreventExitStatus={hostctl.CANT_START_EXIT}" in unit and "Restart=on-failure" in unit
    # a server updated from the admin page gets the new unit too
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PUBLIC_IP", raising=False)
    monkeypatch.setattr(hostctl, "program", lambda: "/opt/pes/pes2021-server")
    calls = []
    monkeypatch.setattr(hostctl, "_systemctl", lambda *a, timeout=15: (calls.append(a), (0, ""))[1])
    os.makedirs(os.path.dirname(hostctl.unit_path()))
    open(hostctl.unit_path(), "w").write(unit.replace("RestartPreventExitStatus=78\n", ""))
    assert hostctl.refresh_unit(str(tmp_path)) is False          # not the service: hands off
    monkeypatch.setenv("PES_SERVICE", "1")
    assert hostctl.refresh_unit(str(tmp_path)) is True and open(hostctl.unit_path()).read() == unit
    assert calls == [("daemon-reload",)]
    assert hostctl.refresh_unit(str(tmp_path)) is False and calls == [("daemon-reload",)]


def test_status_asks_systemd_once_not_on_every_poll(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(hostctl, "_SEEN", {"systemd": None, "autostart": (0.0, None)})
    monkeypatch.setattr(hostctl.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(hostctl, "_systemctl", lambda *a, timeout=15: (calls.append(a[0]), (0, "enabled"))[1])
    monkeypatch.setattr(hostctl, "unit_path", lambda: __file__)
    for _ in range(5):
        st = hostctl.status(svc)
    assert st["systemd"] is True and st["autostart"] is True
    assert calls == ["show-environment", "is-enabled"]


# ---- the network -------------------------------------------------------------------------------------
class Router:
    def __init__(self, ext="81.2.69.160", dead=False):
        self.ext, self.dead, self.open, self.deleted, self.control_url, self.local_ip = ext, dead, [], [], "http://192.168.1.1/ctl", "192.168.1.100"

    def external_ip(self):
        return self.ext

    def add(self, port, proto):
        if self.dead:
            raise OSError("no route to host")
        if port == 443:
            raise netsetup.UPnPError("AddPortMapping: 718 ConflictInMappingEntry")
        self.open.append((port, proto))

    def delete(self, port, proto):
        if self.dead:
            time.sleep(0.05)
            raise OSError("no route to host")
        self.deleted.append((port, proto))


@pytest.fixture
def net(monkeypatch):
    monkeypatch.setattr(netsetup, "STATE", {"ready": False, "lan": None, "wan": None, "upnp": None, "upnp_error": None,
                                            "cgnat": False, "mapped": [], "_gw": None})
    monkeypatch.setattr(netsetup, "ON_CHANGE", [])
    monkeypatch.setattr(netsetup, "public_ip_from_web", lambda: None)
    monkeypatch.setattr(netsetup, "_ports", lambda: [(443, "TCP"), (8080, "TCP"), (3478, "UDP")])
    monkeypatch.delenv("PESSERVER_NO_IPCHECK", raising=False)
    return netsetup.STATE


ALL = {"renew": True, "wan": True, "find": True}
NONE = {"renew": False, "wan": False, "find": False}


def test_a_network_that_comes_up_after_the_server_is_picked_up(net, monkeypatch):
    lan, routers, changes = ["127.0.0.1"], [None], []
    monkeypatch.setattr(netsetup, "lan_ip", lambda: lan[0])
    monkeypatch.setattr(netsetup, "discover_gateway", lambda: routers[0])
    netsetup.ON_CHANGE.append(lambda: changes.append((net["lan"], net["wan"])))
    netsetup._look(True)                                 # started at login, before the Wi-Fi
    assert net["lan"] == "127.0.0.1" and net["_gw"] is None and net["mapped"] == [] and "no UPnP router" in net["upnp_error"]
    netsetup._tick(True, ALL)
    assert not changes                                   # still nothing
    lan[0], routers[0] = "192.168.1.100", Router()
    netsetup._tick(True, NONE)                           # the address appeared: everything is looked up again
    assert net["lan"] == "192.168.1.100" and net["wan"] == "81.2.69.160" and net["upnp_error"] is None
    assert net["mapped"] == ["8080/tcp", "3478/udp"] and changes == [("192.168.1.100", "81.2.69.160")]
    netsetup._tick(True, NONE)
    assert len(changes) == 1                             # nothing new: nobody is told again


def test_a_router_that_answers_later_gets_its_ports_opened(net, monkeypatch):
    routers = [None]
    monkeypatch.setattr(netsetup, "lan_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(netsetup, "discover_gateway", lambda: routers[0])
    netsetup._look(True)
    assert net["mapped"] == []
    routers[0] = Router()
    netsetup._tick(True, NONE)
    assert net["mapped"] == []                           # not every minute forever: when a look is due
    netsetup._tick(True, {**NONE, "find": True})
    assert net["mapped"] == ["8080/tcp", "3478/udp"] and net["wan"] == "81.2.69.160"
    # the router is restarted or replaced: the renewal fails, and the next look finds the new one
    routers[0].dead = True
    netsetup._tick(True, {**NONE, "renew": True})
    assert net["_gw"] is None
    routers[0] = Router()
    netsetup._tick(True, {**NONE, "find": True})
    assert net["_gw"] is routers[0] and routers[0].open == [(8080, "TCP"), (3478, "UDP")]


def test_the_public_address_is_followed(net, monkeypatch):
    monkeypatch.setattr(netsetup, "lan_ip", lambda: "192.168.1.100")
    monkeypatch.setattr(netsetup, "discover_gateway", lambda: None)
    web, changes = ["81.2.69.160"], []
    monkeypatch.setattr(netsetup, "public_ip_from_web", lambda: web[0])
    netsetup.ON_CHANGE.append(lambda: changes.append(net["wan"]))
    netsetup._look(False)
    web[0] = "81.2.69.200"
    netsetup._tick(False, {**NONE, "wan": True})
    assert net["wan"] == "81.2.69.200" and changes == ["81.2.69.200"]


def test_closing_the_routers_ports_cannot_hold_up_a_stop(net, monkeypatch):
    monkeypatch.setattr(netsetup, "_stop", threading.Event())
    gw = net["_gw"] = Router()
    net["mapped"] = ["8080/tcp"]                         # (443 failed at first; it may have opened at a renewal)
    netsetup.teardown()
    assert gw.deleted == [(443, "TCP"), (8080, "TCP"), (3478, "UDP")]
    dead = net["_gw"] = Router(dead=True)
    t = time.monotonic()
    netsetup.teardown()
    assert time.monotonic() - t < 0.5 and dead.deleted == []     # one try, not one per port
    net["_gw"] = None
    netsetup.teardown()                                  # no router: nothing to do


def test_only_a_web_address_is_read_from_a_device_that_answers_the_search(monkeypatch):
    class Sock:
        def __init__(self, *a):
            self.answers = [b"HTTP/1.1 200 OK\r\nLOCATION: file:///etc/passwd\r\n\r\n",
                            b"HTTP/1.1 200 OK\r\nLOCATION: https://evil.example/x\r\n\r\n"]

        def settimeout(self, t): pass
        def setsockopt(self, *a): pass
        def sendto(self, *a): pass
        def close(self): pass

        def recvfrom(self, n):
            if not self.answers:
                raise netsetup.socket.timeout
            return self.answers.pop(0), ("192.168.1.50", 1900)
    opened = []
    monkeypatch.setattr(netsetup.socket, "socket", Sock)
    monkeypatch.setattr(netsetup.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    assert netsetup.discover_gateway() is None and opened == []
