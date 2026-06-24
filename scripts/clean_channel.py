#!/usr/bin/env python3
"""Delete Babbla's bot messages (and their uploaded files) from a Slack channel.

Only messages posted by the bot (identified by bot_id) are touched.
Human messages and tombstones are left in place — those require a user token.

Usage (run inside the container or with SLACK_BOT_TOKEN in env):

    # Delete today's bot messages from a channel:
    python scripts/clean_channel.py C0BC30EPX7F

    # Delete bot messages from a specific date (YYYY-MM-DD, local UTC):
    python scripts/clean_channel.py C0BC30EPX7F --date 2026-06-24

    # Preview what would be deleted without actually deleting:
    python scripts/clean_channel.py C0BC30EPX7F --dry-run

Inside Docker:
    docker compose exec babbla python /app/scripts/clean_channel.py C0BC30EPX7F
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient


async def clean(channel: str, oldest: float, dry_run: bool) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        sys.exit("SLACK_BOT_TOKEN not set")

    client = AsyncWebClient(token=token)
    deleted = files_deleted = skipped = 0

    async def delete_msg(ts: str) -> None:
        nonlocal deleted, skipped
        if dry_run:
            print(f"  [dry-run] would delete message ts={ts}")
            deleted += 1
            return
        try:
            await client.chat_delete(channel=channel, ts=ts)
            deleted += 1
        except SlackApiError as e:
            err = (e.response or {}).get("error", "?")
            if err not in ("cant_delete_message", "message_not_found"):
                print(f"  WARNING ts={ts}: {err}", file=sys.stderr)
            skipped += 1

    async def delete_file(file_id: str) -> None:
        nonlocal files_deleted
        if dry_run:
            print(f"  [dry-run] would delete file {file_id}")
            files_deleted += 1
            return
        try:
            await client.files_delete(file=file_id)
            files_deleted += 1
        except SlackApiError as e:
            err = (e.response or {}).get("error", "?")
            if err not in ("file_not_found", "file_deleted"):
                print(f"  WARNING file={file_id}: {err}", file=sys.stderr)

    # Collect all thread roots from the time window.
    roots: list[dict] = []
    cursor: str | None = None
    while True:
        resp = await client.conversations_history(
            channel=channel, oldest=str(oldest), limit=200, cursor=cursor or ""
        )
        roots.extend(resp.get("messages", []))
        if not resp.get("has_more"):
            break
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    print(f"Found {len(roots)} root messages — scanning threads for bot replies…")

    for root in roots:
        thread_ts = root["ts"]
        reply_cursor: str | None = None
        while True:
            rep = await client.conversations_replies(
                channel=channel, ts=thread_ts, oldest=str(oldest),
                limit=200, cursor=reply_cursor or ""
            )
            for msg in rep.get("messages", []):
                if not msg.get("bot_id"):
                    continue  # skip human messages
                # Delete file before message for file_share subtypes.
                if msg.get("subtype") == "file_share":
                    fid = (msg.get("file") or {}).get("id")
                    if fid:
                        await delete_file(fid)
                await delete_msg(msg["ts"])
            if not rep.get("has_more"):
                break
            reply_cursor = (rep.get("response_metadata") or {}).get("next_cursor") or None
            if not reply_cursor:
                break

    label = "[dry-run] " if dry_run else ""
    print(
        f"{label}Done: {deleted} messages deleted, "
        f"{files_deleted} files deleted, {skipped} skipped (not bot's)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("channel", help="Slack channel ID, e.g. C0BC30EPX7F")
    parser.add_argument(
        "--date", default=None,
        help="Delete messages from this date (YYYY-MM-DD UTC). Defaults to today.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be deleted without actually deleting anything.",
    )
    args = parser.parse_args()

    if args.date:
        day = datetime.datetime.strptime(args.date, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc
        )
    else:
        day = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    print(f"Cleaning bot messages in {args.channel} since {day.date()} UTC"
          + (" (dry-run)" if args.dry_run else "") + "…")
    asyncio.run(clean(args.channel, day.timestamp(), args.dry_run))


if __name__ == "__main__":
    main()
