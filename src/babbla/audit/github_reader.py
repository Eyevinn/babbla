from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitMsg:
    first_line: str
    has_body: bool


@dataclass(frozen=True)
class PrBody:
    length: int


@dataclass(frozen=True)
class RepoFacts:
    owner: str
    repo: str
    visibility: str
    default_branch: str
    has_issues: bool
    issue_count: int
    readme_bytes: int | None
    has_claude_md: bool
    docs_file_count: int
    docs_adr_dir_exists: bool
    adr_count: int
    commits: tuple[CommitMsg, ...]
    pr_bodies: tuple[PrBody, ...]
    workflow_names: tuple[str, ...]
    has_fastly_toml: bool
    environments: tuple[str, ...]
    pages_enabled: bool


class RepoUnreachable(Exception):
    """Raised when the repository itself cannot be read (404/401/403/network)."""
