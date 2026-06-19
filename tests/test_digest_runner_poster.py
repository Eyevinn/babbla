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
    def __init__(self): self.calls = []
    async def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "ts": "111.222"}


async def test_poster_posts_top_level_and_returns_ts():
    client = FakeClient()
    ts = await SlackPoster(client).post("C0XXXXXXXXX", "hello")
    assert ts == "111.222"
    assert client.calls == [{"channel": "C0XXXXXXXXX", "text": "hello"}]


async def test_poster_posts_threaded_reply():
    client = FakeClient()
    await SlackPoster(client).post("C0XXXXXXXXX", "answer", thread_ts="111.222")
    assert client.calls == [{"channel": "C0XXXXXXXXX", "text": "answer", "thread_ts": "111.222"}]


async def test_summarize_shared_groups_by_project():
    agent = FakeAgent()
    out = await DigestRunner(agent).summarize_shared(
        _binding(),
        {
            "MyTV": [Change("abc1234", "feat: playback (#7)", 7)],
            "Stream": [Change("def5678", "fix: retry", None)],
        },
    )
    assert out == "SUMMARY"
    p = agent.prompt
    assert "MyTV" in p and "Stream" in p
    assert "abc1234" in p and "feat: playback (#7)" in p
    assert "def5678" in p and "fix: retry" in p
