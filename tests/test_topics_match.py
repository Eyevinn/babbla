from babbla.config import Topic
from babbla.digest.anchors import Change
from babbla.digest.topics_match import enrich_changes, matches_topic


def _fake(routes):
    """get_json fake: returns the first route whose prefix matches; records calls."""
    calls = []

    def get_json(path):
        calls.append(path)
        for prefix, value in routes.items():
            if path.startswith(prefix):
                return value
        return None

    get_json.calls = calls
    return get_json


def test_enrich_is_noop_without_signals():
    topic = Topic("t", "d")  # no labels/paths
    gj = _fake({})
    changes = [Change("s1", "x", 1)]
    out = enrich_changes("o", "r", changes, topic, get_json=gj)
    assert out is changes          # untouched
    assert gj.calls == []          # never fetched


def test_enrich_populates_labels_and_matches():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/42": {"labels": [{"name": "security"}, {"name": "x"}]}})
    out = enrich_changes("o", "r", [Change("s1", "feat (#42)", 42)], topic, get_json=gj)
    assert out[0].labels == ("security", "x")
    assert matches_topic(out[0], topic) is True


def test_enrich_populates_paths_and_glob_matches():
    topic = Topic("area", "d", paths=("src/babbla/**",))
    gj = _fake({"/repos/o/r/pulls/7/files": [
        {"filename": "src/babbla/digest/runner.py"},
        {"filename": "README.md"},
    ]})
    out = enrich_changes("o", "r", [Change("s1", "feat (#7)", 7)], topic, get_json=gj)
    assert out[0].paths == ("src/babbla/digest/runner.py", "README.md")
    assert matches_topic(out[0], topic) is True


def test_prless_change_is_never_enriched():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/1": {"labels": [{"name": "security"}]}})
    out = enrich_changes("o", "r", [Change("s1", "chore tidy", None)], topic, get_json=gj)
    assert out[0].labels == () and out[0].paths == ()
    assert matches_topic(out[0], topic) is False
    assert gj.calls == []          # no PR -> no fetch


def test_pr_fetch_404_yields_empty_no_raise():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({})                 # everything 404 -> None
    out = enrich_changes("o", "r", [Change("s1", "feat (#9)", 9)], topic, get_json=gj)
    assert out[0].labels == ()
    assert matches_topic(out[0], topic) is False


def test_each_pr_fetched_at_most_once():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/5": {"labels": [{"name": "security"}]}})
    enrich_changes("o", "r",
                   [Change("a", "x (#5)", 5), Change("b", "y (#5)", 5)],
                   topic, get_json=gj)
    assert gj.calls.count("/repos/o/r/pulls/5") == 1


def test_no_match_when_label_absent():
    topic = Topic("sec", "d", labels=("security",))
    c = Change("s", "x", 1, labels=("bug",), paths=())
    assert matches_topic(c, topic) is False
