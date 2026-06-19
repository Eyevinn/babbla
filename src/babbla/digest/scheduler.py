from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)


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
