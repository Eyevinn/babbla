import asyncio

import pytest

from babbla.agent_runner import Artifact, CitedAnswer
from babbla.config import Config, ProjectBinding
from babbla.orchestrator import Orchestrator, UnknownSurfaceError
from babbla.session_store import SessionStore

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
CONFIG = Config(bindings=(BINDING,))


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.next_session = "sess-1"

    async def run_ask(self, text, binding, resume_session_id, *, scratch_key=None):
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
        async def run_ask(self, text, binding, resume_session_id, *, scratch_key=None):
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
        async def run_ask(self, text, binding, resume_session_id, *, scratch_key=None):
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


from babbla.lobby import CatalogEntry
from babbla.session_store import LobbyThreadStore

PUB = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", False)
PRIV = ProjectBinding("Secret", "Wkkkkk", "Secret", "private", "C777", False)
CATALOG = (CatalogEntry(PUB, None), CatalogEntry(PRIV, None))


def _classify_returning(name, *, recorder=None):
    async def classify(text, catalog):
        if recorder is not None:
            recorder.append((text, catalog))
        return name
    return classify


def _lobby_orch(config_bindings, runner, store, classify, lobby_store):
    return Orchestrator(
        Config(bindings=config_bindings),
        runner,
        store,
        catalog=CATALOG,
        classify_fn=classify,
        lobby_store=lobby_store,
    )


