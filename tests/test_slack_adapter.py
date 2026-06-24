import asyncio

import pytest

from babbla.agent_runner import CitedAnswer
from babbla.blocks import DELETE_ACTION_ID
from babbla.slack_adapter import (
    ERROR_TEXT,
    PLACEHOLDER,
    _delete_file_id,
    _delete_owner,
    _delete_target,
    _is_lobby,
    clean_mention_text,
    process_ask,
    process_lobby_ask,
    register_handlers,
)


def _action_ids(blocks):
    return [
        e["action_id"]
        for b in (blocks or [])
        if b.get("type") == "actions"
        for e in b["elements"]
    ]


class FakeClient:
    def __init__(self):
        self.posted = None
        self.updates = []
        self.deleted = []
        self.ephemeral = None

    async def chat_postMessage(self, *, channel, thread_ts, text, blocks=None):
        self.posted = {"channel": channel, "thread_ts": thread_ts, "text": text, "blocks": blocks}
        return {"ts": "msg-1"}

    async def chat_update(self, *, channel, ts, text, blocks=None):
        self.updates.append({"channel": channel, "ts": ts, "text": text, "blocks": blocks})
        return {"ok": True}

    async def chat_delete(self, *, channel, ts):
        self.deleted.append({"channel": channel, "ts": ts})
        return {"ok": True}

    async def chat_postEphemeral(self, *, channel, user, text):
        self.ephemeral = {"channel": channel, "user": user, "text": text}
        return {"ok": True}

    async def files_upload_v2(self, **kwargs):
        return {"files": []}

    async def files_delete(self, **kwargs):
        self.deleted.append({"file": kwargs.get("file")})
        return {"ok": True}


class FakeOrch:
    def __init__(self, answer=None, exc=None):
        self.answer = answer
        self.exc = exc
        self.calls = []
        self.command_calls = []

    async def handle_ask(self, *, text, thread_ts, channel_id, is_dm, user_id=None):
        self.calls.append({"text": text, "thread_ts": thread_ts, "channel_id": channel_id,
                           "is_dm": is_dm, "user_id": user_id})
        if self.exc:
            raise self.exc
        return self.answer

    async def handle_command(self, user_id, text):
        self.command_calls.append((user_id, text))
        return "command-reply"


def test_clean_mention_text_strips_bot():
    assert clean_mention_text("<@U123> why did we move branding?") == "why did we move branding?"
    assert clean_mention_text("no mention here") == "no mention here"


