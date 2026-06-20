from datetime import datetime, timezone

from babbla.digest.adr import changed_adrs


def _contents(*names):
    return [{"name": n} for n in names]


def _commit(date_iso):
    return [{"commit": {"committer": {"date": date_iso}}}]


def test_since_none_returns_all_sorted():
    def gj(path):
        assert "/contents/docs/adr" in path
        return _contents("0002-b.md", "0001-a.md", "README.md")
    out = changed_adrs("o", "r", "docs/adr", since=None, get_json=gj)
    assert out == ["docs/adr/0001-a.md", "docs/adr/0002-b.md"]   # sorted, README excluded


def test_since_filters_by_latest_commit_date():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md", "0002-b.md")
        if "0001-a.md" in path:
            return _commit("2026-06-01T00:00:00Z")   # before since -> excluded
        if "0002-b.md" in path:
            return _commit("2026-06-18T00:00:00Z")   # at/after since -> kept
        raise AssertionError(path)
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    out = changed_adrs("o", "r", "docs/adr", since=since, get_json=gj)
    assert out == ["docs/adr/0002-b.md"]


def test_commit_exactly_at_since_is_included():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return _commit("2026-06-15T00:00:00Z")
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert changed_adrs("o", "r", "docs/adr", since=since, get_json=gj) == ["docs/adr/0001-a.md"]


def test_excludes_non_adr_files():
    def gj(path):
        return _contents("README.md", "index.md", "0003-c.md", "notes.txt")
    out = changed_adrs("o", "r", "docs/adr", since=None, get_json=gj)
    assert out == ["docs/adr/0003-c.md"]


def test_empty_or_none_contents():
    assert changed_adrs("o", "r", "docs/adr", since=None, get_json=lambda p: None) == []
    assert changed_adrs("o", "r", "docs/adr", since=None, get_json=lambda p: []) == []


def test_file_with_no_commit_is_skipped():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return None   # commit lookup unavailable
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert changed_adrs("o", "r", "docs/adr", since=since, get_json=gj) == []
