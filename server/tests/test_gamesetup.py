"""Installing the mod into PES 2021 copies on the host, from the admin page."""
import os
import pytest
from pesserver import gamesetup, identity

USER_REG = """WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000

#arch=win64

[Software\\\\Wine\\\\AppDefaults\\\\DIRT5.exe\\\\DllOverrides] 1785139151
#time=1dd1d9dcf218d10
"atiadlxx"="builtin"

[Software\\\\Wine\\\\Fonts] 1785139151
"Replacements"="x"
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    identity.init(str(tmp_path))
    games, prefixes = tmp_path / "games", tmp_path / "prefixes"
    (games / "1").mkdir(parents=True); (games / "1" / "PES2021.exe").write_bytes(b"MZ")
    (games / "2").mkdir(); (games / "2" / "PES2021.exe").write_bytes(b"MZ")       # no prefix found
    (prefixes / "1").mkdir(parents=True); (prefixes / "1" / "user.reg").write_text(USER_REG)
    monkeypatch.setattr(gamesetup, "LOCAL", [(str(games / "1"), str(prefixes / "1")), (str(games / "2"), None)])
    marker = identity.EMBED_MARKER
    dll = b"MZ" + marker + b"\0" * (identity.EMBED_SIZE - len(marker)) + b"end"
    return tmp_path, identity.pack_mod(dll, identity.mod_settings("srv", "", ["192.168.1.5"]))


def _by_folder(rows):
    """{"1": row, "2": row}: the copies by their folder's name (the ids are made from the whole path)."""
    return {os.path.basename(g["path"]): g for g in rows}


def _id(tmp, n):
    return gamesetup.gid(str(tmp / "games" / str(n)))


def test_install_and_remove(host):
    tmp, dll = host
    st = _by_folder(gamesetup.status())
    assert set(st) == {"1", "2"} and st["1"]["path"] == str(tmp / "games" / "1")
    assert st["1"]["id"] == _id(tmp, 1) != st["2"]["id"]
    assert st["1"]["mod"] == "none" and st["1"]["override"] is False and st["2"]["override"] is None
    st = _by_folder(gamesetup.install(_id(tmp, 1), dll))
    assert st["1"]["mod"] == "ours" and st["1"]["override"] is True
    reg = (tmp / "prefixes" / "1" / "user.reg").read_text()
    assert '[Software\\\\Wine\\\\AppDefaults\\\\PES2021.exe\\\\DllOverrides]' in reg and '"dinput8"="native,builtin"' in reg
    assert '"atiadlxx"="builtin"' in reg and '"Replacements"="x"' in reg          # other settings untouched
    assert (tmp / "prefixes" / "1" / "user.reg.pes2021-backup").read_text() == USER_REG
    gamesetup.install(_id(tmp, 1), dll)                                                   # twice: still one line
    assert (tmp / "prefixes" / "1" / "user.reg").read_text().count('"dinput8"=') == 1
    st = _by_folder(gamesetup.remove(_id(tmp, 1)))
    assert st["1"]["mod"] == "none" and st["1"]["override"] is False
    assert not (tmp / "games" / "1" / "dinput8.dll").exists()
    assert "PES2021.exe" not in (tmp / "prefixes" / "1" / "user.reg").read_text()
    st = _by_folder(gamesetup.install(_id(tmp, 2), dll))                       # no prefix: dll only
    assert st["2"]["mod"] == "ours" and st["2"]["override"] is None


def test_never_overwrites_another_mod(host):
    tmp, dll = host
    (tmp / "games" / "1" / "dinput8.dll").write_bytes(b"MZ some ASI loader")
    assert gamesetup.status()[0]["mod"] == "foreign"
    with pytest.raises(PermissionError):
        gamesetup.install(_id(tmp, 1), dll)
    gamesetup.remove(_id(tmp, 1))
    assert (tmp / "games" / "1" / "dinput8.dll").read_bytes() == b"MZ some ASI loader"
    with pytest.raises(LookupError):
        gamesetup.install(_id(tmp, 9), dll)


