from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

_CADENCES = {"daily", "weekly", "off"}


@dataclass(frozen=True)
class Command:
    verb: str               # subscribe | unsubscribe | list | digest | help
    arg: str | None = None  # project name (sub/unsub) or cadence (digest)


def parse_command(text: str) -> Command:
    tokens = (text or "").split()
    if not tokens:
        return Command("list")
    verb = tokens[0].lower()
    if verb in ("subscribe", "unsubscribe"):
        if len(tokens) < 2:
            return Command("help")
        return Command(verb, tokens[1])
    if verb in ("list", "subscriptions"):
        return Command("list")
    if verb == "digest":
        if len(tokens) >= 2 and tokens[1].lower() in _CADENCES:
            return Command("digest", tokens[1].lower())
        return Command("help")
    return Command("help")


def render_subscribed(name: str) -> str:
    return (
        f"✅ Subscribed to *{name}*. I'll route your DM questions to it and "
        "include it in your personal digest."
    )


def render_unsubscribed(name: str) -> str:
    return f"Unsubscribed from *{name}*."


def render_unknown_project(available: Sequence[str]) -> str:
    listing = ", ".join(f"*{n}*" for n in available) or "(none yet)"
    return f"🤔 I don't know that project. I can follow: {listing}."


def render_private_refused(name: str) -> str:
    return (
        f"🔒 *{name}* is private — personal subscriptions only cover "
        "public/internal projects."
    )


def render_list(names: Sequence[str], cadence: str) -> str:
    if not names:
        return "You don't follow any projects yet. Use `/babbla subscribe <project>` to start."
    listing = ", ".join(f"*{n}*" for n in names)
    cad = "paused" if cadence == "off" else cadence
    return f"You follow: {listing}.\nPersonal digest: *{cad}*."


def render_digest_set(cadence: str) -> str:
    if cadence == "off":
        return "Personal digest *paused*. Your subscriptions are kept for Asks."
    return f"Personal digest set to *{cadence}*."


def render_help() -> str:
    return (
        "*Personal subscriptions* — manage what I follow for you:\n"
        "• `/babbla subscribe <project>` — follow a project\n"
        "• `/babbla unsubscribe <project>` — stop following\n"
        "• `/babbla list` — show your projects and digest cadence\n"
        "• `/babbla digest daily|weekly|off` — set your personal-digest cadence"
    )
