from __future__ import annotations

import logging
from datetime import datetime, timedelta

from babbla.digest.anchors import changes_between, changes_since, current_head
from babbla.digest.cadence import is_due

logger = logging.getLogger(__name__)

_PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class PerProjectDigestAction:
    def __init__(self, binding, store, get_json, runner, poster) -> None:
        self._b = binding
        self._store = store
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self.label = f"digest:{binding.name}"

    async def maybe_run(self, now: datetime) -> None:
        b = self._b
        d = b.digest
        state = await self._store.get(b.channel_id)
        if not is_due(now, state.last_digest_at, d.cadence, d.tz):
            return
        head = current_head(b, get_json=self._get_json)
        if head is None:
            return  # no ship signal yet
        if state.watermark_sha is None:
            if d.anchor == "branch":
                cutoff = (now - _PERIOD[d.cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
            else:
                changes = []
            await self._emit(changes, head, now)
            return
        if head == state.watermark_sha:
            return  # due, but nothing new shipped — stay quiet, do not advance
        changes = changes_between(b.owner, b.repo, state.watermark_sha, head, get_json=self._get_json)
        await self._emit(changes, head, now)

    async def _emit(self, changes, head: str, now: datetime) -> None:
        if changes:
            text = await self._runner.summarize(self._b, changes, head)
            await self._poster.post(self._b.channel_id, text)
        await self._store.advance(self._b.channel_id, head, now.timestamp())
