from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from babbla.config import ProjectBinding

_OPEN_TIERS = {"public", "internal"}


def is_open_tier(binding: ProjectBinding) -> bool:
    return binding.visibility in _OPEN_TIERS


class Surface(Enum):
    CHANNEL = "channel"  # a project's bound Slack channel
    DM = "dm"            # Private Ask (1:1)
    LOBBY = "lobby"      # open discovery surface (non-channel)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None      # why denied (for logs)
    pointer: str | None = None     # user-facing denial text, if denied


def _pointer(binding: ProjectBinding) -> str:
    if binding.channel_id:
        return f"🔒 *{binding.name}* is private — ask about it in <#{binding.channel_id}>."
    return (
        f"🔒 *{binding.name}* is private and has no channel yet — "
        "ask once its channel is set up."
    )


def authorize_ask(binding: ProjectBinding, surface: Surface) -> AccessDecision:
    # On a project's own channel, membership in the channel IS the access.
    if surface is Surface.CHANNEL:
        return AccessDecision(allowed=True)
    # Non-channel surfaces (DM, later Lobby). `public` and `internal` are
    # handled identically ON PURPOSE: in a single Slack workspace every DM-er
    # is a workspace member, so the tiers only diverge at a future external /
    # Lobby edge. Do not "simplify" by dropping one tier.
    if is_open_tier(binding):
        return AccessDecision(allowed=True)
    return AccessDecision(
        allowed=False,
        reason=f"{binding.name} is private; {surface.value} is a non-channel surface",
        pointer=_pointer(binding),
    )
