import asyncio

import pytest

from babbla.agent_runner import CitedAnswer
from babbla.slack_adapter import (
    ERROR_TEXT,
    PLACEHOLDER,
    _is_lobby,
    clean_mention_text,
    process_ask,
    process_lobby_ask,
    register_handlers,
)


class FakeClient:
    def __init__(self):
        self.posted = None
        self.updates = []

    async def chat_postMessage(self, *, channel, thread_ts, text):
        self.posted = {"channel": channel, "thread_ts": thread_ts, "text": text}
        return {"ts": "msg-1"}

    async def chat_update(self, *, channel, ts, text):
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ok": True}


class FakeOrch:
    def __init__(self, answer=None, exc=None):
        self.answer = answer
        self.exc = exc
        self.calls = []

    async def handle_ask(self, *, text, thread_ts, channel_id, is_dm):
        self.calls.append({"text": text, "thread_ts": thread_ts, "channel_id": channel_id, "is_dm": is_dm})
        if self.exc:
            raise self.exc
        return self.answer


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

    class DualOrch:
        def __init__(self):
            self.lobby_calls = []
            self.ask_calls = []

        async def handle_lobby_ask(self, *, text, thread_ts):
            self.lobby_calls.append(text)
            return CitedAnswer(text="lobby", session_id=None)

        async def handle_ask(self, *, text, thread_ts, channel_id, is_dm):
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
