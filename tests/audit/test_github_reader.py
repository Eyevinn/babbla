import dataclasses

from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts


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
