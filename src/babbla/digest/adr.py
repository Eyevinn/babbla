from __future__ import annotations

import re
from datetime import datetime

from babbla.config import ProjectBinding

_ADR_RE = re.compile(r"^\d{4}-.*\.md$")


def _parse_ts(value: str) -> datetime:
    # GitHub timestamps are ISO 8601 with a trailing Z (UTC).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]:
    """Return docs/adr/NNNN-*.md paths changed since `since` (None → all). Read-only.

    `since` is a timezone-aware datetime or None. For a set `since`, a file is kept
    when its latest commit date is at/after `since` (catches adds and edits)."""
    entries = get_json(f"/repos/{owner}/{repo}/contents/{dir}")
    names = sorted(e["name"] for e in (entries or []) if _ADR_RE.match(e.get("name", "")))
    if since is None:
        return [f"{dir}/{n}" for n in names]
    out: list[str] = []
    for n in names:
        commits = get_json(f"/repos/{owner}/{repo}/commits?path={dir}/{n}&per_page=1")
        if not commits:
            continue
        if _parse_ts(commits[0]["commit"]["committer"]["date"]) >= since:
            out.append(f"{dir}/{n}")
    return out


class AdrRunner:
    """Thin read-only wrapper around AgentRunner that turns one ADR file into a short,
    engaging Slack teaser. Mirrors QuizRunner in shape."""

    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def teaser(self, binding: ProjectBinding, adr_path: str) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        prompt = (
            f"Read the single file at {adr_path} in the repository {slug}. Write one short, "
            f"engaging paragraph for a Slack channel: what the architectural decision was and "
            f"why it mattered. End with a link to the ADR on GitHub "
            f"(https://github.com/{slug}/blob/HEAD/{adr_path}). Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
