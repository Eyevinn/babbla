from __future__ import annotations


class SlackPoster:
    def __init__(self, client) -> None:
        self._client = client

    async def post(self, channel_id: str, text: str, thread_ts: str | None = None) -> str:
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        resp = await self._client.chat_postMessage(**kwargs)
        return resp["ts"]
