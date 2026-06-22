from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

MembershipFn = Callable[[str, "str | None"], Awaitable[bool]]


async def deny_membership(user_id: str, channel_id: str | None) -> bool:
    """Fail-closed default: nobody is a member unless a real oracle is wired."""
    return False


def make_membership(
    client,
    *,
    ttl_seconds: float = 5.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> MembershipFn:
    """Build an async `(user_id, channel_id) -> bool` membership oracle.

    Backed by Slack `conversations.members`. Fail-closed on any error.
    Results (positive and negative) are cached per (channel, user) for
    `ttl_seconds` to absorb bursts within a single thread/turn.
    """
    cache: dict[tuple[str, str], tuple[bool, float]] = {}

    async def is_member(user_id: str, channel_id: str | None) -> bool:
        if not channel_id:
            return False
        key = (channel_id, user_id)
        now = now_fn()
        hit = cache.get(key)
        if hit is not None and hit[1] > now:
            return hit[0]
        try:
            found = await _lookup(client, channel_id, user_id)
        except SlackApiError as exc:
            logger.warning(
                "membership lookup failed (%s in %s): %s", user_id, channel_id, exc
            )
            found = False
        except Exception:  # transport / timeout — fail closed
            logger.exception("membership lookup error (%s in %s)", user_id, channel_id)
            found = False
        cache[key] = (found, now + ttl_seconds)
        return found

    return is_member


async def _lookup(client, channel_id: str, user_id: str) -> bool:
    cursor: str | None = None
    while True:
        resp = await client.conversations_members(
            channel=channel_id, limit=200, cursor=cursor
        )
        if user_id in (resp.get("members") or []):
            return True
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return False
