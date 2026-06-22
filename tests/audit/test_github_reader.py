import dataclasses
import io
import urllib.error

import pytest

from babbla.audit import github_reader
from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts, RepoUnreachable, make_reader


def test_repofacts_is_constructible_and_frozen():
    facts = RepoFacts(
        owner="Wkkkkk",
        repo="MyTV",
        visibility="public",
        default_branch="main",
        has_issues=True,
        issue_count=28,
        readme_bytes=1800,
        has_claude_md=False,
        docs_file_count=3,
        docs_adr_dir_exists=True,
        adr_count=10,
        commits=(CommitMsg("add deploy workflow", True),),
        pr_bodies=(PrBody(120),),
        workflow_names=("deploy-pages.yml",),
        has_fastly_toml=False,
        environments=(),
        pages_enabled=True,
    )
    assert facts.owner == "Wkkkkk"
    assert facts.commits[0].has_body is True
    try:
        facts.owner = "x"  # frozen -> should raise
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised


def _raise_http(code):
    def _urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, io.BytesIO(b""))
    return _urlopen


def test_get_json_returns_none_on_403(monkeypatch):
    # A fine-grained token granted only Contents/Metadata/PRs/Issues read gets
    # 403 on optional signal probes (/environments, /pages). That must degrade
    # to "absent", like a 404 — not abort the whole audit.
    monkeypatch.setattr(github_reader.urllib.request, "urlopen", _raise_http(403))
    reader = make_reader("tok")
    assert reader._get("/repos/o/r/environments") is None


def test_get_json_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(github_reader.urllib.request, "urlopen", _raise_http(404))
    reader = make_reader("tok")
    assert reader._get("/repos/o/r/pages") is None


def test_get_json_still_raises_on_other_http_errors(monkeypatch):
    # A bad/expired token (401) or a server error is a real failure, not an
    # absent signal — keep aborting so it surfaces.
    monkeypatch.setattr(github_reader.urllib.request, "urlopen", _raise_http(401))
    reader = make_reader("tok")
    with pytest.raises(RepoUnreachable):
        reader._get("/repos/o/r")
