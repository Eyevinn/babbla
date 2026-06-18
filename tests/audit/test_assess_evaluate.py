from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit.assess import evaluate


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


def test_good_verdict_exit_zero():
    r = evaluate(_facts())
    assert r.verdict == "GOOD"
    assert r.exit_code == 0
    assert r.deploy_style == "Pages"
    assert r.visibility == "public"


def test_thin_when_readme_missing():
    r = evaluate(_facts(readme_bytes=None))
    assert r.verdict == "THIN"
    assert r.exit_code == 1


def test_partial_when_readme_present_but_why_thin():
    # README ok, but ADRs absent, PR bodies & commits all thin/missing -> not GOOD, not THIN
    r = evaluate(_facts(
        docs_adr_dir_exists=False, adr_count=0,
        pr_bodies=tuple(PrBody(0) for _ in range(20)),
        commits=tuple(CommitMsg("wip", False) for _ in range(20)),
    ))
    assert r.verdict == "PARTIAL"
    assert r.exit_code == 1


def test_report_carries_all_findings():
    r = evaluate(_facts())
    names = {f.name for f in r.findings}
    assert {"README", "CLAUDE.md", "docs/", "docs/adr/", "PR bodies", "commit messages", "issues"} <= names
