from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from babbla.config import ProjectBinding

_PR_RE = re.compile(r"\(#(\d+)\)\s*$")


@dataclass(frozen=True)
class Change:
    sha: str
    subject: str
    pr_number: int | None


def make_get_json(token: str, api_base: str = "https://api.github.com") -> Callable[[str], object | None]:
    def get_json(path: str) -> object | None:
        req = urllib.request.Request(api_base + path, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "babbla-digest",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    return get_json


def _change(commit: dict) -> Change:
    subject = (commit.get("commit", {}).get("message", "") or "").splitlines()[0] if commit.get("commit") else ""
    m = _PR_RE.search(subject)
    return Change(sha=commit.get("sha", ""), subject=subject, pr_number=int(m.group(1)) if m else None)


def current_head(binding: ProjectBinding, *, get_json) -> str | None:
    o, r = binding.owner, binding.repo
    d = binding.digest
    if d.anchor == "branch":
        commits = get_json(f"/repos/{o}/{r}/commits?per_page=1")
        if commits:
            return commits[0]["sha"]
        return None
    # deploy: latest successful run of the configured workflow
    wf = urllib.parse.quote(d.deploy_workflow, safe="")
    runs = get_json(f"/repos/{o}/{r}/actions/workflows/{wf}/runs?status=success&per_page=1")
    items = (runs or {}).get("workflow_runs", [])
    return items[0]["head_sha"] if items else None


def changes_between(owner: str, repo: str, base_sha: str, head_sha: str, *, get_json) -> list[Change]:
    data = get_json(f"/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}")
    return [_change(c) for c in (data or {}).get("commits", [])]


def changes_since(owner: str, repo: str, since_iso: str, *, get_json) -> list[Change]:
    q = urllib.parse.quote(since_iso, safe="")
    data = get_json(f"/repos/{owner}/{repo}/commits?since={q}&per_page=100")
    return [_change(c) for c in (data or [])]
