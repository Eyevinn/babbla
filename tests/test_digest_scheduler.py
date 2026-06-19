from datetime import datetime, timedelta, timezone
import babbla.digest.actions as A
from babbla.config import DigestConfig, ProjectBinding, Topic
from babbla.digest.actions import PerProjectDigestAction
from babbla.session_store import DigestState
from babbla.digest.anchors import Change


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
    async def summarize(self, binding, changes, head_sha, topic=None):
        self.calls.append((binding.name, [c.sha for c in changes], head_sha))
        return f"digest:{head_sha}"


class FakePoster:
    def __init__(self): self.posts = []; self.blocks = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); self.blocks.append(blocks); return "ts"


def _action_ids(blocks):
    return [e["action_id"] for b in (blocks or []) if b.get("type") == "actions"
            for e in b["elements"]]


def _btn_value(blocks):
    for b in blocks or []:
        if b.get("type") == "actions":
            # Absent value == "anyone may delete" (Slack rejects an empty value).
            return b["elements"][0].get("value") or ""
    return None


def _action(binding, state, *, head, changes, monkeypatch):
    store, runner, poster = FakeStore(state), FakeRunner(), FakePoster()
    monkeypatch.setattr(A, "current_head", lambda b, *, get_json: head)
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: changes)
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: changes)
    action = PerProjectDigestAction(binding, store, lambda path: None, runner, poster)
    return action, store, runner, poster


NOW = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)


async def test_not_due_does_nothing(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState("old", NOW.timestamp()), head="new",
        changes=[Change("c", "x", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_first_run_branch_posts_window_and_sets_watermark(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState(None, None), head="H",
        changes=[Change("c1", "feat: a (#1)", 1)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", ["c1"], "H")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:H")]
    assert store.advanced == [("C0XXXXXXXXX", "H", NOW.timestamp())]


async def test_digest_post_carries_delete_button_for_anyone(monkeypatch):
    from babbla.blocks import DELETE_ACTION_ID
    action, store, runner, poster = _action(
        _binding(), DigestState(None, None), head="H",
        changes=[Change("c1", "feat: a (#1)", 1)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert DELETE_ACTION_ID in _action_ids(poster.blocks[-1])
    assert _btn_value(poster.blocks[-1]) == ""   # channel digest: anyone may delete


async def test_first_run_deploy_is_silent_but_sets_watermark(monkeypatch):
    action, store, runner, poster = _action(
        _binding("deploy", "cicd_prod.yml"), DigestState(None, None), head="D",
        changes=[Change("x", "y", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert store.advanced == [("C0XXXXXXXXX", "D", NOW.timestamp())]


async def test_due_and_new_posts_range(monkeypatch):
    last_week = (NOW - timedelta(days=8)).timestamp()
    action, store, runner, poster = _action(
        _binding(), DigestState("old", last_week), head="new",
        changes=[Change("c2", "fix: b", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", ["c2"], "new")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:new")]
    assert store.advanced == [("C0XXXXXXXXX", "new", NOW.timestamp())]


async def test_due_but_no_new_ship_stays_quiet_without_advancing(monkeypatch):
    last_week = (NOW - timedelta(days=8)).timestamp()
    action, store, runner, poster = _action(
        _binding(), DigestState("samehead", last_week), head="samehead",
        changes=[], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_no_ship_signal_skips(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState(None, None), head=None, changes=[], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert store.advanced == [] and poster.posts == []


class EmptyRunner:
    def __init__(self): self.calls = []
    async def summarize(self, binding, changes, head_sha, topic=None):
        self.calls.append((binding.name, topic.name if topic else None))
        return ""   # runner normalized NOTHING_RELEVANT to empty


async def test_topic_empty_advances_but_does_not_post(monkeypatch):
    binding = ProjectBinding(
        "MyTV", "o", "r", "public", "C0XXXXXXXXX", False,
        DigestConfig("weekly", "UTC", "branch", None, Topic("security", "auth")),
    )
    store, runner, poster = FakeStore(DigestState(None, None)), EmptyRunner(), FakePoster()
    monkeypatch.setattr(A, "current_head", lambda b, *, get_json: "H")
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: [Change("c", "x", None)])
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: [Change("c", "x", None)])
    action = PerProjectDigestAction(binding, store, lambda path: None, runner, poster)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", "security")]            # topic threaded through
    assert poster.posts == []                                # silent: empty summary
    assert store.advanced == [("C0XXXXXXXXX", "H", NOW.timestamp())]   # but watermark advanced
