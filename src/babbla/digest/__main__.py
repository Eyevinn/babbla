from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from babbla.config import load_config
from babbla.digest.scheduler import ActionScheduler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_once(scheduler, project: str | None = None) -> int:
    if project is not None:
        matching = tuple(
            a for a in scheduler._actions if getattr(a, "project", None) == project
        )
        if not matching:
            print(f"unknown project: {project}", file=sys.stderr)
            return 2
        scheduler = ActionScheduler(
            actions=matching, now_fn=scheduler._now_fn, interval_s=scheduler._interval_s
        )
    await scheduler.tick(_utcnow())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m babbla.digest")
    parser.add_argument("--once", action="store_true", help="run one due-check pass and exit")
    parser.add_argument("--project", default=None, help="limit to one project by name")
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("only --once is supported")

    from slack_sdk.web.async_client import AsyncWebClient

    from babbla.app import build_scheduler, load_secrets

    secrets = load_secrets(os.environ)
    config = load_config(os.environ.get("BABBLA_CONFIG", "config/channels.yaml"))
    client = AsyncWebClient(token=os.environ["SLACK_BOT_TOKEN"])
    scheduler = build_scheduler(
        config=config,
        secrets=secrets,
        db_path=os.environ.get("BABBLA_DB", "babbla.db"),
        client=client,
    )
    return asyncio.run(run_once(scheduler, args.project))


if __name__ == "__main__":
    raise SystemExit(main())