async def test_lobby_routes_public_runs_and_persists(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _lobby_orch((PUB, PRIV), runner, store, _classify_returning("MyTV"), lobby_store)
    ans = await orch.handle_lobby_ask(text="how does playback work?", thread_ts="tl")
    assert ans.text.startswith("answer to how does playback work?")
    assert "<#C123>" in ans.text                       # pointer suffix appended
    assert runner.calls[0][1].name == "MyTV"           # ran the routed project
    assert await lobby_store.get("tl") == "MyTV"        # sticky persisted
    assert await store.get_session("tl") == "sess-1"    # session persisted
    lobby_store.close()


async def test_lobby_sticky_skips_routing(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    await lobby_store.put("tl", "MyTV")
    recorder = []
    orch = _lobby_orch((PUB, PRIV), runner, store, _classify_returning("Secret", recorder=recorder), lobby_store)
    ans = await orch.handle_lobby_ask(text="follow up", thread_ts="tl")
    assert recorder == []                               # classifier NOT called on sticky hit
    assert runner.calls[0][1].name == "MyTV"            # stayed on the sticky project
    lobby_store.close()


async def test_lobby_no_match_returns_discovery(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _lobby_orch((PUB, PRIV), runner, store, _classify_returning("NONE"), lobby_store)
    ans = await orch.handle_lobby_ask(text="unrelated", thread_ts="tl")
    assert "I can help with" in ans.text                # discovery reply
    assert "Secret" not in ans.text                     # private not advertised
    assert runner.calls == []                           # no agent run
    assert await lobby_store.get("tl") is None          # nothing persisted
    lobby_store.close()


async def test_lobby_private_match_points_dont_reveal(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _lobby_orch((PUB, PRIV), runner, store, _classify_returning("Secret"), lobby_store)
    ans = await orch.handle_lobby_ask(text="how does Secret auth work?", thread_ts="tl")
    assert "<#C777>" in ans.text                        # points to its channel
    assert runner.calls == []                           # never ran the agent
    assert await lobby_store.get("tl") is None          # no sticky for a denied match
    lobby_store.close()


async def test_lobby_sticky_project_now_private_is_denied(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    await lobby_store.put("tl", "Secret")               # previously routed, now private
    orch = _lobby_orch((PUB, PRIV), runner, store, _classify_returning("Secret"), lobby_store)
    ans = await orch.handle_lobby_ask(text="follow up", thread_ts="tl")
    assert "<#C777>" in ans.text                        # re-authorized -> points-don't-reveal
    assert runner.calls == []
    lobby_store.close()


class _FakeLobbyStore:
    def __init__(self):
        self._d = {}
    async def get(self, thread_ts):
        return self._d.get(thread_ts)
    async def put(self, thread_ts, project):
        self._d[thread_ts] = project


from babbla.session_store import PersonalSubStore


def _config_two():
    pub = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", True)
    priv = ProjectBinding("Secret", "o", "secret", "private", "C2", False)
    return Config(bindings=(pub, priv))


@pytest.fixture
def psub(tmp_path):
    s = PersonalSubStore(str(tmp_path / "p.db"))
    yield s
    s.close()


async def test_handle_command_subscribe_known(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe MyTV")
    assert "MyTV" in reply
    assert await psub.list_for("U1") == ("MyTV",)


async def test_handle_command_subscribe_unknown_writes_nothing(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Ghost")
    assert "don't know" in reply.lower()
    assert await psub.list_for("U1") == ()


async def test_handle_command_subscribe_unknown_does_not_leak_private_names(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Ghost")
    assert "MyTV" in reply          # public project advertised
    assert "Secret" not in reply    # private project never named


async def test_handle_command_subscribe_private_refused(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert "private" in reply.lower()
    assert await psub.list_for("U1") == ()


def _intent_fn(reply, recorder=None):
    async def fn(text, names):
        if recorder is not None:
            recorder.append((text, tuple(names)))
        return reply
    return fn


async def test_dm_management_intent_dispatches_without_invoking_runner(store, psub):
    runner = FakeRunner()
    orch = Orchestrator(_config_two(), runner, store, personal_store=psub,
                        intent_fn=_intent_fn("subscribe MyTV"))
    ans = await orch.handle_ask(
        text="please follow MyTV for me", thread_ts="t1",
        channel_id="D1", is_dm=True, user_id="U1",
    )
    assert "MyTV" in ans.text
    assert ans.session_id is None
    assert await psub.list_for("U1") == ("MyTV",)
    assert runner.calls == []           # read-only Q&A agent never reached


async def test_dm_non_management_falls_through_to_qa(store, psub):
    runner = FakeRunner()
    orch = Orchestrator(_config_two(), runner, store, personal_store=psub,
                        intent_fn=_intent_fn("NONE"))
    ans = await orch.handle_ask(
        text="how does the digest work?", thread_ts="t1",
        channel_id="D1", is_dm=True, user_id="U1",
    )
    assert ans.text == "answer to how does the digest work?"
    assert len(runner.calls) == 1       # answered by the Q&A agent


async def test_channel_message_never_consults_intent_shim(store, psub):
    recorder = []
    runner = FakeRunner()
    orch = Orchestrator(_config_two(), runner, store, personal_store=psub,
                        intent_fn=_intent_fn("subscribe MyTV", recorder))
    await orch.handle_ask(
        text="subscribe MyTV", thread_ts="t1",
        channel_id="C1", is_dm=False, user_id="U1",   # C1 == MyTV's channel
    )
    assert recorder == []               # intent shim is DM-only
    assert len(runner.calls) == 1       # treated as a normal channel Ask
    assert await psub.list_for("U1") == ()


async def test_handle_command_unsubscribe(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    await orch.handle_command("U1", "unsubscribe MyTV")
    assert await psub.list_for("U1") == ()


async def test_handle_command_digest_sets_cadence(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "digest daily")
    assert "daily" in reply
    assert await psub.get_cadence("U1") == "daily"


async def test_handle_command_list_shows_default_cadence(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store,
                        personal_store=psub, personal_default_cadence="weekly")
    await psub.add("U1", "MyTV")
    reply = await orch.handle_command("U1", "list")
    assert "MyTV" in reply and "weekly" in reply


def _catalog_two():
    pub = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", True)
    other = ProjectBinding("Stream", "o", "stream", "internal", "C2", False)
    return (CatalogEntry(pub, None), CatalogEntry(other, None))


async def test_dm_empty_subs_falls_back_to_dm_true(store, psub):
    # CONFIG has the single dm:true MyTV binding (module-level in this file)
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub, catalog=_catalog_two())
    runner = orch._runner
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert runner.calls[0][1].name == "MyTV"   # fell back to dm:true project
    assert ans.text == "answer to q"


async def test_dm_size1_answers_directly_no_classifier(store, psub):
    classifier_calls = []
    async def classify_fn(text, catalog):
        classifier_calls.append(text)
        return "Stream"
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub,
                        catalog=_catalog_two(), classify_fn=classify_fn,
                        lobby_store=_FakeLobbyStore())
    await psub.add("U1", "Stream")
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert orch._runner.calls[0][1].name == "Stream"
    assert classifier_calls == []              # size-1 shortcut: no routing call
    assert ans.text.endswith("answer to q")    # no pointer suffix


async def test_dm_two_subs_routes_via_classifier(store, psub):
    async def classify_fn(text, catalog):
        return "Stream"
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub,
                        catalog=_catalog_two(), classify_fn=classify_fn,
                        lobby_store=_FakeLobbyStore())
    await psub.add("U1", "MyTV")
    await psub.add("U1", "Stream")
    await orch.handle_ask(text="why HLS", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert orch._runner.calls[0][1].name == "Stream"


# ---------------------------------------------------------------------------
# Topic command dispatch tests
# ---------------------------------------------------------------------------
from babbla import personal  # noqa: E402  (already imported transitively but explicit here)


async def test_dispatch_topic_add_to_followed_project(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="MyTV", name="security", description="auth, secrets"))
    assert "security" in reply and "auth, secrets" in reply
    assert (await psub.topics_for("U1")) == {"MyTV": (("security", "auth, secrets"),)}


async def test_dispatch_topic_add_requires_following(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    # not following MyTV
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="MyTV", name="security", description="x"))
    assert "follow" in reply.lower()
    assert await psub.topics_for("U1") == {}


async def test_dispatch_topic_add_unknown_project(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="Nope", name="x", description="y"))
    assert "don't know that project" in reply.lower()
    assert await psub.topics_for("U1") == {}


async def test_dispatch_topic_add_private_refused(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    # "Secret" is private
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="Secret", name="x", description="y"))
    assert "private" in reply.lower()
    assert await psub.topics_for("U1") == {}


async def test_dispatch_topic_remove_and_list(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    await psub.add_topic("U1", "MyTV", "security", "auth")
    listed = await orch._dispatch_command("U1", personal.Command("topic-list"))
    assert "MyTV" in listed and "security" in listed
    await orch._dispatch_command("U1", personal.Command("topic-remove", project="MyTV", name="security"))
    assert await psub.topics_for("U1") == {}


async def test_dispatch_topic_add_description_falls_back_to_name(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="MyTV", name="security", description=None))
    assert "security" in reply
    assert (await psub.topics_for("U1")) == {"MyTV": (("security", "security"),)}


async def test_dispatch_topic_remove_idempotent(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    # No topic set; removing must not raise and returns a confirmation.
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-remove", project="MyTV", name="security"))
    assert "security" in reply
    assert await psub.topics_for("U1") == {}


# ---------------------------------------------------------------------------
# scratch_key + artifact preservation tests
# ---------------------------------------------------------------------------


class ArtifactRunner:
    def __init__(self):
        self.scratch_keys = []

    async def run_ask(self, text, binding, resume_session_id, *, scratch_key=None):
        self.scratch_keys.append(scratch_key)
        return CitedAnswer(text="drew it", session_id="s1",
                           artifacts=(Artifact("architecture.html", b"<svg/>"),))


async def test_handle_ask_passes_thread_ts_as_scratch_key(store):
    runner = ArtifactRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="draw", thread_ts="t1", channel_id="C123", is_dm=False)
    assert ans.artifacts and ans.artifacts[0].filename == "architecture.html"
    assert runner.scratch_keys == ["t1"]        # thread_ts threaded through as scratch_key


async def test_lobby_ask_preserves_artifacts_and_scratch_key(store, tmp_path):
    runner = ArtifactRunner()
    entry = CatalogEntry(BINDING, None)         # (binding, description) — matches build_catalog
    lobby_store = LobbyThreadStore(str(tmp_path / "lobby.db"))
    await lobby_store.put("t1", BINDING.name)   # sticky → deterministic route, no classifier call
    orch = Orchestrator(CONFIG, runner, store, catalog=(entry,), lobby_store=lobby_store)
    ans = await orch.handle_lobby_ask(text="draw", thread_ts="t1")
    assert ans.artifacts and ans.artifacts[0].filename == "architecture.html"
    assert runner.scratch_keys == ["t1"]
    lobby_store.close()
