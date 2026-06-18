from __future__ import annotations

import asyncio

from babbla.agent_runner import CitedAnswer
from babbla.config import Config, ProjectBinding


class UnknownSurfaceError(Exception):
    """No project binding matches the Slack surface the question came from."""


class Orchestrator:
    def __init__(self, config: Config, runner, store) -> None:
        self._config = config
        self._runner = runner
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, thread_ts: str) -> asyncio.Lock:
        lock = self._locks.get(thread_ts)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_ts] = lock
        return lock

    def _resolve(self, channel_id: str, is_dm: bool) -> ProjectBinding:
        binding = self._config.for_dm() if is_dm else self._config.for_channel(channel_id)
        if binding is None:
            raise UnknownSurfaceError(
                f"No project bound to {'DM' if is_dm else channel_id}"
            )
        return binding

    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool
    ) -> CitedAnswer:
        binding = self._resolve(channel_id, is_dm)
        async with self._lock_for(thread_ts):
            resume_session_id = await self._store.get_session(thread_ts)
            answer = await self._runner.run_ask(text, binding, resume_session_id)
            if answer.session_id:
                await self._store.put_session(thread_ts, answer.session_id)
            return answer
