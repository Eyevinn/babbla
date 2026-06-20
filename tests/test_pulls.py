from datetime import datetime, timezone

from babbla.digest.pulls import StalePR, stale_prs

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _pr(number, days_ago, *, draft=False, title=None, login="alice"):
    updated = (NOW - __import__("datetime").timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "number": number,
        "title": title or f"PR {number}",
        "html_url": f"https://github.com/o/r/pull/{number}",
        "draft": draft,
        "updated_at": updated,
        "user": {"login": login},
    }


def _reader(prs):
    def get_json(path):
        assert "state=open" in path and "sort=updated" in path and "direction=asc" in path
        return prs
    return get_json


def test_filters_by_threshold():
    prs = [_pr(1, days_ago=20), _pr(2, days_ago=3)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [1]
    assert out[0] == StalePR(number=1, title="PR 1", author="alice",
                             url="https://github.com/o/r/pull/1", idle_days=20)


def test_excludes_drafts_unless_included():
    prs = [_pr(1, days_ago=30, draft=True), _pr(2, days_ago=30)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [2]
    out2 = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=True, get_json=_reader(prs))
    assert sorted(p.number for p in out2) == [1, 2]


def test_sorts_oldest_first():
    prs = [_pr(1, days_ago=15), _pr(2, days_ago=40), _pr(3, days_ago=20)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [2, 3, 1]   # most idle first


def test_empty_and_none_input():
    assert stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=lambda p: None) == []
    assert stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=lambda p: []) == []
