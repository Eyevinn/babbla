from __future__ import annotations

import logging
from datetime import datetime, timedelta

from babbla.digest.anchors import changes_between, changes_since, current_head, head_for
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


class SharedDigestAction:
    def __init__(self, subscription, by_name, store, get_json, runner, poster) -> None:
        self._sub = subscription
        self._by_name = by_name
        self._store = store
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self.label = f"shared-digest:{subscription.channel_id}"

    async def maybe_run(self, now: datetime) -> None:
        sub = self._sub
        d = sub.digest
        state = await self._store.get(sub.channel_id)
        if not is_due(now, state.last_digest_at, d.cadence, d.tz):
            return
        heads: dict[str, str] = {}
        per_project_changes: dict[str, list] = {}
        for name in sub.project_names:
            b = self._by_name.get(name)
            if b is None:
                logger.warning("shared digest %s: no binding for project %r", sub.channel_id, name)
                continue
            anchor = b.digest.anchor if b.digest else "branch"
            deploy_workflow = b.digest.deploy_workflow if b.digest else None
            head = head_for(b.owner, b.repo, anchor, deploy_workflow, get_json=self._get_json)
            if head is None:
                continue  # no ship signal — do not advance this project
            heads[name] = head
            wm = state.watermarks.get(name)
            if wm is None:
                if anchor == "branch":
                    cutoff = (now - _PERIOD[d.cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                    changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
                else:
                    changes = []
            elif head == wm:
                changes = []
            else:
                changes = changes_between(b.owner, b.repo, wm, head, get_json=self._get_json)
            if changes:
                per_project_changes[name] = changes
        if not per_project_changes:
            return  # all quiet: no post, no advance
        context_binding = self._by_name[next(iter(per_project_changes))]
        text = await self._runner.summarize_shared(context_binding, per_project_changes)
        await self._poster.post(sub.channel_id, text)
        await self._store.advance(sub.channel_id, heads, now.timestamp())
