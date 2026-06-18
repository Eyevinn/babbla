import asyncio

import pytest

from babbla.agent_runner import CitedAnswer
from babbla.config import Config, ProjectBinding
from babbla.orchestrator import Orchestrator, UnknownSurfaceError
from babbla.session_store import SessionStore

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
CONFIG = Config(bindings=(BINDING,))


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.next_session = "sess-1"

    async def run_ask(self, text, binding, resume_session_id):
        self.calls.append((text, binding, resume_session_id))
        return CitedAnswer(text=f"answer to {text}", session_id=self.next_session)


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    yield s
    s.close()


async def test_new_thread_creates_session(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False)
    assert ans.text == "answer to q1"
    assert runner.calls[0][2] is None  # no resume on first message
    assert await store.get_session("t1") == "sess-1"


async def test_followup_resumes_session(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    await orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False)
    runner.next_session = "sess-1"  # SDK may keep same id on resume
    await orch.handle_ask(text="q2", thread_ts="t1", channel_id="C123", is_dm=False)
    assert runner.calls[1][2] == "sess-1"  # resumed with prior session id


async def test_dm_resolves_via_for_dm(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="t9", channel_id="D999", is_dm=True)
    assert runner.calls[0][1].name == "MyTV"
    assert ans.text == "answer to q"


async def test_unknown_channel_raises(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    with pytest.raises(UnknownSurfaceError):
        await orch.handle_ask(text="q", thread_ts="t1", channel_id="CNOPE", is_dm=False)


async def test_per_thread_lock_serializes(store):
    # Two concurrent asks in the SAME thread must not both run with resume=None.
    order = []
    binding = BINDING

    class SlowRunner:
        async def run_ask(self, text, binding, resume_session_id):
            order.append(("start", text, resume_session_id))
            await asyncio.sleep(0.01)
            order.append(("end", text))
            return CitedAnswer(text=f"a-{text}", session_id="sess-1")

    orch = Orchestrator(CONFIG, SlowRunner(), store)
    await asyncio.gather(
        orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False),
        orch.handle_ask(text="q2", thread_ts="t1", channel_id="C123", is_dm=False),
    )
    # Serialized: first ask fully completes before the second starts.
    assert order[0][0] == "start" and order[1][0] == "end"
    # The second ask saw the session the first one stored.
    second_start = [o for o in order if o[0] == "start"][1]
    assert second_start[2] == "sess-1"


async def test_locks_do_not_accumulate_across_threads(store):
    # Each distinct thread must not leave a permanent lock behind, or a
    # long-lived process leaks one lock per thread it ever served.
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    for i in range(50):
        await orch.handle_ask(
            text="q", thread_ts=f"t{i}", channel_id="C123", is_dm=False
        )
    assert len(orch._locks) == 0


async def test_concurrent_asks_in_one_thread_share_one_lock(store):
    # While asks are in flight in the same thread they must share a lock
    # (serialization), and it must be cleaned up once the thread is idle.
    order = []

    class SlowRunner:
        async def run_ask(self, text, binding, resume_session_id):
            order.append(len(orch._locks))
            await asyncio.sleep(0.01)
            return CitedAnswer(text=f"a-{text}", session_id="sess-1")

    orch = Orchestrator(CONFIG, SlowRunner(), store)
    await asyncio.gather(
        orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False),
        orch.handle_ask(text="q2", thread_ts="t1", channel_id="C123", is_dm=False),
    )
    # Exactly one lock existed while work was in flight (both asks shared it)...
    assert order == [1, 1]
    # ...and nothing is retained once the thread goes idle.
    assert len(orch._locks) == 0


PRIVATE_BINDING = ProjectBinding("Secret", "Wkkkkk", "Secret", "private", "C777", True)
PRIVATE_CONFIG = Config(bindings=(PRIVATE_BINDING,))


async def test_dm_about_private_denies_without_runner_or_store(store):
    runner = FakeRunner()
    orch = Orchestrator(PRIVATE_CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tp", channel_id="D999", is_dm=True)
    assert "<#C777>" in ans.text          # points to the channel
    assert ans.session_id is None
    assert runner.calls == []             # runner never invoked
    assert await store.get_session("tp") is None  # nothing written


async def test_channel_about_private_calls_runner(store):
    runner = FakeRunner()
    orch = Orchestrator(PRIVATE_CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tc", channel_id="C777", is_dm=False)
    assert ans.text == "answer to q"      # channel = access; answered normally
    assert runner.calls[0][1].name == "Secret"


async def test_dm_about_public_still_calls_runner(store):
    # MyTV regression guard: public DM behavior unchanged.
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tx", channel_id="D999", is_dm=True)
    assert ans.text == "answer to q"
    assert runner.calls[0][1].name == "MyTV"
