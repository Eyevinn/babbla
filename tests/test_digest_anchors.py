import pytest
from babbla.config import DigestConfig, ProjectBinding
from babbla.digest.anchors import Change, current_head, changes_between, changes_since


def _binding(digest):
    return ProjectBinding("P", "o", "r", "public", "C0XXXXXXXXX", False, digest)


def _fake(routes):
    def get_json(path):
        for prefix, value in routes.items():
            if path.startswith(prefix):
                return value
        return None
    return get_json


def test_branch_head_from_latest_commit():
    b = _binding(DigestConfig("weekly", "UTC", "branch"))
    gj = _fake({"/repos/o/r/commits": [{"sha": "head1", "commit": {"message": "x"}}]})
    assert current_head(b, get_json=gj) == "head1"


def test_branch_head_none_when_empty():
    b = _binding(DigestConfig("weekly", "UTC", "branch"))
    assert current_head(b, get_json=_fake({"/repos/o/r/commits": []})) is None


def test_deploy_head_from_latest_successful_run():
    b = _binding(DigestConfig("weekly", "UTC", "deploy", "cicd_prod.yml"))
    gj = _fake({"/repos/o/r/actions/workflows/cicd_prod.yml/runs": {"workflow_runs": [{"head_sha": "dep1"}]}})
    assert current_head(b, get_json=gj) == "dep1"


def test_deploy_head_none_when_no_runs():
    b = _binding(DigestConfig("weekly", "UTC", "deploy", "cicd_prod.yml"))
    gj = _fake({"/repos/o/r/actions/workflows/cicd_prod.yml/runs": {"workflow_runs": []}})
    assert current_head(b, get_json=gj) is None


def test_changes_between_parses_subject_and_pr():
    gj = _fake({"/repos/o/r/compare/base...head": {"commits": [
        {"sha": "s1", "commit": {"message": "feat: thing (#238)\n\nbody"}},
        {"sha": "s2", "commit": {"message": "chore: tidy"}},
    ]}})
    out = changes_between("o", "r", "base", "head", get_json=gj)
    assert out == [Change("s1", "feat: thing (#238)", 238), Change("s2", "chore: tidy", None)]


def test_changes_since_parses_window():
    gj = _fake({"/repos/o/r/commits": [
        {"sha": "s1", "commit": {"message": "fix: a (#5)"}},
    ]})
    out = changes_since("o", "r", "2026-06-10T00:00:00Z", get_json=gj)
    assert out == [Change("s1", "fix: a (#5)", 5)]


def test_changes_between_empty_when_404():
    gj = _fake({})  # everything 404 -> None
    assert changes_between("o", "r", "base", "head", get_json=gj) == []
