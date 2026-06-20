from __future__ import annotations

from babbla.config import ProjectBinding


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
