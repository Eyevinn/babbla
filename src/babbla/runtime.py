from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from claude_agent_sdk import ClaudeAgentOptions

from babbla.read_only import DEFAULT_MODEL


@dataclass(frozen=True)
class RuntimeProfile:
    """Per-surface Claude Agent SDK tuning. `model` keeps its existing
    call-site handling; the four optional knobs are applied via tuning_kwargs
    and omitted when None so the SDK keeps its own default (inert-until-set)."""

    model: str = DEFAULT_MODEL
    effort: str | None = None            # 'low'|'medium'|'high'|'xhigh'|'max'
    fallback_model: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None


def tuning_kwargs(p: RuntimeProfile) -> dict:
    """The four NEW optional knobs as ClaudeAgentOptions kwargs. Omits any knob
    left at None. Never emits `model` (keyed separately at each call-site, so
    emitting it here would double-key the options constructor)."""
    out: dict = {}
    if p.effort is not None:
        out["effort"] = p.effort
    if p.fallback_model is not None:
        out["fallback_model"] = p.fallback_model
    if p.max_turns is not None:
        out["max_turns"] = p.max_turns
    if p.max_budget_usd is not None:
        out["max_budget_usd"] = p.max_budget_usd
    return out


def classifier_options(p: RuntimeProfile, system_prompt: str) -> ClaudeAgentOptions:
    """The shared tools-less classifier options for lobby routing and personal
    intent. setting_sources=[] isolates the classifier from host/project context
    (without it it loads CLAUDE.md and emits prose instead of a bare name — the
    2026-06-20 routing fix); mcp_servers={} + allowed_tools=[] keep it a pure
    label-emitter."""
    return ClaudeAgentOptions(
        model=p.model,
        system_prompt=system_prompt,
        allowed_tools=[],
        mcp_servers={},
        setting_sources=[],
        **tuning_kwargs(p),
    )


_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _effort(env: Mapping[str, str], key: str) -> str | None:
    v = env.get(key)
    if not v:
        return None
    if v not in _EFFORTS:
        raise RuntimeError(f"{key}={v!r} must be one of {', '.join(_EFFORTS)}")
    return v


def _pos_int(env: Mapping[str, str], key: str) -> int | None:
    v = env.get(key)
    if not v:
        return None
    try:
        n = int(v)
    except ValueError:
        raise RuntimeError(f"{key}={v!r} must be a positive integer") from None
    if n <= 0:
        raise RuntimeError(f"{key}={v!r} must be a positive integer")
    return n


def _pos_float(env: Mapping[str, str], key: str) -> float | None:
    v = env.get(key)
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        raise RuntimeError(f"{key}={v!r} must be a positive number") from None
    if x <= 0:
        raise RuntimeError(f"{key}={v!r} must be a positive number")
    return x


def _profile(env: Mapping[str, str], prefix: str, *, default_model: str) -> RuntimeProfile:
    p = f"BABBLA_{prefix}_"
    return RuntimeProfile(
        model=env.get(p + "MODEL") or default_model,
        effort=_effort(env, p + "EFFORT"),
        fallback_model=env.get(p + "FALLBACK_MODEL") or None,
        max_turns=_pos_int(env, p + "MAX_TURNS"),
        max_budget_usd=_pos_float(env, p + "MAX_BUDGET_USD"),
    )


def load_profiles(env: Mapping[str, str]) -> tuple[RuntimeProfile, RuntimeProfile]:
    """Resolve (ask, classifier) profiles from env. BABBLA_MODEL is the shared
    default for both surfaces' model; the four tuning knobs are per-surface and
    default to None (the SDK runtime default). Raises RuntimeError on a bad
    value so misconfiguration fails at boot, not at the first ask."""
    base_model = env.get("BABBLA_MODEL") or DEFAULT_MODEL
    return (
        _profile(env, "ASK", default_model=base_model),
        _profile(env, "CLASSIFIER", default_model=base_model),
    )
