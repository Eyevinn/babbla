from __future__ import annotations

import logging
from datetime import datetime, timedelta

from babbla.access import is_open_tier
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
            text = await self._runner.summarize(self._b, changes, head, topic=self._b.digest.topic)
            if text.strip():
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
        text = await self._runner.summarize_shared(
            context_binding, per_project_changes, topic=self._sub.digest.topic
        )
        if text.strip():
            await self._poster.post(sub.channel_id, text)
        await self._store.advance(sub.channel_id, heads, now.timestamp())


class PersonalDigestAction:
    def __init__(self, personal_store, state_store, by_name, get_json, runner, poster,
                 default_cadence: str, tz: str) -> None:
        self._subs = personal_store
        self._state = state_store
        self._by_name = by_name
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self._default_cadence = default_cadence
        self._tz = tz
        self.label = "personal-digest"

    async def maybe_run(self, now: datetime) -> None:
        for user_id in await self._subs.all_user_ids():
            try:
                await self._maybe_run_user(user_id, now)
            except Exception:  # one user's failure must not abort the rest
                logger.exception("personal digest failed for user %s", user_id)

    async def _maybe_run_user(self, user_id: str, now: datetime) -> None:
        cadence = await self._subs.get_cadence(user_id) or self._default_cadence
        if cadence == "off":
            return
        state = await self._state.get(user_id)
        if not is_due(now, state.last_digest_at, cadence, self._tz):
            return
        names = await self._subs.list_for(user_id)
        bindings = [
            self._by_name[n] for n in names
            if n in self._by_name and is_open_tier(self._by_name[n])
        ]
        heads: dict[str, str] = {}
        per_project_changes: dict[str, list] = {}
        for b in bindings:
            anchor = b.digest.anchor if b.digest else "branch"
            deploy_workflow = b.digest.deploy_workflow if b.digest else None
            head = head_for(b.owner, b.repo, anchor, deploy_workflow, get_json=self._get_json)
            if head is None:
                continue
            heads[b.name] = head
            wm = state.watermarks.get(b.name)
            if wm is None:
                if anchor == "branch":
                    cutoff = (now - _PERIOD[cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                    changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
                else:
                    changes = []
            elif head == wm:
                changes = []
            else:
                changes = changes_between(b.owner, b.repo, wm, head, get_json=self._get_json)
            if changes:
                per_project_changes[b.name] = changes
        if not per_project_changes:
            return
        context_binding = self._by_name[next(iter(per_project_changes))]
        text = await self._runner.summarize_shared(context_binding, per_project_changes)
        dm_channel = await self._poster.open_dm(user_id)
        await self._poster.post(dm_channel, text)
        await self._state.advance(user_id, heads, now.timestamp())


class QuizAction:
    def __init__(self, binding, timer, runner, poster, cadence: str, tz: str, count: int) -> None:
        self._b = binding
        self._timer = timer
        self._runner = runner
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._count = count
        self._key = f"quiz:{binding.name}"
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        text = await self._runner.generate(self._b, self._count)
        questions, _, answers = text.partition("===ANSWERS===")
        ts = await self._poster.post(self._b.channel_id, questions.strip())
        if answers.strip():
            await self._poster.post(self._b.channel_id, answers.strip(), thread_ts=ts)
        await self._timer.advance(self._key, now.timestamp())
