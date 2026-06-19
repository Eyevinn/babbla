from datetime import datetime, timedelta, timezone
import babbla.digest.actions as A
from babbla.config import DigestConfig, ProjectBinding, Subscription, SubscriptionDigest, Topic
from babbla.digest.actions import SharedDigestAction
from babbla.session_store import SharedDigestState
from babbla.digest.anchors import Change

NOW = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)
LAST_WEEK = (NOW - timedelta(days=8)).timestamp()


def _b(name, anchor="branch", wf=None, digest=True):
    d = DigestConfig("weekly", "UTC", anchor, wf) if digest else None
    return ProjectBinding(name, "o", name.lower(), "public", f"C_{name}", False, d)


def _sub(names):
    return Subscription("C900", tuple(names), SubscriptionDigest("weekly", "UTC"))


class FakeShared:
    def __init__(self, state): self._state = state; self.advanced = []
    async def get(self, channel_id): return self._state
    async def advance(self, channel_id, heads, last_digest_at):
        self.advanced.append((channel_id, dict(heads), last_digest_at))


class FakeRunner:
    def __init__(self): self.calls = []
    async def summarize_shared(self, context_binding, per_project_changes, topic=None):
        self.calls.append((context_binding.name, {k: [c.sha for c in v] for k, v in per_project_changes.items()}))
        return "shared-digest"


class FakePoster:
    def __init__(self): self.posts = []; self.blocks = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); self.blocks.append(blocks); return "ts"


def _action(sub, bindings, state, *, heads, changes_map, monkeypatch):
    by_name = {b.name: b for b in bindings}
    store, runner, poster = FakeShared(state), FakeRunner(), FakePoster()
    monkeypatch.setattr(A, "head_for", lambda o, r, anchor, wf, *, get_json: heads.get(r))
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: changes_map.get(r, []))
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: changes_map.get(r, []))
    action = SharedDigestAction(sub, by_name, store, lambda path: None, runner, poster)
    return action, store, runner, poster


async def test_not_due_does_nothing(monkeypatch):
    state = SharedDigestState({"mytv": "old"}, NOW.timestamp())   # same weekly bucket
    action, store, runner, poster = _action(
        _sub(["MyTV"]), [_b("MyTV")], state,
        heads={"mytv": "new"}, changes_map={"mytv": [Change("c", "x", None)]}, monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_first_run_bootstrap_posts_and_advances_all(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1", "stream": "H2"},
        changes_map={"mytv": [Change("a", "feat (#1)", 1)], "stream": [Change("b", "fix", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", {"MyTV": ["a"], "Stream": ["b"]})]
    assert poster.posts == [("C900", "shared-digest")]
    assert store.advanced == [("C900", {"MyTV": "H1", "Stream": "H2"}, NOW.timestamp())]
    from babbla.blocks import DELETE_ACTION_ID
    btn = next(b["elements"][0] for b in poster.blocks[-1] if b.get("type") == "actions")
    assert btn["action_id"] == DELETE_ACTION_ID
    assert btn["value"] == ""   # shared channel digest: anyone may delete


async def test_all_quiet_no_post_no_advance(monkeypatch):
    state = SharedDigestState({"mytv": "H1", "stream": "H2"}, LAST_WEEK)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1", "stream": "H2"}, changes_map={}, monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_some_shipped_posts_shippers_and_advances_all(monkeypatch):
    state = SharedDigestState({"mytv": "old", "stream": "H2"}, LAST_WEEK)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1new", "stream": "H2"},          # stream unchanged
        changes_map={"mytv": [Change("a", "feat", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", {"MyTV": ["a"]})]      # only the shipper
    assert poster.posts == [("C900", "shared-digest")]
    # advance-all: both watermarks move forward + the channel timer
    assert store.advanced == [("C900", {"MyTV": "H1new", "Stream": "H2"}, NOW.timestamp())]


async def test_project_without_digest_defaults_to_branch(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["Plain"]), [_b("Plain", digest=False)], state,
        heads={"plain": "HP"}, changes_map={"plain": [Change("p", "x", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [("C900", "shared-digest")]
    assert store.advanced == [("C900", {"Plain": "HP"}, NOW.timestamp())]


async def test_none_head_project_skipped(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Dead"]), [_b("MyTV"), _b("Dead", "deploy", "wf.yml")], state,
        heads={"mytv": "H1", "dead": None},               # Dead has no ship signal
        changes_map={"mytv": [Change("a", "x", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    # Dead skipped: not advanced; MyTV posted + advanced
    assert store.advanced == [("C900", {"MyTV": "H1"}, NOW.timestamp())]


async def test_unknown_name_skipped_with_warning(monkeypatch, caplog):
    import logging
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Ghost"]), [_b("MyTV")], state,      # Ghost has no binding
        heads={"mytv": "H1"}, changes_map={"mytv": [Change("a", "x", None)]},
        monkeypatch=monkeypatch)
    with caplog.at_level(logging.WARNING, logger="babbla.digest.actions"):
        await action.maybe_run(NOW)
    assert store.advanced == [("C900", {"MyTV": "H1"}, NOW.timestamp())]
    assert any("Ghost" in r.message for r in caplog.records)


class EmptySharedRunner:
    def __init__(self): self.calls = []
    async def summarize_shared(self, context_binding, per_project_changes, topic=None):
        self.calls.append(topic.name if topic else None)
        return ""


async def test_topic_empty_advances_but_does_not_post(monkeypatch):
    state = SharedDigestState({}, None)
    by_name = {b.name: b for b in [_b("MyTV")]}
    store, runner, poster = FakeShared(state), EmptySharedRunner(), FakePoster()
    monkeypatch.setattr(A, "head_for", lambda o, r, anchor, wf, *, get_json: {"mytv": "H1"}.get(r))
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: [Change("a", "x", None)])
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: [Change("a", "x", None)])
    sub = Subscription("C900", ("MyTV",), SubscriptionDigest("weekly", "UTC", Topic("incidents", "outages")))
    action = SharedDigestAction(sub, by_name, store, lambda path: None, runner, poster)
    await action.maybe_run(NOW)
    assert runner.calls == ["incidents"]                            # topic threaded
    assert poster.posts == []                                       # silent
    assert store.advanced == [("C900", {"MyTV": "H1"}, NOW.timestamp())]   # but advanced
