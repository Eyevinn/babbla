from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from babbla.config import ProjectBinding
from babbla.read_only import (
    build_agent_config,
    readonly_hook_kwargs,
    skill_loading_kwargs,
)
from babbla.runtime import RuntimeProfile, tuning_kwargs


@dataclass(frozen=True)
class Artifact:
    filename: str
    data: bytes


@dataclass(frozen=True)
class CitedAnswer:
    text: str
    session_id: str | None
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class Secrets:
    github_token: str
    ask: RuntimeProfile = field(default_factory=RuntimeProfile)
    classifier: RuntimeProfile = field(default_factory=RuntimeProfile)
    github_launcher: str = "docker"
    skills_pool: str = "config/skills"


def _scratch_path(scratch_key: str) -> str:
    """A STABLE scratch dir path for a conversation thread. Keyed by scratch_key
    (the thread_ts) so a thread's turns share one cwd — required for session
    resume, which the CLI scopes by cwd path. Lives under $TMPDIR (honor a
    writable tmpfs in containers)."""
    digest = hashlib.sha1(scratch_key.encode()).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"babbla-skill-{digest}")


def _stage_skills(pool: str, names: tuple[str, ...], scratch: str) -> None:
    dest_root = Path(scratch) / ".claude" / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(Path(pool) / name, dest_root / name)


def _collect_artifacts(scratch: str) -> tuple[Artifact, ...]:
    root = Path(scratch)
    out: list[Artifact] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue  # skips <scratch>/.claude/skills/* (staged skills) and dotfiles
        out.append(Artifact(filename=p.name, data=p.read_bytes()))
    return tuple(out)


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
        self, text: str, binding: ProjectBinding, resume_session_id: str | None,
        *, system_prompt: str | None = None, scratch_key: str | None = None,
    ) -> CitedAnswer:
        cfg = build_agent_config(
            owner=binding.owner,
            repo=binding.repo,
            github_token=self._secrets.github_token,
            model=self._secrets.ask.model,
            github_launcher=self._secrets.github_launcher,
            skills=binding.skills,
        )
        # The skilled branch needs a STABLE per-thread scratch path so session
        # resume works (the CLI scopes sessions by cwd — a fresh random cwd each
        # turn crashes resume with "No conversation found"). It fires only when a
        # scratch_key is supplied, which the interactive Ask paths pass as the
        # thread_ts; digest/quiz/adr callers pass none, so they NEVER go skilled
        # (digest-path skills stay out of scope).
        if cfg.skills and scratch_key is not None:
            return await self._run_skilled(
                cfg, text, binding, resume_session_id, system_prompt, scratch_key
            )
        return await self._run_plain(cfg, text, binding, resume_session_id, system_prompt)

    def _base_options(self, cfg, system_prompt, resume_session_id, **extra) -> ClaudeAgentOptions:
        # Read-only by construction (ADR 0003), enforced at runtime:
        # - setting_sources=[] isolates the agent from the host's Claude settings.
        #   Without it the CLI loads the operator's ~/.claude/settings.json, whose
        #   permissions.allow rules widen what permission_mode="dontAsk" permits —
        #   the 2026-06-20 plain-path tool leak. The skilled path overrides this to
        #   ["project"] (still excludes user settings) to discover staged skills.
        # - strict_mcp_config pins MCP to the github server we pass, ignoring any
        #   server defined in loaded settings (e.g. the operator's claude.ai ones).
        params = dict(
            model=cfg.model,
            system_prompt=system_prompt or cfg.system_prompt,
            allowed_tools=list(cfg.allowed_tools),
            permission_mode=cfg.permission_mode,
            mcp_servers=cfg.mcp_servers,
            setting_sources=[],
            strict_mcp_config=True,
            **tuning_kwargs(self._secrets.ask),
        )
        params.update(extra)  # path-specific overrides (skilled: setting_sources, cwd, skills, hooks)
        options = ClaudeAgentOptions(**params)
        if resume_session_id:
            options.resume = resume_session_id
        return options

    async def _drain(self, options, text, resume_session_id):
        last_text: str | None = None
        session_id: str | None = resume_session_id
        async for message in self._query(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                last_text = captured
            sid = getattr(message, "session_id", None)
            if sid:
                session_id = sid
        return last_text, session_id

    def _fallback(self, binding) -> str:
        return f"I don't know — I couldn't find anything in {binding.name}'s history."

    async def _run_plain(self, cfg, text, binding, resume_session_id, system_prompt) -> CitedAnswer:
        # The plain path has no scratch workspace, so it installs the deny-by-default
        # readonly guard (denies every non-github tool). This is the independent
        # runtime layer the plain path was missing (incident 2026-06-20).
        options = self._base_options(
            cfg, system_prompt, resume_session_id, **readonly_hook_kwargs()
        )
        last_text, session_id = await self._drain(options, text, resume_session_id)
        return CitedAnswer(text=last_text or self._fallback(binding), session_id=session_id)

    async def _run_skilled(
        self, cfg, text, binding, resume_session_id, system_prompt, scratch_key
    ) -> CitedAnswer:
        # Deterministic per-thread path (NOT mkdtemp): turn N+1 must reuse the
        # same cwd as turn N or resume crashes. Wipe + recreate so the dir starts
        # empty each turn (simple artifact capture); resume still works because
        # the session transcript lives in ~/.claude keyed by the cwd *path*, not
        # inside the dir (validated by smoke_resume2). The orchestrator serializes
        # asks per thread, so the shared path is never used concurrently.
        scratch = _scratch_path(scratch_key)
        shutil.rmtree(scratch, ignore_errors=True)   # clear any prior-turn / crashed-run leftovers
        os.makedirs(scratch, exist_ok=True)
        try:
            _stage_skills(self._secrets.skills_pool, cfg.skills, scratch)
            options = self._base_options(
                cfg, system_prompt, resume_session_id,
                **skill_loading_kwargs(scratch_dir=scratch, skills=cfg.skills),
            )
            last_text, session_id = await self._drain(options, text, resume_session_id)
            artifacts = _collect_artifacts(scratch)
            return CitedAnswer(
                text=last_text or self._fallback(binding),
                session_id=session_id,
                artifacts=artifacts,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
