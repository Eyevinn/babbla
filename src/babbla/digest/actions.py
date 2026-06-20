from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from babbla.access import is_open_tier
from babbla.blocks import delete_button_blocks
from babbla.digest.anchors import changes_between, changes_since, current_head, head_for
from babbla.digest.cadence import is_due
from babbla.digest.pulls import stale_prs
from babbla.digest.adr import changed_adrs

logger = logging.getLogger(__name__)

_PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class PerProjectDigestAction:
    def __init__(self, binding, store, get_json, runner, poster) -> None:
        self._b = binding
        self._store = store
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self.project = binding.name
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
                await self._poster.post(
                    self._b.channel_id, text, blocks=delete_button_blocks(text)
                )
        await self._store.advance(self._b.channel_id, head, now.timestamp())


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
        slugs = {
            n: f"{self._by_name[n].owner}/{self._by_name[n].repo}"
            for n in per_project_changes if n in self._by_name
        }
        all_topics = await self._subs.topics_for(user_id)
        topics_by_project = {
            n: all_topics[n] for n in per_project_changes if n in all_topics
        }
        text = await self._runner.summarize_shared(
            context_binding, per_project_changes, slugs=slugs, topics_by_project=topics_by_project
        )
        if text.strip():
            dm_channel = await self._poster.open_dm(user_id)
            await self._poster.post(
                dm_channel, text, blocks=delete_button_blocks(text, owner_id=user_id)
            )
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
        self.project = binding.name
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


class StalePRAction:
    _MAX = 20

    def __init__(self, binding, timer, get_json, poster, cadence: str, tz: str,
                 threshold_days: int, include_drafts: bool) -> None:
        self._b = binding
        self._timer = timer
        self._get_json = get_json
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._threshold_days = threshold_days
        self._include_drafts = include_drafts
        self._key = f"stale-pr:{binding.name}"
        self.project = binding.name
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        prs = stale_prs(
            self._b.owner, self._b.repo, now=now,
            threshold_days=self._threshold_days,
            include_drafts=self._include_drafts, get_json=self._get_json,
        )
        if prs:
            await self._poster.post(self._b.channel_id, self._render(prs))
        # Always advance: one check per cadence bucket, never per-tick. No watermark —
        # staleness is recomputed each period from live updated_at.
        await self._timer.advance(self._key, now.timestamp())

    def _render(self, prs) -> str:
        lines = [f"🧹 *{self._b.repo} — {len(prs)} open PRs idle ≥ {self._threshold_days}d*"]
        for pr in prs[: self._MAX]:
            lines.append(
                f"• <{pr.url}|#{pr.number}> *{pr.title}* — idle {pr.idle_days}d, @{pr.author}"
            )
        if len(prs) > self._MAX:
            lines.append(f"…and {len(prs) - self._MAX} more")
        return "\n".join(lines)


class AdrDigestAction:
    def __init__(self, binding, timer, get_json, runner, poster,
                 cadence: str, tz: str, dir: str) -> None:
        self._b = binding
        self._timer = timer
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._dir = dir
        self._key = f"adr:{binding.name}"
        self.project = binding.name
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        since = None if last is None else datetime.fromtimestamp(last, tz=timezone.utc)
        paths = changed_adrs(
            self._b.owner, self._b.repo, self._dir, since=since, get_json=self._get_json
        )
        if not paths:
            # Nothing changed (or no ADRs): stay quiet, advance once per period.
            await self._timer.advance(self._key, now.timestamp())
            return
        # digest failure raises here -> scheduler catches it -> timer NOT advanced
        # -> retries the same window next bucket.
        text = await self._runner.digest(self._b, paths)
        slug = f"{self._b.owner}/{self._b.repo}"
        lead = f"Here's a {self._cadence} architecture decision record on *{slug}*"
        await self._poster.post(self._b.channel_id, f"{lead}\n\n{text}")
        await self._timer.advance(self._key, now.timestamp())
