from __future__ import annotations

from babbla.config import ProjectBinding


class QuizRunner:
    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def generate(self, binding: ProjectBinding, count: int) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        prompt = (
            f"Create a short Slack quiz of {count} questions to test a colleague's knowledge of the "
            f"project {slug}. Draw the questions from the project's README, docs/, ADRs, and notable "
            f"history. Number the questions. After the last question, output a line containing exactly "
            f"===ANSWERS=== and then the numbered answers. Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
