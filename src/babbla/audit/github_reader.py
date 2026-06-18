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


import json
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"


def _first_line_and_body(message: str) -> tuple[str, bool]:
    parts = message.split("\n", 1)
    first = parts[0]
    body = parts[1].strip() if len(parts) > 1 else ""
    return first, bool(body)


class GithubReader:
    def __init__(self, get_json):
        # get_json: Callable[[str], dict | list | None]; None means HTTP 404.
        self._get = get_json

    def fetch(self, owner: str, repo: str) -> RepoFacts:
        base = f"/repos/{owner}/{repo}"
        meta = self._get(base)
        if meta is None:
            raise RepoUnreachable(f"{owner}/{repo}: not found or no read access")

        readme = self._get(f"{base}/readme")
        claude = self._get(f"{base}/contents/CLAUDE.md")

        docs = self._get(f"{base}/contents/docs") or []
        docs_files = [e for e in docs if e.get("type") == "file"]
        docs_adr_dir = any(e.get("type") == "dir" and e.get("name") == "adr" for e in docs)

        adr = self._get(f"{base}/contents/docs/adr") or []
        adr_count = sum(
            1 for e in adr
            if e.get("type") == "file"
            and e.get("name", "").lower().endswith(".md")
            and e.get("name", "").lower() != "readme.md"
        )

        commits_raw = self._get(f"{base}/commits?per_page=20") or []
        commits = []
        for c in commits_raw:
            first, has_body = _first_line_and_body(c["commit"]["message"])
            commits.append(CommitMsg(first_line=first, has_body=has_body))

        pulls_raw = self._get(f"{base}/pulls?state=closed&per_page=20") or []
        pr_bodies = [PrBody(length=len(p.get("body") or "")) for p in pulls_raw if p.get("merged_at")]

        issues = self._get(f"/search/issues?q=repo:{owner}/{repo}+type:issue") or {}
        issue_count = int(issues.get("total_count", 0))

        workflows = self._get(f"{base}/contents/.github/workflows") or []
        workflow_names = tuple(e["name"] for e in workflows if e.get("type") == "file")

        fastly = self._get(f"{base}/contents/fastly.toml")
        env_resp = self._get(f"{base}/environments") or {}
        environments = tuple(e["name"] for e in env_resp.get("environments", []))
        pages = self._get(f"{base}/pages")

        return RepoFacts(
            owner=owner,
            repo=repo,
            visibility=meta.get("visibility", "public"),
            default_branch=meta.get("default_branch", "main"),
            has_issues=bool(meta.get("has_issues", False)),
            issue_count=issue_count,
            readme_bytes=(readme.get("size") if readme else None),
            has_claude_md=claude is not None,
            docs_file_count=len(docs_files),
            docs_adr_dir_exists=docs_adr_dir,
            adr_count=adr_count,
            commits=tuple(commits),
            pr_bodies=tuple(pr_bodies),
            workflow_names=workflow_names,
            has_fastly_toml=fastly is not None,
            environments=environments,
            pages_enabled=pages is not None,
        )


def make_reader(token: str, *, api_base: str = GITHUB_API) -> GithubReader:
    def get_json(path: str):
        req = urllib.request.Request(
            f"{api_base}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "babbla-audit",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RepoUnreachable(f"HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise RepoUnreachable(f"network error for {path}: {exc.reason}") from exc

    return GithubReader(get_json)
