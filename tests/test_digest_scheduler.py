from datetime import datetime, timedelta, timezone
import pytest
from babbla.config import Config, DigestConfig, ProjectBinding
from babbla.digest.scheduler import DigestScheduler


def _binding(anchor="branch", wf=None):
    return ProjectBinding("MyTV", "o", "r", "public", "C0XXXXXXXXX", False,
                          DigestConfig("weekly", "UTC", anchor, wf))


class FakeStore:
    def __init__(self, state): self._state = state; self.advanced = []
    async def get(self, channel_id): return self._state
    async def advance(self, channel_id, watermark_sha, last_digest_at):
        self.advanced.append((channel_id, watermark_sha, last_digest_at))


class FakeRunner:
    def __init__(self): self.calls = []
    async def summarize(self, binding, changes, head_sha):
        self.calls.append((binding.name, [c.sha for c in changes], head_sha))
        return f"digest:{head_sha}"


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text): self.posts.append((channel_id, text))


from babbla.session_store import DigestState
from babbla.digest.anchors import Change


def _sched(binding, state, *, head, changes, now):
    store, runner, poster = FakeStore(state), FakeRunner(), FakePoster()
    sched = DigestScheduler(
        config=Config(bindings=(binding,)),
        store=store, runner=runner, poster=poster,
        get_json=lambda path: None,           # unused: monkeypatched below
        now_fn=lambda: now,
    )
    # Patch the module-level anchor calls the scheduler uses.
    import babbla.digest.scheduler as S
    S.current_head = lambda b, *, get_json: head
    S.changes_between = lambda o, r, base, hd, *, get_json: changes
    S.changes_since = lambda o, r, since, *, get_json: changes
    return sched, store, runner, poster


NOW = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)


async def test_not_due_does_nothing():
    state = DigestState("old", NOW.timestamp())  # same weekly bucket
    sched, store, runner, poster = _sched(_binding(), state, head="new", changes=[Change("c", "x", None)], now=NOW)
    await sched.tick(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_first_run_branch_posts_window_and_sets_watermark():
    state = DigestState(None, None)
    changes = [Change("c1", "feat: a (#1)", 1)]
    sched, store, runner, poster = _sched(_binding(), state, head="H", changes=changes, now=NOW)
    await sched.tick(NOW)
    assert runner.calls == [("MyTV", ["c1"], "H")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:H")]
    assert store.advanced == [("C0XXXXXXXXX", "H", NOW.timestamp())]


async def test_first_run_deploy_is_silent_but_sets_watermark():
    state = DigestState(None, None)
    sched, store, runner, poster = _sched(_binding("deploy", "cicd_prod.yml"), state,
                                          head="D", changes=[Change("x", "y", None)], now=NOW)
    await sched.tick(NOW)
    assert runner.calls == [] and poster.posts == []          # silent bootstrap
    assert store.advanced == [("C0XXXXXXXXX", "D", NOW.timestamp())]


async def test_due_and_new_posts_range():
    last_week = (NOW - timedelta(days=8)).timestamp()
    state = DigestState("old", last_week)
    changes = [Change("c2", "fix: b", None)]
    sched, store, runner, poster = _sched(_binding(), state, head="new", changes=changes, now=NOW)
    await sched.tick(NOW)
    assert runner.calls == [("MyTV", ["c2"], "new")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:new")]
    assert store.advanced == [("C0XXXXXXXXX", "new", NOW.timestamp())]


async def test_due_but_no_new_ship_stays_quiet_without_advancing():
    last_week = (NOW - timedelta(days=8)).timestamp()
    state = DigestState("samehead", last_week)
    sched, store, runner, poster = _sched(_binding(), state, head="samehead", changes=[], now=NOW)
    await sched.tick(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_no_ship_signal_skips():
    state = DigestState(None, None)
    sched, store, runner, poster = _sched(_binding(), state, head=None, changes=[], now=NOW)
    await sched.tick(NOW)
    assert store.advanced == [] and poster.posts == []