@pytest.fixture
def auto(host, monkeypatch):
    tmp, dll = host
    monkeypatch.setattr(gamesetup, "game_running", lambda: False)
    monkeypatch.setattr(gamesetup, "use_local_games", lambda: None)      # keep the test's two copies
    monkeypatch.setitem(gamesetup.AUTO, "found_at", 1e18)
    monkeypatch.setitem(gamesetup.AUTO, "on", True)
    monkeypatch.setitem(gamesetup.AUTO, "skip", [])
    gamesetup._auto_load(str(tmp))
    return tmp, dll


def test_auto_puts_the_mod_in_and_keeps_it_current(auto):
    tmp, dll = auto
    seen = []
    done = gamesetup.auto_pass(dll, lambda d, w: seen.append((d, w)))
    assert sorted(w for _, w in done) == ["installed", "installed"] and len(seen) == 2
    assert all(g["mod"] == "ours" for g in gamesetup.status())
    assert gamesetup.auto_pass(dll) == []                                 # nothing changed: nothing to do
    newer = identity.pack_mod(dll, identity.mod_settings("srv", "", ["192.168.1.6"]))
    assert sorted(w for _, w in gamesetup.auto_pass(newer)) == ["updated", "updated"]
    assert (tmp / "games" / "1" / "dinput8.dll").read_bytes() == newer


def test_auto_leaves_other_mods_and_removed_copies_alone(auto):
    tmp, dll = auto
    (tmp / "games" / "1" / "dinput8.dll").write_bytes(b"MZ someone else's mod")
    gamesetup.install(_id(tmp, 2), dll)
    gamesetup.remove(_id(tmp, 2))                                                  # the host took it out
    assert gamesetup.auto_pass(dll) == []
    assert (tmp / "games" / "1" / "dinput8.dll").read_bytes() == b"MZ someone else's mod"
    assert not (tmp / "games" / "2" / "dinput8.dll").exists()
    gamesetup.install(_id(tmp, 2), dll)                                            # put back by hand: automatic again
    newer = identity.pack_mod(dll, identity.mod_settings("srv", "", ["10.0.0.9"]))
    assert [w for _, w in gamesetup.auto_pass(newer)] == ["updated"]


def test_auto_waits_while_the_game_runs_and_can_be_off(auto, monkeypatch):
    tmp, dll = auto
    monkeypatch.setattr(gamesetup, "game_running", lambda: True)
    assert gamesetup.auto_pass(dll) == []
    monkeypatch.setattr(gamesetup, "game_running", lambda: False)
    gamesetup.set_auto(False)
    assert gamesetup.auto_pass(dll) == [] and not (tmp / "games" / "1" / "dinput8.dll").exists()
    gamesetup.AUTO["on"] = True
    gamesetup._auto_load(str(tmp))
    assert gamesetup.AUTO["on"] is False                                   # the choice is saved


def test_only_the_game_itself_counts_as_running():
    assert gamesetup._is_game(b"Z:\\home\\me\\game\\files\\PES2021.exe\0")
    assert gamesetup._is_game(b"/home/me/game/files/PES2021.exe\0-arg\0")
    assert not gamesetup._is_game(b"/bin/bash\0-c\0grep PES2021.exe log\0")
    assert gamesetup._is_game(b"python3\0/usr/proton\0waitforexitandrun\0/g/PES2021.exe\0")      # starting up
    assert gamesetup._is_game(b"c:\\windows\\system32\\steam.exe\0/g/PES2021.exe\0")
    assert not gamesetup._is_game(b"tail\0-f\0/g/PES2021.exe.log\0")


def test_a_copy_keeps_its_id_when_another_one_goes_away(host, monkeypatch):
    tmp, dll = host
    second = _id(tmp, 2)
    monkeypatch.setattr(gamesetup, "LOCAL", gamesetup.LOCAL[1:])          # the first copy's drive was unplugged
    assert [g["id"] for g in gamesetup.status()] == [second]
    with pytest.raises(LookupError):
        gamesetup.install(_id(tmp, 1), dll)                                 # a click on the old list: no wrong copy
    assert not (tmp / "games" / "2" / "dinput8.dll").exists()


def test_a_mod_file_that_cannot_be_read_stops_nothing(auto):
    tmp, dll = auto
    (tmp / "games" / "1" / "dinput8.dll").mkdir()                          # not a file at all
    assert _by_folder(gamesetup.status())["1"]["mod"] == "foreign"
    assert [os.path.basename(d) for d, _ in gamesetup.auto_pass(dll)] == ["2"]     # the other copy still gets it
