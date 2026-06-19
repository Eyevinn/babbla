from __future__ import annotations

import asyncio
import logging
import re

from babbla.blocks import DELETE_ACTION_ID, delete_button_blocks
from babbla.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

PLACEHOLDER = "🔎 looking into it…"
ERROR_TEXT = "⚠️ Couldn't answer that right now — please try again shortly."
NOT_OWNER_TEXT = "Only the person who asked can delete this message."

_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*")


def clean_mention_text(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _delete_target(body: dict) -> tuple[str | None, str | None]:
    """Pull (channel_id, message_ts) of the clicked message from an action payload,
    tolerating both the top-level and container-nested shapes Slack may send."""
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts")
    return channel, ts


def _delete_owner(body: dict) -> str:
    """The owner id the button carries in its value ("" = anyone may delete)."""
    actions = body.get("actions") or [{}]
    return actions[0].get("value") or ""


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
        await client.chat_update(
            channel=channel, ts=ts, text=answer.text,
            blocks=delete_button_blocks(answer.text, owner_id=user_id or ""),
        )
    except Exception:  # one failed Ask must never crash the process
        logger.exception("Ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)


async def process_lobby_ask(
    *, text: str, channel: str, thread_ts: str, client, orchestrator: Orchestrator,
    user_id: str | None = None,
) -> None:
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    try:
        answer = await orchestrator.handle_lobby_ask(text=text, thread_ts=thread_ts)
        await client.chat_update(
            channel=channel, ts=ts, text=answer.text,
            blocks=delete_button_blocks(answer.text, owner_id=user_id or ""),
        )
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
                    client=client, orchestrator=orchestrator, user_id=event.get("user"),
                )
            )
        else:
            _spawn(
                process_ask(
                    text=text, channel=channel, thread_ts=thread_ts,
                    is_dm=False, client=client, orchestrator=orchestrator,
                    user_id=event.get("user"),
                )
            )

    @app.event("message")
    async def _on_message(event, client):
        # DM (Private Ask) only. Ignore bot echoes, non-DM channels, and any event
        # carrying a subtype — deletions (message_deleted), edits (message_changed),
        # joins, etc. are not new questions and must never trigger an Ask.
        if (
            event.get("channel_type") != "im"
            or event.get("bot_id")
            or event.get("subtype")
        ):
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

    @app.action(DELETE_ACTION_ID)
    async def _on_delete(ack, body, client):
        await ack()
        channel, ts = _delete_target(body)
        if not (channel and ts):
            return
        owner = _delete_owner(body)
        clicker = (body.get("user") or {}).get("id")
        if owner and owner != clicker:
            # Restricted to the original asker; tell the clicker privately, delete nothing.
            try:
                await client.chat_postEphemeral(channel=channel, user=clicker, text=NOT_OWNER_TEXT)
            except Exception:
                logger.exception("ephemeral deny failed for %s/%s", channel, ts)
            return
        try:
            await client.chat_delete(channel=channel, ts=ts)
        except Exception:
            logger.exception("delete button failed for %s/%s", channel, ts)

    @app.command("/babbla")
    async def _on_command(ack, command, respond):
        await ack()
        try:
            reply = await orchestrator.handle_command(command["user_id"], command.get("text", ""))
        except Exception:
            logger.exception("/babbla command failed for user %s", command.get("user_id"))
            reply = "⚠️ Couldn't update your subscriptions right now — please try again shortly."
        await respond(reply)
