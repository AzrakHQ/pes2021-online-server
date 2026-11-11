"""The "newer version is out" check reads the published release, the same one "Update now" installs."""
import types

from pesserver import hostctl, updates

MINE = "2026-11-11 (8839e88)"


def test_newer_release_counts():
    assert updates.newer("2026-11-12 (a95abd0)", MINE) == "2026-11-12 (a95abd0)"
    assert updates.newer("2026-11-11 (a95abd0)", MINE) == "2026-11-11 (a95abd0)"     # a later build the same day


def test_same_older_or_missing_release_does_not():
    assert updates.newer(MINE, MINE) == ""
    assert updates.newer(MINE + "\n", MINE) == ""
    assert updates.newer("2026-11-08 (f9a5d4d)", MINE) == ""     # a hand-installed newer build stays put
    assert updates.newer("", MINE) == ""


def test_main_ahead_of_the_release_is_not_an_update(monkeypatch):
    """The bug: main had new commits but no release was built for them yet; the notice promised an update
    that Update now couldn't find."""
    monkeypatch.setattr(hostctl, "latest_version", lambda: MINE)
    assert updates.check_once(MINE) == ""


def test_update_now_with_nothing_newer_says_so_and_drops_the_notice(monkeypatch):
    feed = []
    monkeypatch.setattr(hostctl, "manageable", lambda: (True, ""))
    monkeypatch.setattr(hostctl, "current", lambda: MINE)
    monkeypatch.setattr(hostctl, "latest_version", lambda: MINE)
    monkeypatch.setattr(hostctl, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing to download")))
    import pesserver.events as events
    monkeypatch.setattr(events, "feed", lambda svc, text, kind="info": feed.append(text))
    monkeypatch.setitem(hostctl.STATE, "update", "idle")
    monkeypatch.setattr(updates, "STATE", {"available": True, "latest": "2026-11-11 (13a1cef)", "checked": 1.0})
    hostctl.update(types.SimpleNamespace(), "now")
    hostctl._CTL["thread"].join(5)
    assert hostctl.STATE["update"] == "idle" and hostctl.STATE["target"] == MINE
    assert updates.STATE == {"available": False, "latest": "", "checked": 1.0}
    assert feed == ["The server is already the newest version"]


def test_off_without_a_build_commit(monkeypatch):
    import pesserver
    monkeypatch.setattr(pesserver, "COMMIT", "")
    assert updates.start() is None
    monkeypatch.setattr(pesserver, "COMMIT", "abcdef1234567")
    monkeypatch.setenv("UPDATE_CHECK", "0")
    assert updates.start() is None


def test_a_source_build_without_a_date_still_sees_releases():
    assert updates.newer("2099-01-01 (abcdef1)", "dev") == "2099-01-01 (abcdef1)"
