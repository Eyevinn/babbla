from __future__ import annotations


class SlackPoster:
    def __init__(self, client) -> None:
        self._client = client

    async def post(self, channel_id: str, text: str) -> None:
        await self._client.chat_postMessage(channel=channel_id, text=text)
