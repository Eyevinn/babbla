from __future__ import annotations

import asyncio
import sqlite3
import time
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
