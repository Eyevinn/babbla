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
from babbla.digest.actions import (
    PerProjectDigestAction, PersonalDigestAction, QuizAction, SharedDigestAction,
)
from babbla.digest.anchors import make_get_json
from babbla.digest.poster import SlackPoster
from babbla.digest.quiz import QuizRunner
from babbla.digest.runner import DigestRunner
from babbla.digest.scheduler import ActionScheduler
from babbla.lobby import build_catalog, make_classify_fn
from babbla.personal import make_intent_fn
from babbla.orchestrator import Orchestrator
from babbla.read_only import DEFAULT_MODEL
from babbla.session_store import (
    ActionTimerStore, DigestStateStore, LobbyThreadStore, PersonalDigestStateStore,
    PersonalSubStore, SessionStore, SharedDigestStateStore,
)
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
    personal_store = PersonalSubStore(db_path)
    default_cadence = config.personal_digest.default_cadence if config.personal_digest else "weekly"
    intent_fn = make_intent_fn(_sdk_query, secrets.model)
    if config.lobby_channel_id is None and not config.subscriptions and config.personal_digest is None:
        return Orchestrator(
            config, runner, store,
            personal_store=personal_store, personal_default_cadence=default_cadence,
            intent_fn=intent_fn,
        )
    reader = get_json or make_get_json(secrets.github_token)
    catalog = build_catalog([b for b in config.bindings], reader)
    return Orchestrator(
        config, runner, store,
        catalog=catalog,
        classify_fn=make_classify_fn(_sdk_query, secrets.model),
        lobby_store=LobbyThreadStore(db_path),
        personal_store=personal_store,
        personal_default_cadence=default_cadence,
        intent_fn=intent_fn,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_scheduler(*, config, secrets: Secrets, db_path: str, client) -> ActionScheduler:
    get_json = make_get_json(secrets.github_token)
    poster = SlackPoster(client)
    digest_runner = DigestRunner(AgentRunner(secrets))
    quiz_runner = QuizRunner(AgentRunner(secrets))
    digest_store = DigestStateStore(db_path)
    shared_store = SharedDigestStateStore(db_path)
    timer_store = ActionTimerStore(db_path)
    by_name = {b.name: b for b in config.bindings}
    actions = []
    for b in config.digest_bindings():
        actions.append(PerProjectDigestAction(b, digest_store, get_json, digest_runner, poster))
    for s in config.digest_subscriptions():
        actions.append(SharedDigestAction(s, by_name, shared_store, get_json, digest_runner, poster))
    for b in config.quiz_bindings():
        actions.append(QuizAction(b, timer_store, quiz_runner, poster, b.quiz.cadence, b.quiz.tz, b.quiz.count))
    if config.personal_digest is not None:
        personal_store = PersonalSubStore(db_path)
        personal_state = PersonalDigestStateStore(db_path)
        actions.append(
            PersonalDigestAction(
                personal_store, personal_state, by_name, get_json, digest_runner, poster,
                config.personal_digest.default_cadence, config.personal_digest.tz,
            )
        )
    return ActionScheduler(actions=tuple(actions), now_fn=_utcnow)


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
