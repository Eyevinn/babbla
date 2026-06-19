from __future__ import annotations

from babbla.config import ProjectBinding, Topic
from babbla.digest.anchors import Change

NOTHING_RELEVANT = "NOTHING_RELEVANT"


def _topic_preamble(topic: Topic) -> str:
    return (
        f'This digest is scoped to the topic "{topic.name}": {topic.description}. '
        "Include ONLY changes relevant to this topic; omit everything else — do not pad. "
        "If NONE of the changes below are relevant to this topic, reply with exactly: "
        f"{NOTHING_RELEVANT}\n\n"
    )


def _facts(changes: list[Change]) -> str:
    lines = []
    for c in changes:
        pr = f" (#{c.pr_number})" if c.pr_number else ""
        lines.append(f"- {c.sha[:7]} {c.subject}{pr}")
    return "\n".join(lines)


class DigestRunner:
    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def summarize(
        self, binding: ProjectBinding, changes: list[Change], head_sha: str,
        topic: Topic | None = None,
    ) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        preamble = _topic_preamble(topic) if topic else ""
        prompt = preamble + (
            f"Write a concise Slack digest of what shipped in {slug} (now at {head_sha[:7]}). "
            f"These are the changes in scope — summarize them at a reader-friendly altitude, "
            f"group related work, and CITE commits by SHA and PRs by number as GitHub links:\n\n"
            f"{_facts(changes)}\n\n"
            f"Keep it short and Slack-friendly. Lead with the headline. If the changes are all "
            f"minor/chore, say so briefly rather than padding."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        if topic and answer.text.strip() == NOTHING_RELEVANT:
            return ""
        return answer.text

    async def summarize_shared(
        self, context_binding: ProjectBinding, per_project_changes: dict[str, list[Change]],
        topic: Topic | None = None,
    ) -> str:
        sections = "\n\n".join(
            f"## {name}\n{_facts(changes)}" for name, changes in per_project_changes.items()
        )
        preamble = _topic_preamble(topic) if topic else ""
        prompt = preamble + (
            "Write ONE concise Slack digest of what shipped across several projects this period. "
            "Lead with a short cross-project headline, then a section per project. Summarize at a "
            "reader-friendly altitude, group related work, and CITE commits by SHA and PRs by number "
            "as GitHub links. Keep it short and Slack-friendly.\n\n"
            f"{sections}"
        )
        answer = await self._agent.run_ask(prompt, context_binding, None)
        if topic and answer.text.strip() == NOTHING_RELEVANT:
            return ""
        return answer.text
