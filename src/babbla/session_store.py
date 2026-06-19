from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_ts  TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL
)
"""


class SessionStore:
    def __init__(
        self,
        db_path: str,
        ttl_seconds: int = 86400,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._now = time_fn
        # check_same_thread=False: we serialize access through asyncio.to_thread.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    async def get_session(self, thread_ts: str) -> str | None:
        return await asyncio.to_thread(self._get_session_sync, thread_ts)

    def _get_session_sync(self, thread_ts: str) -> str | None:
        row = self._conn.execute(
            "SELECT session_id, updated_at FROM sessions WHERE thread_ts = ?",
            (thread_ts,),
        ).fetchone()
        if row is None:
            return None
        session_id, updated_at = row
        if self._now() - updated_at > self._ttl:
            self._conn.execute("DELETE FROM sessions WHERE thread_ts = ?", (thread_ts,))
            self._conn.commit()
            return None
        return session_id

    async def put_session(self, thread_ts: str, session_id: str) -> None:
        await asyncio.to_thread(self._put_session_sync, thread_ts, session_id)

    def _put_session_sync(self, thread_ts: str, session_id: str) -> None:
        self._conn.execute(
            "INSERT INTO sessions (thread_ts, session_id, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_ts) DO UPDATE SET session_id = excluded.session_id, "
            "updated_at = excluded.updated_at",
            (thread_ts, session_id, self._now()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_state (
    channel_id     TEXT PRIMARY KEY,
    watermark_sha  TEXT,
    last_digest_at REAL
)
"""


@dataclass(frozen=True)
class DigestState:
    watermark_sha: str | None
    last_digest_at: float | None


