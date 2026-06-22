from __future__ import annotations

import asyncio

from babbla.access import AccessDecision, Surface, authorize_ask, authorize_personal, is_open_tier
from babbla.membership import deny_membership
from babbla.agent_runner import CitedAnswer
from babbla import lobby, personal, subscriptions
from babbla.config import Config, ProjectBinding


class UnknownSurfaceError(Exception):
    """No project binding matches the Slack surface the question came from."""


class Orchestrator:
    def __init__(
        self, config: Config, runner, store, *,
        catalog=(), classify_fn=None, lobby_store=None,
        personal_store=None, personal_default_cadence: str = "weekly",
        intent_fn=None, membership=deny_membership,
    ) -> None:
        self._config = config
        self._runner = runner
        self._store = store
        self._catalog = catalog
        self._classify_fn = classify_fn
        self._lobby_store = lobby_store
        self._personal_store = personal_store
        self._intent_fn = intent_fn
        self._personal_default_cadence = personal_default_cadence
        self._membership = membership
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, thread_ts: str) -> asyncio.Lock:
        lock = self._locks.get(thread_ts)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_ts] = lock
        return lock

    def _release_lock(self, thread_ts: str) -> None:
        # Drop the lock once the thread is idle so `_locks` can't grow without
        # bound over the life of the process. A still-held lock or one with a
        # queued waiter means another ask in this thread is in flight — keep it.
        lock = self._locks.get(thread_ts)
        if lock is None or lock.locked() or lock._waiters:
            return
        del self._locks[thread_ts]

    def _resolve(self, channel_id: str, is_dm: bool) -> ProjectBinding:
        binding = self._config.for_dm() if is_dm else self._config.for_channel(channel_id)
        if binding is None:
            raise UnknownSurfaceError(
                f"No project bound to {'DM' if is_dm else channel_id}"
            )
        return binding

    async def _authorize_personal(self, user_id: str, binding) -> "AccessDecision":
        # Open-tier short-circuits BEFORE any Slack call.
        if is_open_tier(binding):
            return authorize_personal(binding, is_member=True)
        member = await self._membership(user_id, binding.channel_id)
        return authorize_personal(binding, is_member=member)

    async def _followable_for(self, user_id: str) -> list[str]:
        """Names this user may follow: open-tier always, private only when the
        user is a verified channel member. Delegates to the same
        `_authorize_personal` decision the subscribe/ask paths use, so the
        advertised set always matches what `follow` will accept. Open-tier
        short-circuits call-free; private lookups run concurrently."""
        bindings = self._config.bindings
        decisions = await asyncio.gather(
            *(self._authorize_personal(user_id, b) for b in bindings)
        )
        return [b.name for b, d in zip(bindings, decisions) if d.allowed]

    async def handle_command(self, user_id: str, text: str) -> str:
        return await self._dispatch_command(user_id, personal.parse_command(text))

    async def _dispatch_command(self, user_id: str, cmd: personal.Command) -> str:
        if cmd.verb == "help":
            return personal.render_help()
        if cmd.verb == "list":
            names = await self._personal_store.list_for(user_id)
            cadence = await self._personal_store.get_cadence(user_id) or self._personal_default_cadence
            return personal.render_list(names, cadence)
        if cmd.verb == "digest":
            await self._personal_store.set_cadence(user_id, cmd.arg)
            return personal.render_digest_set(cmd.arg)
        if cmd.verb == "topic-list":
            topics = await self._personal_store.topics_for(user_id)
            return personal.render_topic_list(topics)
        if cmd.verb in ("topic-add", "topic-remove"):
            binding = next((b for b in self._config.bindings if b.name == cmd.project), None)
            if binding is None:
                return personal.render_unknown_project(
                    await self._followable_for(user_id)
                )
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                return decision.pointer
            if cmd.verb == "topic-remove":
                await self._personal_store.remove_topic(user_id, binding.name, cmd.name)
                return personal.render_topic_removed(binding.name, cmd.name)
            followed = await self._personal_store.list_for(user_id)
            if binding.name not in followed:
                return personal.render_topic_needs_follow(binding.name)
            description = cmd.description or cmd.name
            await self._personal_store.add_topic(user_id, binding.name, cmd.name, description)
            return personal.render_topic_added(binding.name, cmd.name, description)
        if cmd.verb == "subscribe":
            if len(cmd.projects) > 1:
                return await self._subscribe_many(user_id, cmd.projects)
            name = cmd.projects[0] if cmd.projects else cmd.arg
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                # Advertise the projects this user may follow — open-tier always,
                # plus any private project they are a verified channel member of.
                return personal.render_unknown_project(
                    await self._followable_for(user_id)
                )
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                return decision.pointer
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
        # unsubscribe
        if len(cmd.projects) > 1:
            return await self._unsubscribe_many(user_id, cmd.projects)
        name = cmd.projects[0] if cmd.projects else cmd.arg
        await self._personal_store.remove(user_id, name)
        return personal.render_unsubscribed(name)

    async def _subscribe_many(self, user_id: str, names) -> str:
        followed = set(await self._personal_store.list_for(user_id))
        subscribed: list[str] = []
        skipped: list[tuple[str, str]] = []
        for name in names:
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                skipped.append((name, "unknown"))
                continue
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                skipped.append((binding.name, "private"))
                continue
            if binding.name in followed:
                continue                       # already followed — dedupe silently
            await self._personal_store.add(user_id, binding.name)
            followed.add(binding.name)
            subscribed.append(binding.name)
        return personal.render_subscribed_many(subscribed, skipped)

    async def _unsubscribe_many(self, user_id: str, names) -> str:
        followed = set(await self._personal_store.list_for(user_id))
        removed: list[str] = []
        skipped: list[tuple[str, str]] = []
        for name in names:
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                skipped.append((name, "unknown"))
                continue
            if binding.name not in followed:
                skipped.append((binding.name, "not following"))
                continue
            await self._personal_store.remove(user_id, binding.name)
            followed.discard(binding.name)
            removed.append(binding.name)
        return personal.render_unsubscribed_many(removed, skipped)

    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool,
        user_id: str | None = None,
    ) -> CitedAnswer:
        # DM-only: a free-text subscription-management request ("follow MyTV",
        # "stop sending me X", "make my digest daily") is dispatched as a command
        # and never reaches the read-only Q&A agent.
        if (
            is_dm and user_id is not None
            and self._intent_fn is not None and self._personal_store is not None
        ):
            cmd = await personal.classify_intent(
                text, [b.name for b in self._config.bindings], self._intent_fn
            )
            if cmd is not None:
                reply = await self._dispatch_command(user_id, cmd)
                return CitedAnswer(text=reply, session_id=None)
        if is_dm and self._personal_store is not None and user_id is not None:
            names = await self._personal_store.list_for(user_id)
            if not names:
                # Onboarding gate: an unsubscribed DM user is redirected to follow
                # a project first — no agent run, no default-binding Q&A.
                followable = await self._followable_for(user_id)
                return CitedAnswer(text=personal.render_no_subscriptions(followable), session_id=None)
            if self._catalog:
                entries = subscriptions.entries_for(self._catalog, names)
                if entries:
                    return await self._handle_personal_ask(
                        text=text, thread_ts=thread_ts, entries=entries, user_id=user_id
                    )
        binding = self._resolve(channel_id, is_dm)
        surface = Surface.DM if is_dm else Surface.CHANNEL
        decision = authorize_ask(binding, surface)
        if not decision.allowed:
            # Pre-flight deny: no model call, no session write.
            return CitedAnswer(text=decision.pointer, session_id=None)
        try:
            async with self._lock_for(thread_ts):
                resume_session_id = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, binding, resume_session_id, scratch_key=thread_ts)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
            return answer
        finally:
            self._release_lock(thread_ts)

    async def _resolve_subscription(self, text: str, thread_ts: str, entries):
        if len(entries) == 1:
            return entries[0]                       # deterministic: no classifier call
        sticky = await self._lobby_store.get(thread_ts)
        if sticky is not None:
            for entry in entries:
                if entry.binding.name == sticky:
                    return entry
            # sticky project no longer in this subscription → re-route
        return await lobby.route(text, entries, self._classify_fn)

    async def _handle_personal_ask(self, *, text: str, thread_ts: str, entries, user_id: str) -> CitedAnswer:
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_subscription(text, thread_ts, entries)
                if entry is None:
                    return CitedAnswer(
                        text=subscriptions.subscription_clarify(entries), session_id=None
                    )
                decision = await self._authorize_personal(user_id, entry.binding)
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
                await self._lobby_store.put(thread_ts, entry.binding.name)
                resume = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, entry.binding, resume, scratch_key=thread_ts)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return answer                                          # no pointer suffix — already in a DM
            finally:
                self._release_lock(thread_ts)

    async def _resolve_lobby(self, text: str, thread_ts: str):
        sticky = await self._lobby_store.get(thread_ts)
        if sticky is not None:
            for entry in self._catalog:
                if entry.binding.name == sticky:
                    return entry
            # sticky project no longer in the catalog → re-route
        return await lobby.route(text, self._catalog, self._classify_fn)

    async def handle_lobby_ask(self, *, text: str, thread_ts: str) -> CitedAnswer:
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_lobby(text, thread_ts)
                if entry is None:
                    return CitedAnswer(text=lobby.discovery_reply(self._catalog), session_id=None)
                decision = authorize_ask(entry.binding, Surface.LOBBY)
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
                await self._lobby_store.put(thread_ts, entry.binding.name)
                resume = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, entry.binding, resume, scratch_key=thread_ts)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return CitedAnswer(
                    text=answer.text + lobby.pointer_suffix(entry),
                    session_id=answer.session_id,
                    artifacts=answer.artifacts,
                )
            finally:
                self._release_lock(thread_ts)
