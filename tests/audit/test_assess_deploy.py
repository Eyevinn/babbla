from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit.assess import detect_deploy


def _facts(**over):
    base = dict(
        owner="o", repo="r", visibility="public", default_branch="main",
        has_issues=True, issue_count=5, readme_bytes=1800, has_claude_md=True,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=(CommitMsg("a descriptive message here", False),),
        pr_bodies=(PrBody(120),),
        workflow_names=(), has_fastly_toml=False, environments=(), pages_enabled=False,
    )
    base.update(over)
    return RepoFacts(**base)


def test_environments_wins():
    style, detail = detect_deploy(_facts(environments=("stage", "prod"), pages_enabled=True))
    assert style == "Environments"
    assert "stage" in detail and "prod" in detail


def test_fastly_by_toml_or_workflow():
    assert detect_deploy(_facts(has_fastly_toml=True))[0] == "Fastly"
    assert detect_deploy(_facts(workflow_names=("fastly-deploy.yml",)))[0] == "Fastly"


def test_pages_by_flag_or_workflow():
    assert detect_deploy(_facts(pages_enabled=True))[0] == "Pages"
    assert detect_deploy(_facts(workflow_names=("deploy-pages.yml",)))[0] == "Pages"


def test_head_sha_fallback_for_generic_deploy_workflow():
    assert detect_deploy(_facts(workflow_names=("release.yml",)))[0] == "head_sha-fallback"


def test_none_when_no_cd():
    assert detect_deploy(_facts(workflow_names=("test.yml", "lint.yml")))[0] == "none"
