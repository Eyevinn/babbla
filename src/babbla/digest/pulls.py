from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StalePR:
    number: int
    title: str
    author: str
    url: str
    idle_days: int


def _parse_ts(value: str) -> datetime:
    # GitHub timestamps are ISO 8601 with a trailing Z (UTC).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stale_prs(owner, repo, *, now, threshold_days, include_drafts, get_json) -> list[StalePR]:
    """Fetch open PRs (read-only), keep those idle >= threshold_days, sort oldest-first."""
    path = f"/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=asc&per_page=100"
    data = get_json(path)
    if not data:
        return []
    out: list[StalePR] = []
    for pr in data:
        if pr.get("draft") and not include_drafts:
            continue
        idle = (now - _parse_ts(pr["updated_at"])).days
        if idle < threshold_days:
            continue
        out.append(
            StalePR(
                number=pr["number"],
                title=pr.get("title", ""),
                author=(pr.get("user") or {}).get("login", ""),
                url=pr.get("html_url", ""),
                idle_days=idle,
            )
        )
    out.sort(key=lambda p: p.idle_days, reverse=True)  # oldest-first = most idle first
    return out
