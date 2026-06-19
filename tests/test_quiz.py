from babbla.agent_runner import CitedAnswer
from babbla.config import ProjectBinding, QuizConfig
from babbla.digest.quiz import QuizRunner


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          quiz=QuizConfig("weekly", "UTC", 3))


class FakeAgent:
    def __init__(self): self.prompt = None
    async def run_ask(self, text, binding, resume_session_id):
        self.prompt = text
        assert resume_session_id is None
        return CitedAnswer(text="Q1?\n===ANSWERS===\nA1", session_id="ignored")


async def test_quiz_runner_builds_prompt_and_returns_text():
    agent = FakeAgent()
    out = await QuizRunner(agent).generate(_binding(), 3)
    assert out == "Q1?\n===ANSWERS===\nA1"
    p = agent.prompt
    assert "Wkkkkk/MyTV" in p
    assert "3" in p
    assert "===ANSWERS===" in p
