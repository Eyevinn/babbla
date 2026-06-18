import pytest
from babbla.agent_runner import CitedAnswer
from babbla.config import DigestConfig, ProjectBinding
from babbla.digest.anchors import Change
from babbla.digest.runner import DigestRunner
from babbla.digest.poster import SlackPoster


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          DigestConfig("weekly", "UTC", "branch"))


class FakeAgent:
    def __init__(self): self.prompt = None
    async def run_ask(self, text, binding, resume_session_id):
        self.prompt = text
        assert resume_session_id is None       # digests are stateless
        return CitedAnswer(text="SUMMARY", session_id="ignored")


async def test_runner_builds_prompt_with_facts_and_returns_text():
    agent = FakeAgent()
    out = await DigestRunner(agent).summarize(
        _binding(),
        [Change("abc1234", "feat: thing (#7)", 7), Change("def5678", "chore: tidy", None)],
        "head99",
    )
    assert out == "SUMMARY"
    p = agent.prompt
    assert "Wkkkkk/MyTV" in p
    assert "feat: thing (#7)" in p and "abc1234" in p
    assert "#7" in p and "chore: tidy" in p


class FakeClient:
    def __init__(self): self.kwargs = None
    async def chat_postMessage(self, **kwargs): self.kwargs = kwargs; return {"ok": True}


async def test_poster_posts_top_level_message():
    client = FakeClient()
    await SlackPoster(client).post("C0XXXXXXXXX", "hello")
    assert client.kwargs == {"channel": "C0XXXXXXXXX", "text": "hello"}
