from __future__ import annotations

import asyncio
import logging
import re

from babbla.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

PLACEHOLDER = "🔎 looking into it…"
ERROR_TEXT = "⚠️ Couldn't answer that right now — please try again shortly."

_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*")


def clean_mention_text(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


async def process_ask(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    is_dm: bool,
    client,
    orchestrator: Orchestrator,
    user_id: str | None = None,
) -> None:
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    try:
        answer = await orchestrator.handle_ask(
            text=text, thread_ts=thread_ts, channel_id=channel, is_dm=is_dm, user_id=user_id
        )
        await client.chat_update(channel=channel, ts=ts, text=answer.text)
    except Exception:  # one failed Ask must never crash the process
        logger.exception("Ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)


async def process_lobby_ask(
    *, text: str, channel: str, thread_ts: str, client, orchestrator: Orchestrator
) -> None:
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    try:
        answer = await orchestrator.handle_lobby_ask(text=text, thread_ts=thread_ts)
        await client.chat_update(channel=channel, ts=ts, text=answer.text)
    except Exception:  # one failed Lobby ask must never crash the process
        logger.exception("Lobby ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)


def _is_lobby(channel: str, lobby_channel_id: str | None) -> bool:
    return lobby_channel_id is not None and channel == lobby_channel_id


def _spawn(coro) -> None:
    """Schedule *coro* as a Task and ensure any escaping exception is logged."""
    task = asyncio.create_task(coro)

    def _log_if_failed(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            logger.exception("dispatched Ask task failed", exc_info=t.exception())

    task.add_done_callback(_log_if_failed)


def register_handlers(app, orchestrator: Orchestrator, lobby_channel_id: str | None = None) -> None:
    @app.event("app_mention")
    async def _on_mention(event, client):
        thread_ts = event.get("thread_ts") or event["ts"]
        text = clean_mention_text(event.get("text", ""))
        channel = event["channel"]
        if _is_lobby(channel, lobby_channel_id):
            _spawn(
                process_lobby_ask(
                    text=text, channel=channel, thread_ts=thread_ts,
                    client=client, orchestrator=orchestrator,
                )
            )
        else:
            _spawn(
                process_ask(
                    text=text, channel=channel, thread_ts=thread_ts,
                    is_dm=False, client=client, orchestrator=orchestrator,
                )
            )

    @app.event("message")
    async def _on_message(event, client):
        # DM (Private Ask) only; ignore bot echoes and non-DM channel messages.
        if event.get("channel_type") != "im" or event.get("bot_id"):
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        _spawn(
            process_ask(
                text=(event.get("text") or "").strip(),
                channel=event["channel"],
                thread_ts=thread_ts,
                is_dm=True,
                client=client,
                orchestrator=orchestrator,
                user_id=event.get("user"),
            )
        )

    @app.command("/babbla")
    async def _on_command(ack, command, respond):
        await ack()
        try:
            reply = await orchestrator.handle_command(command["user_id"], command.get("text", ""))
        except Exception:
            logger.exception("/babbla command failed for user %s", command.get("user_id"))
            reply = "⚠️ Couldn't update your subscriptions right now — please try again shortly."
        await respond(reply)
