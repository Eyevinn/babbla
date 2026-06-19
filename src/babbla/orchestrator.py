from __future__ import annotations

import asyncio

from babbla.access import Surface, authorize_ask, is_open_tier
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
    ) -> None:
        self._config = config
        self._runner = runner
        self._store = store
        self._catalog = catalog
        self._classify_fn = classify_fn
        self._lobby_store = lobby_store
        self._personal_store = personal_store
        self._personal_default_cadence = personal_default_cadence
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

    async def handle_command(self, user_id: str, text: str) -> str:
        cmd = personal.parse_command(text)
        if cmd.verb == "help":
            return personal.render_help()
        if cmd.verb == "list":
            names = await self._personal_store.list_for(user_id)
            cadence = await self._personal_store.get_cadence(user_id) or self._personal_default_cadence
            return personal.render_list(names, cadence)
        if cmd.verb == "digest":
            await self._personal_store.set_cadence(user_id, cmd.arg)
            return personal.render_digest_set(cmd.arg)
        if cmd.verb == "subscribe":
            binding = next((b for b in self._config.bindings if b.name == cmd.arg), None)
            if binding is None:
                return personal.render_unknown_project([b.name for b in self._config.bindings])
            if not is_open_tier(binding):
                return personal.render_private_refused(binding.name)
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
        # unsubscribe
        await self._personal_store.remove(user_id, cmd.arg)
        return personal.render_unsubscribed(cmd.arg)

    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool
    ) -> CitedAnswer:
        if not is_dm:
            sub = self._config.subscription_for(channel_id)
            if sub is not None:
                return await self._handle_subscription_ask(
                    text=text, thread_ts=thread_ts, sub=sub
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
                answer = await self._runner.run_ask(text, binding, resume_session_id)
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

    async def _handle_subscription_ask(self, *, text: str, thread_ts: str, sub) -> CitedAnswer:
        entries = subscriptions.entries_for(self._catalog, sub.project_names)
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_subscription(text, thread_ts, entries)
                if entry is None:
                    return CitedAnswer(
                        text=subscriptions.subscription_clarify(entries), session_id=None
                    )
                decision = authorize_ask(entry.binding, Surface.CHANNEL)  # channel = access
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
                await self._lobby_store.put(thread_ts, entry.binding.name)
                resume = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, entry.binding, resume)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return answer       # no pointer suffix — the asker is already home
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
                answer = await self._runner.run_ask(text, entry.binding, resume)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return CitedAnswer(
                    text=answer.text + lobby.pointer_suffix(entry),
                    session_id=answer.session_id,
                )
            finally:
                self._release_lock(thread_ts)
