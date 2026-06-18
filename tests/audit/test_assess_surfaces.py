from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit import assess
from babbla.audit.assess import OK, THIN, MISSING


def _facts(**over):
    base = dict(
        owner="o", repo="r", visibility="public", default_branch="main",
        has_issues=True, issue_count=5, readme_bytes=1800, has_claude_md=True,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=tuple(CommitMsg(f"descriptive commit message {i}", False) for i in range(20)),
        pr_bodies=tuple(PrBody(120) for _ in range(20)),
        workflow_names=(), has_fastly_toml=False, environments=(), pages_enabled=False,
    )
    base.update(over)
    return RepoFacts(**base)


def _find(facts, name):
    return next(f for f in assess.surface_findings(facts) if f.name == name)


def test_readme_ok_thin_missing():
    assert _find(_facts(readme_bytes=1800), "README").status == OK
    assert _find(_facts(readme_bytes=500), "README").status == THIN     # boundary: 500 is not > 500
    assert _find(_facts(readme_bytes=None), "README").status == MISSING


def test_adr_dir_present_but_empty_is_thin():
    assert _find(_facts(adr_count=10), "docs/adr/").status == OK
    assert _find(_facts(docs_adr_dir_exists=True, adr_count=0), "docs/adr/").status == THIN
    assert _find(_facts(docs_adr_dir_exists=False, adr_count=0), "docs/adr/").status == MISSING


def test_pr_bodies_ratio():
    ok = tuple(PrBody(120) for _ in range(10)) + tuple(PrBody(0) for _ in range(10))   # 10/20 = 50%
    thin = tuple(PrBody(120) for _ in range(3)) + tuple(PrBody(0) for _ in range(17))  # 3/20
    assert _find(_facts(pr_bodies=ok), "PR bodies").status == OK
    assert _find(_facts(pr_bodies=thin), "PR bodies").status == THIN
    assert _find(_facts(pr_bodies=()), "PR bodies").status == MISSING


def test_commit_messages_descriptive_rule():
    junk = tuple(CommitMsg("wip", False) for _ in range(20))
    assert _find(_facts(commits=junk), "commit messages").status == MISSING
    short_with_body = tuple(CommitMsg("fix", True) for _ in range(20))   # body rescues it
    assert _find(_facts(commits=short_with_body), "commit messages").status == OK


def test_pr_body_length_boundary():
    at_80 = tuple(PrBody(80) for _ in range(20))      # none descriptive -> MISSING
    at_81 = tuple(PrBody(81) for _ in range(20))      # all descriptive -> OK
    assert _find(_facts(pr_bodies=at_80), "PR bodies").status == MISSING
    assert _find(_facts(pr_bodies=at_81), "PR bodies").status == OK


def test_thin_surface_carries_recommendation():
    f = _find(_facts(readme_bytes=None), "README")
    assert f.recommendation is not None and "RECOMMENDATIONS.md" in f.recommendation
    assert _find(_facts(readme_bytes=1800), "README").recommendation is None