class DigestStateStore:
    def __init__(self, db_path: str, time_fn: Callable[[], float] = time.time) -> None:
        self._now = time_fn
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_DIGEST_SCHEMA)
        self._conn.commit()

    async def get(self, channel_id: str) -> DigestState:
        return await asyncio.to_thread(self._get_sync, channel_id)

    def _get_sync(self, channel_id: str) -> DigestState:
        row = self._conn.execute(
            "SELECT watermark_sha, last_digest_at FROM digest_state WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return DigestState(None, None)
        return DigestState(watermark_sha=row[0], last_digest_at=row[1])

    async def advance(self, channel_id: str, watermark_sha: str, last_digest_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, channel_id, watermark_sha, last_digest_at)

    def _advance_sync(self, channel_id: str, watermark_sha: str, last_digest_at: float) -> None:
        self._conn.execute(
            "INSERT INTO digest_state (channel_id, watermark_sha, last_digest_at) VALUES (?, ?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET watermark_sha = excluded.watermark_sha, "
            "last_digest_at = excluded.last_digest_at",
            (channel_id, watermark_sha, last_digest_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_LOBBY_SCHEMA = """
CREATE TABLE IF NOT EXISTS lobby_threads (
    thread_ts    TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    updated_at   REAL NOT NULL
)
"""


class LobbyThreadStore:
    """Remembers which project a Lobby thread was routed to, so follow-ups stay sticky."""

    def __init__(
        self,
        db_path: str,
        ttl_seconds: int = 86400,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._now = time_fn
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_LOBBY_SCHEMA)
        self._conn.commit()

    async def get(self, thread_ts: str) -> str | None:
        return await asyncio.to_thread(self._get_sync, thread_ts)

    def _get_sync(self, thread_ts: str) -> str | None:
        row = self._conn.execute(
            "SELECT project_name, updated_at FROM lobby_threads WHERE thread_ts = ?",
            (thread_ts,),
        ).fetchone()
        if row is None:
            return None
        project_name, updated_at = row
        if self._now() - updated_at > self._ttl:
            self._conn.execute("DELETE FROM lobby_threads WHERE thread_ts = ?", (thread_ts,))
            self._conn.commit()
            return None
        return project_name

    async def put(self, thread_ts: str, project_name: str) -> None:
        await asyncio.to_thread(self._put_sync, thread_ts, project_name)

    def _put_sync(self, thread_ts: str, project_name: str) -> None:
        self._conn.execute(
            "INSERT INTO lobby_threads (thread_ts, project_name, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_ts) DO UPDATE SET project_name = excluded.project_name, "
            "updated_at = excluded.updated_at",
            (thread_ts, project_name, self._now()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_SHARED_DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_digest_state (
    channel_id     TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (channel_id, project_name)
)
"""


@dataclass(frozen=True)
class SharedDigestState:
    watermarks: dict[str, str | None]
    last_digest_at: float | None


class SharedDigestStateStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_SHARED_DIGEST_SCHEMA)
        self._conn.commit()

    async def get(self, channel_id: str) -> SharedDigestState:
        return await asyncio.to_thread(self._get_sync, channel_id)

    def _get_sync(self, channel_id: str) -> SharedDigestState:
        rows = self._conn.execute(
            "SELECT project_name, watermark_sha, last_digest_at FROM shared_digest_state "
            "WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        watermarks = {r[0]: r[1] for r in rows}
        last = max((r[2] for r in rows if r[2] is not None), default=None)
        return SharedDigestState(watermarks=watermarks, last_digest_at=last)

    async def advance(self, channel_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, channel_id, heads, last_digest_at)

    def _advance_sync(self, channel_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        for project_name, head in heads.items():
            self._conn.execute(
                "INSERT INTO shared_digest_state (channel_id, project_name, watermark_sha, last_digest_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(channel_id, project_name) DO UPDATE SET "
                "watermark_sha = excluded.watermark_sha, last_digest_at = excluded.last_digest_at",
                (channel_id, project_name, head, last_digest_at),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_ACTION_TIMER_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_timer (
    action_key    TEXT PRIMARY KEY,
    last_fired_at REAL NOT NULL
)
"""


class ActionTimerStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_ACTION_TIMER_SCHEMA)
        self._conn.commit()

    async def get(self, action_key: str) -> float | None:
        return await asyncio.to_thread(self._get_sync, action_key)

    def _get_sync(self, action_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT last_fired_at FROM action_timer WHERE action_key = ?", (action_key,)
        ).fetchone()
        return row[0] if row else None

    async def advance(self, action_key: str, last_fired_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, action_key, last_fired_at)

    def _advance_sync(self, action_key: str, last_fired_at: float) -> None:
        self._conn.execute(
            "INSERT INTO action_timer (action_key, last_fired_at) VALUES (?, ?) "
            "ON CONFLICT(action_key) DO UPDATE SET last_fired_at = excluded.last_fired_at",
            (action_key, last_fired_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


_PERSONAL_SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_subs (
    user_id      TEXT NOT NULL,
    project_name TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, project_name)
)
"""

_PERSONAL_PREFS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_prefs (
    user_id TEXT PRIMARY KEY,
    cadence TEXT NOT NULL
)
"""


class PersonalSubStore:
    """A user's persisted project interests + their personal-digest cadence."""

    def __init__(self, db_path: str, time_fn: Callable[[], float] = time.time) -> None:
        self._now = time_fn
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_PERSONAL_SUBS_SCHEMA)
        self._conn.execute(_PERSONAL_PREFS_SCHEMA)
        self._conn.commit()

    async def add(self, user_id: str, project: str) -> None:
        await asyncio.to_thread(self._add_sync, user_id, project)

    def _add_sync(self, user_id: str, project: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO personal_subs (user_id, project_name, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, project, self._now()),
        )
        self._conn.commit()

    async def remove(self, user_id: str, project: str) -> None:
        await asyncio.to_thread(self._remove_sync, user_id, project)

    def _remove_sync(self, user_id: str, project: str) -> None:
        self._conn.execute(
            "DELETE FROM personal_subs WHERE user_id = ? AND project_name = ?",
            (user_id, project),
        )
        self._conn.commit()

    async def list_for(self, user_id: str) -> tuple[str, ...]:
        return await asyncio.to_thread(self._list_for_sync, user_id)

    def _list_for_sync(self, user_id: str) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT project_name FROM personal_subs WHERE user_id = ? ORDER BY created_at, project_name",
            (user_id,),
        ).fetchall()
        return tuple(r[0] for r in rows)

    async def all_user_ids(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._all_user_ids_sync)

    def _all_user_ids_sync(self) -> tuple[str, ...]:
        rows = self._conn.execute("SELECT DISTINCT user_id FROM personal_subs").fetchall()
        return tuple(r[0] for r in rows)

    async def get_cadence(self, user_id: str) -> str | None:
        return await asyncio.to_thread(self._get_cadence_sync, user_id)

    def _get_cadence_sync(self, user_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT cadence FROM personal_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None

    async def set_cadence(self, user_id: str, cadence: str) -> None:
        await asyncio.to_thread(self._set_cadence_sync, user_id, cadence)

    def _set_cadence_sync(self, user_id: str, cadence: str) -> None:
        self._conn.execute(
            "INSERT INTO personal_prefs (user_id, cadence) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET cadence = excluded.cadence",
            (user_id, cadence),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
