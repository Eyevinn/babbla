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
    """Thin read-only wrapper around AgentRunner that turns a set of changed ADRs into a
    Slack digest: an opening summary paragraph plus a per-ADR list. Mirrors QuizRunner in shape."""

    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def digest(self, binding: ProjectBinding, adr_paths: list[str]) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        listing = "\n".join(
            f"- {p}  (link: https://github.com/{slug}/blob/HEAD/{p})" for p in adr_paths
        )
        prompt = (
            f"Read each of these Architecture Decision Records in the repository {slug}:\n"
            f"{listing}\n\n"
            f"Write the body of a Slack post in two parts: (1) a short opening summary paragraph "
            f"synthesizing what these ADRs cover and why they matter; then (2) a bulleted "
            f"list with one bullet per ADR — a one-line gloss and its GitHub link. "
            f"Output only that body — no preamble, no title line, no sign-off, and no surrounding "
            f"code fences. Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