async def test_process_ask_posts_placeholder_then_answer():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="Because PR #58", session_id="s1"))
    await process_ask(
        text="why?", channel="C123", thread_ts="t1", is_dm=False, client=client, orchestrator=orch
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.posted["thread_ts"] == "t1"
    assert client.updates[-1]["text"] == "Because PR #58"
    assert client.updates[-1]["ts"] == "msg-1"
    assert orch.calls[0]["channel_id"] == "C123"
    assert orch.calls[0]["is_dm"] is False


async def test_process_ask_edits_to_error_on_failure():
    client = FakeClient()
    orch = FakeOrch(exc=RuntimeError("github down"))
    await process_ask(
        text="why?", channel="C123", thread_ts="t1", is_dm=False, client=client, orchestrator=orch
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.updates[-1]["text"] == ERROR_TEXT  # no dangling placeholder


async def test_process_ask_long_answer_fits_slack_limits():
    # Regression: a long-form answer (e.g. an onboarding guide) must not exceed
    # Slack's text-field (40k) or block-count (50) caps -> previously msg_too_long.
    client = FakeClient()
    huge = "paragraph of the guide.\n" * 20000   # ~480k chars
    orch = FakeOrch(answer=CitedAnswer(text=huge, session_id="s1"))
    await process_ask(
        text="write an onboarding guide", channel="D9", thread_ts="t9",
        is_dm=True, client=client, orchestrator=orch,
    )
    upd = client.updates[-1]
    assert len(upd["text"]) <= 40000          # text fallback within Slack's cap
    assert len(upd["blocks"]) <= 50           # block count within Slack's cap


async def test_process_ask_passes_is_dm():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    await process_ask(
        text="q", channel="D9", thread_ts="t9", is_dm=True, client=client, orchestrator=orch
    )
    assert orch.calls[0]["is_dm"] is True


# ---------------------------------------------------------------------------
# register_handlers tests
# ---------------------------------------------------------------------------


class FakeApp:
    def __init__(self):
        self.handlers = {}

    def event(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn
        return deco

    def command(self, name):
        def deco(fn):
            self.handlers[("command", name)] = fn
            return fn
        return deco

    def action(self, name):
        def deco(fn):
            self.handlers[("action", name)] = fn
            return fn
        return deco


async def test_mention_handler_invokes_process_ask_not_dm():
    """app_mention handler dispatches process_ask with is_dm=False."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="answer", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "<@UBOT> what is the plan?", "channel": "C1", "ts": "t1"}
    await app.handlers["app_mention"](event=event, client=client)
    await asyncio.sleep(0)  # let the scheduled task run

    assert len(orch.calls) == 1
    assert orch.calls[0]["is_dm"] is False
    assert orch.calls[0]["text"] == "what is the plan?"
    assert orch.calls[0]["channel_id"] == "C1"


async def test_mention_handler_uses_thread_ts_when_present():
    """app_mention handler passes event thread_ts when it exists."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "<@UBOT> question", "channel": "C1", "ts": "t1", "thread_ts": "t0"}
    await app.handlers["app_mention"](event=event, client=client)
    await asyncio.sleep(0)

    assert orch.calls[0]["thread_ts"] == "t0"


async def test_mention_handler_falls_back_to_ts_when_no_thread_ts():
    """app_mention handler uses event ts as thread_ts when thread_ts absent."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "<@UBOT> question", "channel": "C1", "ts": "t99"}
    await app.handlers["app_mention"](event=event, client=client)
    await asyncio.sleep(0)

    assert orch.calls[0]["thread_ts"] == "t99"


async def test_dm_message_handler_invokes_process_ask_with_is_dm():
    """message handler dispatches process_ask with is_dm=True for channel_type=im."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "hello bot", "channel": "D1", "ts": "t2", "channel_type": "im"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)

    assert len(orch.calls) == 1
    assert orch.calls[0]["is_dm"] is True
    assert orch.calls[0]["text"] == "hello bot"
    assert orch.calls[0]["channel_id"] == "D1"


async def test_mention_handler_passes_asker_user_id():
    """Channel @mentions thread the asker's user id through to process_ask."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "<@UBOT> q", "channel": "C1", "ts": "t1", "user": "Uasker"}
    await app.handlers["app_mention"](event=event, client=client)
    await asyncio.sleep(0)

    assert orch.calls[0]["user_id"] == "Uasker"


async def test_dm_message_handler_ignores_non_dm_channel():
    """message handler does NOT invoke process_ask when channel_type != 'im'."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {"text": "hello", "channel": "C2", "ts": "t3", "channel_type": "channel"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)

    assert len(orch.calls) == 0


async def test_dm_message_handler_ignores_deletions_and_edits():
    """A deleted/edited DM arrives as a message event with a subtype — never an Ask."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    deleted = {
        "channel": "D1", "ts": "t5", "channel_type": "im",
        "subtype": "message_deleted",
        "previous_message": {"text": "what I typed", "user": "U7"},
    }
    changed = {
        "channel": "D1", "ts": "t6", "channel_type": "im",
        "subtype": "message_changed",
        "message": {"text": "edited text", "user": "U7"},
    }
    await app.handlers["message"](event=deleted, client=client)
    await app.handlers["message"](event=changed, client=client)
    await asyncio.sleep(0)

    assert len(orch.calls) == 0
    assert client.posted is None  # no placeholder posted either


async def test_dm_message_handler_ignores_bot_echo():
    """message handler does NOT invoke process_ask when bot_id is present."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)

    event = {
        "text": "bot echo",
        "channel": "D1",
        "ts": "t4",
        "channel_type": "im",
        "bot_id": "BABC123",
    }
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)

    assert len(orch.calls) == 0


# ---------------------------------------------------------------------------
# Lobby dispatch tests (Task 6)
# ---------------------------------------------------------------------------


def test_is_lobby():
    assert _is_lobby("C0LOBBY", "C0LOBBY") is True
    assert _is_lobby("C123", "C0LOBBY") is False
    assert _is_lobby("C123", None) is False


async def test_process_lobby_ask_posts_answer():
    client = FakeClient()

    class LobbyOrch:
        async def handle_lobby_ask(self, *, text, thread_ts):
            return CitedAnswer(text="routed answer ↪ <#C123>", session_id="s1")

    await process_lobby_ask(
        text="q", channel="C0LOBBY", thread_ts="t1", client=client, orchestrator=LobbyOrch()
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.updates[-1]["text"] == "routed answer ↪ <#C123>"


async def test_process_lobby_ask_edits_to_error_on_failure():
    client = FakeClient()

    class FailingLobbyOrch:
        async def handle_lobby_ask(self, *, text, thread_ts):
            raise RuntimeError("boom")

    await process_lobby_ask(
        text="q", channel="C0LOBBY", thread_ts="t1", client=client, orchestrator=FailingLobbyOrch()
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.updates[-1]["text"] == ERROR_TEXT


async def test_register_handlers_dispatches_lobby_vs_ask():
    class FakeApp:
        def __init__(self):
            self.handlers = {}

        def event(self, name):
            def deco(fn):
                self.handlers[name] = fn
                return fn
            return deco

        def command(self, name):
            def deco(fn):
                self.handlers[("command", name)] = fn
                return fn
            return deco

        def action(self, name):
            def deco(fn):
                self.handlers[("action", name)] = fn
                return fn
            return deco

    class DualOrch:
        def __init__(self):
            self.lobby_calls = []
            self.ask_calls = []

        async def handle_lobby_ask(self, *, text, thread_ts):
            self.lobby_calls.append(text)
            return CitedAnswer(text="lobby", session_id=None)

        async def handle_ask(self, *, text, thread_ts, channel_id, is_dm, user_id=None):
            self.ask_calls.append((text, channel_id))
            return CitedAnswer(text="ask", session_id=None)

    app, orch, client = FakeApp(), DualOrch(), FakeClient()
    register_handlers(app, orch, lobby_channel_id="C0LOBBY")
    mention = app.handlers["app_mention"]

    await mention({"channel": "C0LOBBY", "ts": "t1", "text": "<@U> hi"}, client)
    await mention({"channel": "C999", "ts": "t2", "text": "<@U> hey"}, client)
    # drain the tasks _spawn scheduled
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending)

    assert orch.lobby_calls == ["hi"]
    assert orch.ask_calls == [("hey", "C999")]


async def test_dm_message_passes_user_id():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)
    event = {"text": "q", "channel": "D1", "ts": "t2", "channel_type": "im", "user": "U7"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert orch.calls[0]["user_id"] == "U7"
    assert orch.calls[0]["is_dm"] is True


def _button_value(blocks):
    for b in blocks or []:
        if b.get("type") == "actions":
            return b["elements"][0]["value"]
    return None


async def test_process_ask_attaches_delete_button_with_asker_as_owner():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="the answer", session_id="s1"))
    await process_ask(
        text="q", channel="D1", thread_ts="t1", is_dm=True,
        client=client, orchestrator=orch, user_id="U7",
    )
    last = client.updates[-1]
    assert last["text"] == "the answer"                 # fallback text preserved
    assert DELETE_ACTION_ID in _action_ids(last["blocks"])
    assert _button_value(last["blocks"]) == "U7"        # owner = asker


async def test_process_lobby_ask_attaches_delete_button_with_asker_as_owner():
    client = FakeClient()

    class LobbyOrch:
        async def handle_lobby_ask(self, *, text, thread_ts):
            return CitedAnswer(text="routed", session_id="s1")

    await process_lobby_ask(
        text="q", channel="C0LOBBY", thread_ts="t1",
        client=client, orchestrator=LobbyOrch(), user_id="U8",
    )
    last = client.updates[-1]
    assert DELETE_ACTION_ID in _action_ids(last["blocks"])
    assert _button_value(last["blocks"]) == "U8"


async def test_process_ask_error_has_no_delete_button():
    client = FakeClient()
    orch = FakeOrch(exc=RuntimeError("boom"))
    await process_ask(
        text="q", channel="D1", thread_ts="t1", is_dm=True, client=client, orchestrator=orch
    )
    assert client.updates[-1]["text"] == ERROR_TEXT
    assert _action_ids(client.updates[-1]["blocks"]) == []


def test_delete_target_reads_both_payload_shapes():
    assert _delete_target({"channel": {"id": "D1"}, "message": {"ts": "m1"}}) == ("D1", "m1")
    assert _delete_target({"container": {"channel_id": "D2", "message_ts": "m2"}}) == ("D2", "m2")
    assert _delete_target({}) == (None, None)


def test_delete_owner_reads_button_value():
    assert _delete_owner({"actions": [{"value": "U7"}]}) == "U7"
    assert _delete_owner({"actions": [{"value": ""}]}) == ""
    assert _delete_owner({}) == ""
    # Composite "owner:file_ts" — only the owner part is returned.
    assert _delete_owner({"actions": [{"value": "U7:1234.567"}]}) == "U7"
    assert _delete_owner({"actions": [{"value": ":1234.567"}]}) == ""


def test_delete_file_id_parses_composite_value():
    assert _delete_file_id({"actions": [{"value": "U7:F123ABC"}]}) == "F123ABC"
    assert _delete_file_id({"actions": [{"value": ":F123ABC"}]}) == "F123ABC"
    # Plain owner-only value → no file id.
    assert _delete_file_id({"actions": [{"value": "U7"}]}) is None
    assert _delete_file_id({"actions": [{"value": ""}]}) is None
    assert _delete_file_id({}) is None


def _delete_handler(app):
    return app.handlers[("action", DELETE_ACTION_ID)]


async def _ack():
    pass


async def test_delete_handler_owner_can_delete():
    app, client, orch = FakeApp(), FakeClient(), FakeOrch()
    register_handlers(app, orch)
    body = {"channel": {"id": "D1"}, "message": {"ts": "m9"},
            "user": {"id": "U7"}, "actions": [{"value": "U7"}]}
    await _delete_handler(app)(ack=_ack, body=body, client=client)
    assert client.deleted == [{"channel": "D1", "ts": "m9"}]


async def test_delete_handler_anyone_can_delete_when_no_owner():
    app, client, orch = FakeApp(), FakeClient(), FakeOrch()
    register_handlers(app, orch)
    body = {"channel": {"id": "C1"}, "message": {"ts": "m9"},
            "user": {"id": "Ustranger"}, "actions": [{"value": ""}]}
    await _delete_handler(app)(ack=_ack, body=body, client=client)
    assert client.deleted == [{"channel": "C1", "ts": "m9"}]


async def test_delete_handler_also_deletes_file():
    app, client, orch = FakeApp(), FakeClient(), FakeOrch()
    register_handlers(app, orch)
    body = {"channel": {"id": "C1"}, "message": {"ts": "summary_ts"},
            "user": {"id": "U7"}, "actions": [{"value": "U7:F_FILE99"}]}
    await _delete_handler(app)(ack=_ack, body=body, client=client)
    # File is deleted via files_delete (not chat_delete).
    file_deleted = [d["file"] for d in client.deleted if "file" in d]
    assert "F_FILE99" in file_deleted
    # Summary message is still deleted via chat_delete.
    msg_deleted = [d["ts"] for d in client.deleted if "ts" in d]
    assert "summary_ts" in msg_deleted


async def test_delete_handler_non_owner_is_refused():
    app, client, orch = FakeApp(), FakeClient(), FakeOrch()
    register_handlers(app, orch)
    body = {"channel": {"id": "C1"}, "message": {"ts": "m9"},
            "user": {"id": "Ustranger"}, "actions": [{"value": "U7"}]}
    await _delete_handler(app)(ack=_ack, body=body, client=client)
    assert client.deleted == []                          # not deleted
    assert client.ephemeral["user"] == "Ustranger"       # told why, privately
    assert "asked" in client.ephemeral["text"].lower()


async def test_babbla_command_acks_and_responds():
    app = FakeApp()
    orch = FakeOrch()
    register_handlers(app, orch)
    acked = []
    responded = []
    async def ack():
        acked.append(True)
    async def respond(text):
        responded.append(text)
    await app.handlers[("command", "/babbla")](
        ack=ack, command={"user_id": "U7", "text": "subscribe MyTV"}, respond=respond
    )
    assert acked == [True]
    assert orch.command_calls == [("U7", "subscribe MyTV")]
    assert responded == ["command-reply"]


# ---------------------------------------------------------------------------
# Empty-question guard — a bare @mention (or empty DM) must not run the agent.
# ---------------------------------------------------------------------------


class FakeAnswerStore:
    def __init__(self, mapping=None):
        self.recorded = []
        self._mapping = dict(mapping or {})

    async def record(self, channel_id, parent_ts, answer_ts):
        self.recorded.append((channel_id, parent_ts, answer_ts))
        self._mapping.setdefault((channel_id, parent_ts), []).append(answer_ts)

    async def pop(self, channel_id, parent_ts):
        return tuple(self._mapping.pop((channel_id, parent_ts), []))


async def test_process_ask_skips_blank_question():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="x", session_id="s1"))
    await process_ask(
        text="   ", channel="C1", thread_ts="t1", is_dm=False, client=client, orchestrator=orch
    )
    assert client.posted is None       # no placeholder
    assert orch.calls == []            # agent never invoked


