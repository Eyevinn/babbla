from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Protocol

from babbla.digest.anchors import changes_between, changes_since, current_head
from babbla.digest.cadence import is_due

logger = logging.getLogger(__name__)

_PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class DigestScheduler:
    def __init__(self, *, config, store, runner, poster, get_json, now_fn, interval_s: int = 900) -> None:
        self._config = config
        self._store = store
        self._runner = runner
        self._poster = poster
        self._get_json = get_json
        self._now_fn = now_fn
        self._interval_s = interval_s

    async def run(self) -> None:
        while True:
            try:
                await self.tick(self._now_fn())
            except Exception:  # a digest failure must never crash the process
                logger.exception("digest tick failed")
            await asyncio.sleep(self._interval_s)

    async def tick(self, now: datetime) -> None:
        for binding in self._config.digest_bindings():
            try:
                await self._maybe_digest(binding, now)
            except Exception:
                logger.exception("digest failed for %s", binding.name)

    async def _maybe_digest(self, binding, now: datetime) -> None:
        d = binding.digest
        state = await self._store.get(binding.channel_id)
        if not is_due(now, state.last_digest_at, d.cadence, d.tz):
            return
        head = current_head(binding, get_json=self._get_json)
        if head is None:
            return  # no ship signal yet
        if state.watermark_sha is None:
            # First run: branch bootstraps with a window; deploy is silent.
            if d.anchor == "branch":
                cutoff = (now - _PERIOD[d.cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                changes = changes_since(binding.owner, binding.repo, cutoff, get_json=self._get_json)
            else:
                changes = []
            await self._emit(binding, changes, head, now)
            return
        if head == state.watermark_sha:
            return  # due, but nothing new shipped — stay quiet, do not advance
        changes = changes_between(binding.owner, binding.repo, state.watermark_sha, head, get_json=self._get_json)
        await self._emit(binding, changes, head, now)

    async def _emit(self, binding, changes, head: str, now: datetime) -> None:
        if changes:
            text = await self._runner.summarize(binding, changes, head)
            await self._poster.post(binding.channel_id, text)
        await self._store.advance(binding.channel_id, head, now.timestamp())


class Action(Protocol):
    label: str

    async def maybe_run(self, now: datetime) -> None: ...


class ActionScheduler:
    def __init__(self, *, actions: tuple[Action, ...], now_fn, interval_s: int = 900) -> None:
        self._actions = actions
        self._now_fn = now_fn
        self._interval_s = interval_s

    async def run(self) -> None:
        while True:
            try:
                await self.tick(self._now_fn())
            except Exception:  # an action failure must never crash the process
                logger.exception("action tick failed")
            await asyncio.sleep(self._interval_s)

    async def tick(self, now: datetime) -> None:
        for action in self._actions:
            try:
                await action.maybe_run(now)
            except Exception:
                logger.exception("action failed: %s", action.label)
