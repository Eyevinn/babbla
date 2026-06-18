from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Mapping

from claude_agent_sdk import query as _sdk_query
from slack_sdk.web.async_client import AsyncWebClient  # noqa: F401  (type only; client comes from AsyncApp)

from babbla.agent_runner import AgentRunner, Secrets
from babbla.config import load_config
from babbla.digest.anchors import make_get_json
from babbla.digest.poster import SlackPoster
from babbla.digest.runner import DigestRunner
from babbla.digest.scheduler import DigestScheduler
from babbla.lobby import build_catalog, make_classify_fn
from babbla.orchestrator import Orchestrator
from babbla.read_only import DEFAULT_MODEL
from babbla.session_store import DigestStateStore, LobbyThreadStore, SessionStore
from babbla.slack_adapter import register_handlers

logger = logging.getLogger(__name__)

# ANTHROPIC_API_KEY is intentionally NOT required: the Agent SDK runs the
# Claude Code CLI, which authenticates via the local subscription login
# (Path B). Set ANTHROPIC_API_KEY in the env to use a metered API key instead.
_REQUIRED = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GITHUB_TOKEN")


def load_secrets(env: Mapping[str, str]) -> Secrets:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return Secrets(
        github_token=env["GITHUB_TOKEN"],
        agentmemory_url=env.get("AGENTMEMORY_URL", "http://localhost:3111"),
        agentmemory_secret=env.get("AGENTMEMORY_SECRET", ""),
        model=env.get("BABBLA_MODEL", DEFAULT_MODEL),
        github_launcher=env.get("BABBLA_GITHUB_MCP", "docker"),
    )


def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets, get_json=None) -> Orchestrator:
    config = load_config(config_path)
    runner = AgentRunner(secrets)
    store = SessionStore(db_path)
    if config.lobby_channel_id is None:
        return Orchestrator(config, runner, store)
    reader = get_json or make_get_json(secrets.github_token)
    catalog = build_catalog([b for b in config.bindings], reader)
    return Orchestrator(
        config, runner, store,
        catalog=catalog,
        classify_fn=make_classify_fn(_sdk_query, secrets.model),
        lobby_store=LobbyThreadStore(db_path),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_scheduler(*, config, secrets: Secrets, db_path: str, client) -> DigestScheduler:
    return DigestScheduler(
        config=config,
        store=DigestStateStore(db_path),
        runner=DigestRunner(AgentRunner(secrets)),
        poster=SlackPoster(client),
        get_json=make_get_json(secrets.github_token),
        now_fn=_utcnow,
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    secrets = load_secrets(os.environ)
    config_path = os.environ.get("BABBLA_CONFIG", "config/channels.yaml")
    db_path = os.environ.get("BABBLA_DB", "babbla.db")
    config = load_config(config_path)
    orchestrator = build_orchestrator(config_path=config_path, db_path=db_path, secrets=secrets)

    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    register_handlers(app, orchestrator, lobby_channel_id=config.lobby_channel_id)

    scheduler = build_scheduler(config=config, secrets=secrets, db_path=db_path, client=app.client)
    scheduler_task = asyncio.create_task(scheduler.run())  # retained for the process lifetime

    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    logger.info("Babbla starting in Socket Mode (digest scheduler active)…")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