async def test_mention_handler_ignores_bare_mention():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="x", session_id="s1"))
    register_handlers(app, orch)
    event = {"text": "<@UBOT>", "channel": "C1", "ts": "t1"}   # mention, no question
    await app.handlers["app_mention"](event=event, client=client)
    await asyncio.sleep(0)
    assert orch.calls == []
    assert client.posted is None


async def test_dm_handler_ignores_empty_text():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="x", session_id="s1"))
    register_handlers(app, orch)
    event = {"text": "   ", "channel": "D1", "ts": "t1", "channel_type": "im"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert orch.calls == []
    assert client.posted is None


# ---------------------------------------------------------------------------
# Orphan cleanup — deleting a question deletes the bot's now-orphaned reply.
# ---------------------------------------------------------------------------


async def test_process_ask_records_answer_message():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ans", session_id="s1"))
    store = FakeAnswerStore()
    await process_ask(
        text="why?", channel="C1", thread_ts="t1", is_dm=False,
        client=client, orchestrator=orch, answer_store=store,
    )
    assert store.recorded == [("C1", "t1", "msg-1")]   # placeholder ts = answer ts


async def test_deletion_handler_removes_orphaned_answer():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch()
    store = FakeAnswerStore(mapping={("C1", "q1"): ["ans-1"]})
    register_handlers(app, orch, answer_store=store)
    event = {
        "channel": "C1", "subtype": "message_deleted",
        "deleted_ts": "q1", "previous_message": {"ts": "q1", "user": "U7"},
    }
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert client.deleted == [{"channel": "C1", "ts": "ans-1"}]


async def test_deletion_handler_noop_when_no_mapping():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch()
    store = FakeAnswerStore()                     # nothing recorded
    register_handlers(app, orch, answer_store=store)
    event = {"channel": "C1", "subtype": "message_deleted", "deleted_ts": "q1"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert client.deleted == []                  # nothing to clean, no crash


async def test_deletion_handler_does_not_trigger_ask():
    """A message_deleted event must never be treated as a new question."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch()
    store = FakeAnswerStore(mapping={("D1", "q1"): ["ans-1"]})
    register_handlers(app, orch, answer_store=store)
    event = {
        "channel": "D1", "channel_type": "im", "subtype": "message_deleted",
        "deleted_ts": "q1", "previous_message": {"text": "old", "user": "U7"},
    }
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert orch.calls == []                       # not an ask
    assert client.deleted == [{"channel": "D1", "ts": "ans-1"}]


async def test_tombstone_change_cleans_orphan():
    """Deleting a question that already has a reply yields a message_changed
    tombstone (not message_deleted) — cleanup must still fire."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch()
    store = FakeAnswerStore(mapping={("C1", "q1"): ["ans-1"]})
    register_handlers(app, orch, answer_store=store)
    event = {
        "channel": "C1", "subtype": "message_changed",
        "message": {"subtype": "tombstone", "ts": "q1"},
        "previous_message": {"ts": "q1"},
    }
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert client.deleted == [{"channel": "C1", "ts": "ans-1"}]
    assert orch.calls == []


async def test_normal_edit_does_not_clean_or_ask():
    """A real edit (message_changed without a tombstone) is neither a deletion
    nor a question."""
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch()
    store = FakeAnswerStore(mapping={("C1", "q1"): ["ans-1"]})
    register_handlers(app, orch, answer_store=store)
    event = {
        "channel": "C1", "subtype": "message_changed",
        "message": {"ts": "q1", "text": "edited"},
    }
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert client.deleted == []
    assert orch.calls == []


async def test_cleanup_swallows_message_not_found():
    """A best-effort delete of an already-gone message must not raise or log loud."""
    from slack_sdk.errors import SlackApiError

    class _Client(FakeClient):
        async def chat_delete(self, *, channel, ts):
            raise SlackApiError("gone", {"error": "message_not_found"})

    app = FakeApp()
    client = _Client()
    orch = FakeOrch()
    store = FakeAnswerStore(mapping={("C1", "q1"): ["ans-1"]})
    register_handlers(app, orch, answer_store=store)
    event = {"channel": "C1", "subtype": "message_deleted", "deleted_ts": "q1"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert await store.pop("C1", "q1") == ()   # row was still popped, no crash
