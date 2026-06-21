from __future__ import annotations

from dataclasses import dataclass

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
