from __future__ import annotations

import asyncio
import logging
import re

from slack_sdk.errors import SlackApiError

from babbla.blocks import DELETE_ACTION_ID, delete_button_blocks, notification_text
from babbla.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

PLACEHOLDER = "🔎 looking into it…"
ERROR_TEXT = "⚠️ Couldn't answer that right now — please try again shortly."
NOT_OWNER_TEXT = "Only the person who asked can delete this message."

_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*")


def clean_mention_text(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _deleted_parent_ts(event: dict) -> str | None:
    """The ts of a question that was removed, or None if this isn't a removal.

    Slack signals a removal two ways: a hard delete (`message_deleted`), and —
    when the message already had thread replies — a `message_changed` carrying a
    `tombstone` (the "This message was deleted" placeholder). The orphan case is
    *exactly* the latter, since the reply is what makes Babbla's answer dangle.
    """
    subtype = event.get("subtype")
    if subtype == "message_deleted":
        return event.get("deleted_ts") or (event.get("previous_message") or {}).get("ts")
    if subtype == "message_changed":
        msg = event.get("message") or {}
        if msg.get("subtype") == "tombstone":
            return msg.get("ts") or (event.get("previous_message") or {}).get("ts")
    return None


def _delete_target(body: dict) -> tuple[str | None, str | None]:
    """Pull (channel_id, message_ts) of the clicked message from an action payload,
    tolerating both the top-level and container-nested shapes Slack may send."""
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts")
    return channel, ts


def _delete_owner(body: dict) -> str:
    """The owner id from the button value (part before ':', or the whole value).

    Button values are either "owner_id" (plain delete) or "owner_id:file_ts"
    (delete + file message). Both shapes use ':' as a separator.
    """
    actions = body.get("actions") or [{}]
    value = actions[0].get("value") or ""
    return value.split(":", 1)[0]


def _delete_file_id(body: dict) -> str | None:
    """The file ID encoded after ':' in the button value, or None."""
    actions = body.get("actions") or [{}]
    value = actions[0].get("value") or ""
    parts = value.split(":", 1)
    return parts[1] if len(parts) > 1 and parts[1] else None


async def _upload_artifacts(client, *, channel: str, thread_ts: str, artifacts, user_id: str = "") -> list[str]:
    """Upload artifacts as Slack files. Returns the Slack file ID of each uploaded file."""
    file_ids: list[str] = []
    if not artifacts:
        return file_ids
    for art in artifacts:
        try:
            content = art.data.decode("utf-8", errors="replace") if isinstance(art.data, bytes) else (art.data or "")
            resp = await client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                filename=art.filename,
                content=content,
                title=art.filename,
            )
            resp_files = (resp or {}).get("files")
            resp_file = (resp or {}).get("file")
            file_id = (resp_files or [{}])[0].get("id") if resp_files else (resp_file or {}).get("id")
            if file_id:
                file_ids.append(file_id)
        except Exception:
            logger.exception("artifact post failed: %s -> %s", art.filename, channel)
    return file_ids


