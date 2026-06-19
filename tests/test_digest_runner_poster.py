import pytest
from babbla.agent_runner import CitedAnswer
from babbla.config import DigestConfig, ProjectBinding, Topic
from babbla.digest.anchors import Change
from babbla.digest.runner import DigestRunner, NOTHING_RELEVANT
from babbla.digest.poster import SlackPoster


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          DigestConfig("weekly", "UTC", "branch"))


class FakeAgent:
    def __init__(self): self.prompt = None; self.system_prompt = None
    async def run_ask(self, text, binding, resume_session_id, *, system_prompt=None):
        self.prompt = text
        self.system_prompt = system_prompt
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


async def test_summarize_uses_digest_system_prompt_not_qa():
    agent = FakeAgent()
    await DigestRunner(agent).summarize(
        _binding(), [Change("abc1234", "feat: x", None)], "head99",
    )
    sp = agent.system_prompt
    assert sp is not None                      # an explicit digest prompt is passed
    assert "disclaimer" in sp.lower()          # forbids the scope/verification disclaimer
    assert "digest" in sp.lower()


async def test_summarize_shared_uses_digest_system_prompt():
    agent = FakeAgent()
    await DigestRunner(agent).summarize_shared(
        _binding(), {"MyTV": [Change("a", "x", None)]},
    )
    assert agent.system_prompt is not None
    assert "disclaimer" in agent.system_prompt.lower()


async def test_summarize_shared_labels_sections_with_repo_slug():
    agent = FakeAgent()
    await DigestRunner(agent).summarize_shared(
        _binding(),
        {"MyTV": [Change("abc1234", "feat", None)], "Stream": [Change("def5678", "fix", None)]},
        slugs={"MyTV": "Wkkkkk/MyTV", "Stream": "eyevinn/stream-starter"},
    )
    p = agent.prompt
    assert "Wkkkkk/MyTV" in p and "eyevinn/stream-starter" in p


async def test_open_dm_returns_channel_id():
    class FakeClient:
        def __init__(self):
            self.opened = None
        async def conversations_open(self, *, users):
            self.opened = users
            return {"channel": {"id": "D123"}}
    client = FakeClient()
    poster = SlackPoster(client)
    assert await poster.open_dm("U7") == "D123"
    assert client.opened == "U7"


class SentinelAgent:
    """Returns whatever text it is configured with; captures the prompt."""
    def __init__(self, text):
        self.text = text
        self.prompt = None
    async def run_ask(self, prompt, binding, resume_session_id, *, system_prompt=None):
        self.prompt = prompt
        self.system_prompt = system_prompt
        assert resume_session_id is None
        return CitedAnswer(text=self.text, session_id="ignored")


async def test_summarize_topic_injects_scoping_preamble():
    agent = FakeAgent()
    await DigestRunner(agent).summarize(
        _binding(), [Change("abc1234", "feat: x", None)], "head99",
        topic=Topic("security", "auth and secrets"),
    )
    p = agent.prompt
    assert "security" in p and "auth and secrets" in p
    assert NOTHING_RELEVANT in p


async def test_summarize_no_topic_has_no_preamble():
    agent = FakeAgent()
    await DigestRunner(agent).summarize(
        _binding(), [Change("abc1234", "feat: x", None)], "head99",
    )
    assert "scoped to the topic" not in agent.prompt
    assert NOTHING_RELEVANT not in agent.prompt


async def test_summarize_topic_sentinel_returns_empty():
    agent = SentinelAgent(NOTHING_RELEVANT)
    out = await DigestRunner(agent).summarize(
        _binding(), [Change("abc1234", "feat: x", None)], "head99",
        topic=Topic("security", "auth"),
    )
    assert out == ""


async def test_summarize_shared_topic_injects_preamble_and_sentinel_empties():
    agent = SentinelAgent(NOTHING_RELEVANT)
    out = await DigestRunner(agent).summarize_shared(
        _binding(), {"MyTV": [Change("a", "x", None)]}, topic=Topic("incidents", "outages"),
    )
    assert out == ""
    assert "incidents" in agent.prompt and "outages" in agent.prompt and NOTHING_RELEVANT in agent.prompt
