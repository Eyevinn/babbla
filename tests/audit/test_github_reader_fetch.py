import pytest

from babbla.audit.github_reader import GithubReader, RepoUnreachable


def _canned(over=None):
    data = {
        "/repos/o/r": {"visibility": "public", "default_branch": "main", "has_issues": True},
        "/repos/o/r/readme": {"size": 1800},
        "/repos/o/r/contents/CLAUDE.md": {"name": "CLAUDE.md", "type": "file"},
        "/repos/o/r/contents/docs": [
            {"name": "PROPOSAL.md", "type": "file"},
            {"name": "adr", "type": "dir"},
        ],
        "/repos/o/r/contents/docs/adr": [
            {"name": "0001-x.md", "type": "file"},
            {"name": "README.md", "type": "file"},
        ],
        "/repos/o/r/commits?per_page=20": [
            {"commit": {"message": "a descriptive subject line here\n\nwith body"}},
            {"commit": {"message": "wip"}},
        ],
        "/repos/o/r/pulls?state=closed&per_page=20": [
            {"merged_at": "2026-01-01T00:00:00Z", "body": "x" * 120},
            {"merged_at": None, "body": "ignored, not merged"},
        ],
        "/search/issues?q=repo:o/r+type:issue": {"total_count": 28},
        "/repos/o/r/contents/.github/workflows": [{"name": "deploy-pages.yml", "type": "file"}],
        "/repos/o/r/contents/fastly.toml": None,
        "/repos/o/r/environments": {"environments": []},
        "/repos/o/r/pages": {"status": "built"},
    }
    if over:
        data.update(over)
    return data


def _reader(over=None):
    # Override values may be None to simulate a 404 for that path; data.get
    # returns None for both "overridden to None" and "absent", which is exactly
    # what the real get_json does on a 404.
    data = _canned(over)
    return GithubReader(lambda path: data.get(path))


def test_fetch_maps_all_facts():
    facts = _reader().fetch("o", "r")
    assert facts.visibility == "public"
    assert facts.readme_bytes == 1800
    assert facts.has_claude_md is True
    assert facts.docs_file_count == 1          # PROPOSAL.md; 'adr' dir excluded
    assert facts.docs_adr_dir_exists is True
    assert facts.adr_count == 1                 # 0001-x.md; README.md excluded
    assert facts.commits[0].has_body is True
    assert facts.commits[1].has_body is False
    assert len(facts.pr_bodies) == 1           # only the merged PR
    assert facts.pr_bodies[0].length == 120
    assert facts.issue_count == 28
    assert facts.workflow_names == ("deploy-pages.yml",)
    assert facts.has_fastly_toml is False
    assert facts.environments == ()
    assert facts.pages_enabled is True


def test_fetch_absent_surfaces_are_facts_not_errors():
    facts = _reader({
        "/repos/o/r/readme": None,
        "/repos/o/r/contents/CLAUDE.md": None,
        "/repos/o/r/pages": None,
    }).fetch("o", "r")
    assert facts.readme_bytes is None
    assert facts.has_claude_md is False
    assert facts.pages_enabled is False


def test_unreachable_repo_raises():
    reader = GithubReader(lambda path: None)   # repo metadata 404
    with pytest.raises(RepoUnreachable):
        reader.fetch("o", "r")
