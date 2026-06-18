from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def cadence_bucket(when: datetime, cadence: str, tz: str) -> str:
    local = when.astimezone(ZoneInfo(tz))
    if cadence == "daily":
        return local.strftime("%Y-%m-%d")
    iso = local.isocalendar()  # weekly
    return f"{iso.year}-W{iso.week:02d}"


def is_due(now: datetime, last_digest_at: float | None, cadence: str, tz: str) -> bool:
    if last_digest_at is None:
        return True
    last = datetime.fromtimestamp(last_digest_at, tz=timezone.utc)
    return cadence_bucket(now, cadence, tz) != cadence_bucket(last, cadence, tz)
