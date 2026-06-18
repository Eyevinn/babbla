import pytest

from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts, RepoUnreachable
from babbla.audit.__main__ import main


def _facts(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        has_issues=True, issue_count=28, readme_bytes=1800, has_claude_md=False,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=tuple(CommitMsg(f"descriptive commit message {i}", False) for i in range(20)),
        pr_bodies=tuple(PrBody(120) for _ in range(20)),
        workflow_names=("deploy-pages.yml",), has_fastly_toml=False, environments=(), pages_enabled=True,
    )
    base.update(over)
    return RepoFacts(**base)


class _FakeReader:
    def __init__(self, facts=None, exc=None):
        self._facts = facts
        self._exc = exc

    def fetch(self, owner, repo):
        if self._exc:
            raise self._exc
        return self._facts


def test_good_repo_exits_zero_and_prints_report(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(_facts()))
    out = capsys.readouterr().out
    assert code == 0
    assert "Verdict: GOOD" in out
    assert "  - name: MyTV" in out


def test_thin_repo_exits_one(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(_facts(readme_bytes=None)))
    assert code == 1


def test_emit_binding_prints_only_yaml(capsys):
    code = main(["Wkkkkk/MyTV", "--emit-binding"], reader=_FakeReader(_facts()))
    out = capsys.readouterr().out
    assert out.lstrip().startswith("- name: MyTV")
    assert "Verdict:" not in out
    assert code == 0


def test_bad_args_exit_two(capsys):
    code = main(["not-a-slug"], reader=_FakeReader(_facts()))
    err = capsys.readouterr().err
    assert code == 2
    assert "owner/repo" in err


def test_unreachable_repo_exit_two(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(exc=RepoUnreachable("404 Not Found")))
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot read" in err.lower()


def test_missing_token_without_reader_exit_two(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main(["Wkkkkk/MyTV"])   # no injected reader -> needs token
    err = capsys.readouterr().err
    assert code == 2
    assert "GITHUB_TOKEN" in err
