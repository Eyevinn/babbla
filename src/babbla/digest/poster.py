from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SlackPoster:
    def __init__(self, client) -> None:
        self._client = client

    async def post(
        self, channel_id: str, text: str, thread_ts: str | None = None, blocks=None
    ) -> str:
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if blocks is not None:
            kwargs["blocks"] = blocks
        resp = await self._client.chat_postMessage(**kwargs)
        return resp["ts"]

    async def open_dm(self, user_id: str) -> str:
        resp = await self._client.conversations_open(users=user_id)
        return resp["channel"]["id"]

    async def upload_file(
        self, channel_id: str, *, filename: str, content,
        title: str | None = None, thread_ts: str | None = None,
    ) -> bool:
        """Upload one artifact. Returns False (logged) on failure — a missing
        files:write scope or upload error must never crash the ask."""
        kwargs = {
            "channel": channel_id,
            "filename": filename,
            "content": content,
            "title": title or filename,
        }
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        try:
            await self._client.files_upload_v2(**kwargs)
            return True
        except Exception:
            logger.exception("artifact upload failed: %s -> %s", filename, channel_id)
            return False