async def process_ask(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    is_dm: bool,
    client,
    orchestrator: Orchestrator,
    user_id: str | None = None,
    answer_store=None,
) -> None:
    if not text.strip():
        return  # a mention/DM with no question — nothing to answer, post nothing
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    if answer_store is not None:
        # Remember which message answers this question so deleting the question
        # can clean up the otherwise-orphaned reply. The placeholder ts is the
        # answer ts (chat_update edits it in place).
        await answer_store.record(channel, thread_ts, ts)
    try:
        answer = await orchestrator.handle_ask(
            text=text, thread_ts=thread_ts, channel_id=channel, is_dm=is_dm, user_id=user_id
        )
        file_ids = await _upload_artifacts(
            client, channel=channel, thread_ts=thread_ts,
            artifacts=getattr(answer, "artifacts", ()), user_id=user_id or "",
        )
        if answer_store is not None:
            for fid in file_ids:
                await answer_store.record(channel, thread_ts, fid)
        # Encode the first file ID in the delete button so clicking it also removes
        # the uploaded file. Format: "owner_id:file_id" or plain "owner_id" if no file.
        file_id = file_ids[0] if file_ids else None
        button_value = f"{user_id or ''}:{file_id}" if file_id else (user_id or "")
        await client.chat_update(
            channel=channel, ts=ts, text=notification_text(answer.text),
            blocks=delete_button_blocks(answer.text, owner_id=button_value),
        )
    except Exception:  # one failed Ask must never crash the process
        logger.exception("Ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)


async def process_lobby_ask(
    *, text: str, channel: str, thread_ts: str, client, orchestrator: Orchestrator,
    user_id: str | None = None, answer_store=None,
) -> None:
    if not text.strip():
        return  # a bare mention in the lobby — nothing to route, post nothing
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    if answer_store is not None:
        await answer_store.record(channel, thread_ts, ts)
    try:
        answer = await orchestrator.handle_lobby_ask(text=text, thread_ts=thread_ts)
        file_ids = await _upload_artifacts(
            client, channel=channel, thread_ts=thread_ts,
            artifacts=getattr(answer, "artifacts", ()), user_id=user_id or "",
        )
        if answer_store is not None:
            for fid in file_ids:
                await answer_store.record(channel, thread_ts, fid)
        file_id = file_ids[0] if file_ids else None
        button_value = f"{user_id or ''}:{file_id}" if file_id else (user_id or "")
        await client.chat_update(
            channel=channel, ts=ts, text=notification_text(answer.text),
            blocks=delete_button_blocks(answer.text, owner_id=button_value),
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


def register_handlers(
    app, orchestrator: Orchestrator, lobby_channel_id: str | None = None, answer_store=None,
) -> None:
    @app.event("app_mention")
    async def _on_mention(event, client):
        text = clean_mention_text(event.get("text", ""))
        if not text:
            return  # a bare @mention (e.g. inviting Babbla) is not a question
        thread_ts = event.get("thread_ts") or event["ts"]
        channel = event["channel"]
        if _is_lobby(channel, lobby_channel_id):
            _spawn(
                process_lobby_ask(
                    text=text, channel=channel, thread_ts=thread_ts,
                    client=client, orchestrator=orchestrator, user_id=event.get("user"),
                    answer_store=answer_store,
                )
            )
        else:
            _spawn(
                process_ask(
                    text=text, channel=channel, thread_ts=thread_ts,
                    is_dm=False, client=client, orchestrator=orchestrator,
                    user_id=event.get("user"), answer_store=answer_store,
                )
            )

    async def _cleanup_orphan(channel, parent_ts, client):
        # Delete the bot reply a now-removed question orphaned. pop() returns ()
        # for anything we didn't answer, so this is a safe no-op on unrelated
        # deletions — including the deletion events our own chat.delete emits.
        if answer_store is None or not (channel and parent_ts):
            return
        for entry in await answer_store.pop(channel, parent_ts):
            try:
                if entry.startswith("F"):
                    # Stored entry is a Slack file ID — delete the file itself.
                    await client.files_delete(file=entry)
                else:
                    await client.chat_delete(channel=channel, ts=entry)
            except SlackApiError as e:
                err = (e.response or {}).get("error")
                if err in ("message_not_found", "file_not_found", "file_deleted"):
                    continue  # already gone — best-effort cleanup, nothing to do
                logger.exception("orphan cleanup failed for %s/%s", channel, entry)
            except Exception:
                logger.exception("orphan cleanup failed for %s/%s", channel, entry)

    @app.event("message")
    async def _on_message(event, client):
        # A removed question gets its orphaned answer cleaned up (any channel/DM).
        # Two shapes: a hard delete (message_deleted), or — when the question
        # already had a reply — Slack keeps it as a "tombstone" and sends a
        # message_changed instead of message_deleted. Handle both.
        parent_ts = _deleted_parent_ts(event)
        if parent_ts is not None:
            await _cleanup_orphan(event.get("channel"), parent_ts, client)
            return
        # Otherwise: DM (Private Ask) only. Ignore bot echoes, non-DM channels, and
        # any other subtype — edits (message_changed), joins, etc. are not new
        # questions and must never trigger an Ask.
        if (
            event.get("channel_type") != "im"
            or event.get("bot_id")
            or event.get("subtype")
        ):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return  # empty DM — nothing to answer
        thread_ts = event.get("thread_ts") or event["ts"]
        _spawn(
            process_ask(
                text=text,
                channel=event["channel"],
                thread_ts=thread_ts,
                is_dm=True,
                client=client,
                orchestrator=orchestrator,
                user_id=event.get("user"),
                answer_store=answer_store,
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
        # Delete any uploaded file whose ID is encoded in the button value.
        file_id = _delete_file_id(body)
        if file_id:
            try:
                await client.files_delete(file=file_id)
            except SlackApiError as e:
                err = (e.response or {}).get("error")
                if err not in ("file_not_found", "file_deleted"):
                    logger.exception("file delete failed for %s", file_id)
            except Exception:
                logger.exception("file delete failed for %s", file_id)
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
