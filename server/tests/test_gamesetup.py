"""Installing the mod into PES 2021 copies on the host, from the admin page."""
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


def test_install_and_remove(host):
    tmp, dll = host
    st = {g["id"]: g for g in gamesetup.status()}
    assert set(st) == {"1", "2"} and st["1"]["path"] == str(tmp / "games" / "1")
    assert st["1"]["mod"] == "none" and st["1"]["override"] is False and st["2"]["override"] is None
    st = {g["id"]: g for g in gamesetup.install("1", dll)}
    assert st["1"]["mod"] == "ours" and st["1"]["override"] is True
    reg = (tmp / "prefixes" / "1" / "user.reg").read_text()
    assert '[Software\\\\Wine\\\\AppDefaults\\\\PES2021.exe\\\\DllOverrides]' in reg and '"dinput8"="native,builtin"' in reg
    assert '"atiadlxx"="builtin"' in reg and '"Replacements"="x"' in reg          # other settings untouched
    assert (tmp / "prefixes" / "1" / "user.reg.pes2021-backup").read_text() == USER_REG
    gamesetup.install("1", dll)                                                   # twice: still one line
    assert (tmp / "prefixes" / "1" / "user.reg").read_text().count('"dinput8"=') == 1
    st = {g["id"]: g for g in gamesetup.remove("1")}
    assert st["1"]["mod"] == "none" and st["1"]["override"] is False
    assert not (tmp / "games" / "1" / "dinput8.dll").exists()
    assert "PES2021.exe" not in (tmp / "prefixes" / "1" / "user.reg").read_text()
    st = {g["id"]: g for g in gamesetup.install("2", dll)}                       # no prefix: dll only
    assert st["2"]["mod"] == "ours" and st["2"]["override"] is None


def test_never_overwrites_another_mod(host):
    tmp, dll = host
    (tmp / "games" / "1" / "dinput8.dll").write_bytes(b"MZ some ASI loader")
    assert gamesetup.status()[0]["mod"] == "foreign"
    with pytest.raises(PermissionError):
        gamesetup.install("1", dll)
    gamesetup.remove("1")
    assert (tmp / "games" / "1" / "dinput8.dll").read_bytes() == b"MZ some ASI loader"
    with pytest.raises(LookupError):
        gamesetup.install("9", dll)
