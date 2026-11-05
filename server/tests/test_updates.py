"""The "newer version is out" check: only server/ changes count."""
from pesserver import updates


def compare(files, status="ahead", ahead=1):
    return {"status": status, "ahead_by": ahead, "files": [{"filename": f} for f in files],
            "commits": [{"sha": "abcdef1234567", "commit": {"committer": {"date": "2026-11-06T10:00:00Z"}}}]}


def test_server_change_is_an_update():
    assert updates.parse_compare(compare(["server/pesserver/gate.py", "docs/index.html"])) == "2026-11-06 (abcdef1)"
    assert updates.parse_compare(compare(["run.sh", "README.md"])) == ""     # launchers are fetched fresh anyway


def test_docs_only_or_same_commit_is_not():
    assert updates.parse_compare(compare(["docs/index.html", "README.md"])) == ""
    assert updates.parse_compare(compare([], status="identical", ahead=0)) == ""
    assert updates.parse_compare(compare(["server/x.py"], status="behind", ahead=0)) == ""


def test_capped_file_list_counts_as_update():
    assert updates.parse_compare(compare(["docs/%d.html" % i for i in range(300)])) != ""


def test_off_without_a_build_commit(monkeypatch):
    import pesserver
    monkeypatch.setattr(pesserver, "COMMIT", "")
    assert updates.start() is None
    monkeypatch.setattr(pesserver, "COMMIT", "abcdef1234567")
    monkeypatch.setenv("UPDATE_CHECK", "0")
    assert updates.start() is None
