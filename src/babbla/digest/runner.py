from __future__ import annotations

from babbla.config import ProjectBinding
from babbla.digest.anchors import Change


def _facts(changes: list[Change]) -> str:
    lines = []
    for c in changes:
        pr = f" (#{c.pr_number})" if c.pr_number else ""
        lines.append(f"- {c.sha[:7]} {c.subject}{pr}")
    return "\n".join(lines)


class DigestRunner:
    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def summarize(self, binding: ProjectBinding, changes: list[Change], head_sha: str) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        prompt = (
            f"Write a concise Slack digest of what shipped in {slug} (now at {head_sha[:7]}). "
            f"These are the changes in scope — summarize them at a reader-friendly altitude, "
            f"group related work, and CITE commits by SHA and PRs by number as GitHub links:\n\n"
            f"{_facts(changes)}\n\n"
            f"Keep it short and Slack-friendly. Lead with the headline. If the changes are all "
            f"minor/chore, say so briefly rather than padding."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
