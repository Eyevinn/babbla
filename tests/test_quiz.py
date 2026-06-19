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


from datetime import datetime, timedelta, timezone
from babbla.digest.actions import QuizAction

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakeQuizRunner:
    def __init__(self, text): self._text = text; self.calls = []
    async def generate(self, binding, count): self.calls.append((binding.name, count)); return self._text


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text, thread_ts)); return "TS1"


def _quiz_action(last, text, monkeypatch):
    timer, runner, poster = FakeTimer(last), FakeQuizRunner(text), FakePoster()
    action = QuizAction(_binding(), timer, runner, poster, "weekly", "UTC", 3)
    return action, timer, runner, poster


async def test_quiz_not_due_does_nothing(monkeypatch):
    action, timer, runner, poster = _quiz_action(NOW.timestamp(), "Q\n===ANSWERS===\nA", monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == []


async def test_quiz_due_posts_questions_then_answers_in_thread(monkeypatch):
    action, timer, runner, poster = _quiz_action(None, "Q1?\n===ANSWERS===\nA1", monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [
        ("C0XXXXXXXXX", "Q1?", None),
        ("C0XXXXXXXXX", "A1", "TS1"),       # answers threaded under the questions ts
    ]
    assert timer.advanced == [("quiz:MyTV", NOW.timestamp())]


async def test_quiz_without_delimiter_posts_questions_only(monkeypatch):
    action, timer, runner, poster = _quiz_action(None, "just questions, no answers", monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [("C0XXXXXXXXX", "just questions, no answers", None)]
    assert timer.advanced == [("quiz:MyTV", NOW.timestamp())]


async def test_quiz_same_bucket_second_run_not_due(monkeypatch):
    action, timer, runner, poster = _quiz_action((NOW - timedelta(hours=1)).timestamp(),
                                                 "Q\n===ANSWERS===\nA", monkeypatch)
    await action.maybe_run(NOW)                # same weekly bucket as 1h ago
    assert poster.posts == []
