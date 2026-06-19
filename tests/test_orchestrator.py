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


from babbla.config import Subscription


def _sub_orch(bindings, subs, runner, store, classify, lobby_store):
    return Orchestrator(
        Config(bindings=bindings, subscriptions=subs),
        runner,
        store,
        catalog=CATALOG,
        classify_fn=classify,
        lobby_store=lobby_store,
    )


SUBS_TWO = (Subscription("C900", ("MyTV", "Secret")),)


async def test_subscription_routes_runs_and_persists_no_suffix(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("MyTV"), lobby_store)
    ans = await orch.handle_ask(text="how does playback work?", thread_ts="ts", channel_id="C900", is_dm=False)
    assert ans.text == "answer to how does playback work?"   # NO pointer suffix
    assert "↪" not in ans.text
    assert runner.calls[0][1].name == "MyTV"
    assert await lobby_store.get("ts") == "MyTV"              # sticky persisted
    assert await store.get_session("ts") == "sess-1"          # session persisted
    lobby_store.close()


async def test_subscription_sticky_skips_routing(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    await lobby_store.put("ts", "MyTV")
    recorder = []
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("Secret", recorder=recorder), lobby_store)
    await orch.handle_ask(text="follow up", thread_ts="ts", channel_id="C900", is_dm=False)
    assert recorder == []                                     # classifier NOT called on sticky hit
    assert runner.calls[0][1].name == "MyTV"
    lobby_store.close()


async def test_subscription_no_match_clarifies(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("NONE"), lobby_store)
    ans = await orch.handle_ask(text="ambiguous", thread_ts="ts", channel_id="C900", is_dm=False)
    assert "MyTV" in ans.text and "Secret" in ans.text       # lists subscribed projects
    assert runner.calls == []                                 # no agent run
    assert await lobby_store.get("ts") is None                # nothing persisted
    assert await store.get_session("ts") is None
    lobby_store.close()


async def test_subscription_size_one_skips_classifier(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    recorder = []
    subs = (Subscription("C901", ("MyTV",)),)
    orch = _sub_orch((PUB, PRIV), subs, runner, store, _classify_returning("NONE", recorder=recorder), lobby_store)
    ans = await orch.handle_ask(text="anything", thread_ts="ts", channel_id="C901", is_dm=False)
    assert recorder == []                                     # no classifier call for size-1
    assert runner.calls[0][1].name == "MyTV"
    assert ans.text == "answer to anything"
    lobby_store.close()


async def test_subscription_private_project_is_answered(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("Secret"), lobby_store)
    ans = await orch.handle_ask(text="how does Secret work?", thread_ts="ts", channel_id="C900", is_dm=False)
    assert ans.text == "answer to how does Secret work?"     # channel = access
    assert runner.calls[0][1].name == "Secret"
    lobby_store.close()


async def test_non_subscription_channel_unchanged(store, tmp_path):
    # A channel NOT in subscriptions takes the existing single-project path; router untouched.
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    recorder = []
    single = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", False)
    orch = _sub_orch((single,), SUBS_TWO, runner, store, _classify_returning("MyTV", recorder=recorder), lobby_store)
    ans = await orch.handle_ask(text="q", thread_ts="ts", channel_id="C123", is_dm=False)
    assert ans.text == "answer to q"
    assert recorder == []                                     # subscription router not engaged
    assert runner.calls[0][1].name == "MyTV"
    lobby_store.close()


async def test_subscription_stale_sticky_reroutes(store, tmp_path):
    # Sticky names a project no longer in this subscription -> must re-route.
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    await lobby_store.put("ts", "Gone")            # not in SUBS_TWO's (MyTV, Secret)
    recorder = []
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("MyTV", recorder=recorder), lobby_store)
    await orch.handle_ask(text="q", thread_ts="ts", channel_id="C900", is_dm=False)
    assert recorder != []                           # classifier WAS called (re-routed)
    assert runner.calls[0][1].name == "MyTV"        # routed to classifier's choice
    assert await lobby_store.get("ts") == "MyTV"    # sticky updated to the new project
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
