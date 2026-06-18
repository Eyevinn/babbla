from __future__ import annotations

import asyncio
import logging
import os
from typing import Mapping

from concierge.agent_runner import AgentRunner, Secrets
from concierge.config import load_config
from concierge.orchestrator import Orchestrator
from concierge.read_only import DEFAULT_MODEL
from concierge.session_store import SessionStore
from concierge.slack_adapter import register_handlers

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
        model=env.get("CONCIERGE_MODEL", DEFAULT_MODEL),
    )


def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets) -> Orchestrator:
    config = load_config(config_path)
    runner = AgentRunner(secrets)
    store = SessionStore(db_path)
    return Orchestrator(config, runner, store)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    secrets = load_secrets(os.environ)
    orchestrator = build_orchestrator(
        config_path=os.environ.get("CONCIERGE_CONFIG", "config/channels.yaml"),
        db_path=os.environ.get("CONCIERGE_DB", "concierge.db"),
        secrets=secrets,
    )
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    register_handlers(app, orchestrator)
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    logger.info("Project Concierge (MyTV Q&A pilot) starting in Socket Mode…")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
