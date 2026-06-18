from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from babbla.config import ProjectBinding
from babbla.read_only import DEFAULT_MODEL, build_agent_config


@dataclass(frozen=True)
class CitedAnswer:
    text: str
    session_id: str | None


@dataclass(frozen=True)
class Secrets:
    github_token: str
    agentmemory_url: str
    agentmemory_secret: str
    model: str = DEFAULT_MODEL


def _extract_text(message) -> str | None:
    """Return assistant-visible text from a message, or None if it carries none."""
    # Terminal ResultMessage carries the final string in `.result`.
    result = getattr(message, "result", None)
    if isinstance(result, str) and result:
        return result
    # AssistantMessage carries a list of content blocks with `.text` on text blocks.
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = [getattr(b, "text", "") for b in content if getattr(b, "text", "")]
        if parts:
            return " ".join(parts)
    return None


class AgentRunner:
    def __init__(self, secrets: Secrets, query_fn=_sdk_query) -> None:
        self._secrets = secrets
        self._query = query_fn

    async def run_ask(
        self, text: str, binding: ProjectBinding, resume_session_id: str | None
    ) -> CitedAnswer:
        cfg = build_agent_config(
            owner=binding.owner,
            repo=binding.repo,
            github_token=self._secrets.github_token,
            agentmemory_url=self._secrets.agentmemory_url,
            agentmemory_secret=self._secrets.agentmemory_secret,
            model=self._secrets.model,
        )
        options = ClaudeAgentOptions(
            model=cfg.model,
            system_prompt=cfg.system_prompt,
            allowed_tools=list(cfg.allowed_tools),
            permission_mode=cfg.permission_mode,
            mcp_servers=cfg.mcp_servers,
        )
        if resume_session_id:
            options.resume = resume_session_id

        last_text: str | None = None
        session_id: str | None = resume_session_id
        async for message in self._query(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                last_text = captured
            sid = getattr(message, "session_id", None)
            if sid:
                session_id = sid

        fallback = f"I don't know — I couldn't find anything in {binding.name}'s history."
        return CitedAnswer(
            text=last_text or fallback,
            session_id=session_id,
        )
