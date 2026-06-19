from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from claude_agent_sdk import ClaudeAgentOptions

from babbla.agent_runner import _extract_text

_CADENCES = {"daily", "weekly", "off"}
# Verbs that count as a subscription-management intent. Anything else the
# classifier emits (prose, a greeting, "NONE") falls through to the Q&A agent.
_MGMT_VERBS = {"subscribe", "unsubscribe", "list", "subscriptions", "digest"}


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
        return Command(verb, " ".join(tokens[1:]))  # project names may be multi-word
    if verb in ("list", "subscriptions"):
        return Command("list")
    if verb == "digest":
        if len(tokens) >= 2 and tokens[1].lower() in _CADENCES:
            return Command("digest", tokens[1].lower())
        return Command("help")
    return Command("help")


async def classify_intent(
    text: str,
    project_names: Sequence[str],
    intent_fn: Callable[[str, Sequence[str]], Awaitable[str]],
) -> Command | None:
    """Map a free-text DM to a management Command, or None to fall through to Q&A.

    The classifier replies in the same grammar `parse_command` understands
    (`subscribe X`, `digest daily`, `list`, …) or the word NONE. Anything that
    isn't a recognised management verb is treated as a question, not a command.
    """
    reply = await intent_fn(text, project_names) or ""
    line = _command_line(reply)
    if line is None:
        return None
    return parse_command(line)


def _command_line(reply: str) -> str | None:
    """The model may prepend reasoning or wrap the answer in backticks. Take the
    last non-empty line, strip code fencing, and accept it only if it starts with
    a management verb (NONE / prose → not a command)."""
    for raw in reversed(reply.splitlines() or [reply]):
        s = raw.strip().strip("`").strip()
        if not s:
            continue
        return s if s.split()[0].lower() in _MGMT_VERBS else None
    return None


def make_intent_fn(query_fn, model: str):
    """Default intent classifier: a tools-less SDK query emitting one command line or NONE."""

    async def intent_fn(text: str, project_names: Sequence[str]) -> str:
        listing = "\n".join(f"- {n}" for n in project_names) or "(none configured)"
        system_prompt = (
            "Classify a single Slack DM and output ONE line, nothing else — no "
            "explanation, no reasoning, no tools, no backticks. The user is either "
            "(a) MANAGING their personal project subscriptions, or (b) asking a "
            "question about a project. Output EXACTLY one of:\n"
            "  subscribe <project name>\n"
            "  unsubscribe <project name>\n"
            "  list\n"
            "  digest daily   (or: digest weekly | digest off)\n"
            "  NONE\n\n"
            "Map the user's wording, e.g.:\n"
            "  'follow MyTV' / 'subscribe me to MyTV' / 'add MyTV'        -> subscribe MyTV\n"
            "  'stop following MyTV' / 'drop MyTV' / 'mute MyTV'          -> unsubscribe MyTV\n"
            "  'what am I following?' / 'what do I follow' / 'my subs'    -> list\n"
            "  'send my digest daily' / 'switch me to weekly' / 'pause my digest' -> digest daily|weekly|off\n"
            "  'how does the digest work?' / 'what's in MyTV?' / 'hi'     -> NONE\n\n"
            "Copy a project name EXACTLY as written in the list. A 'what/show/list my "
            "subscriptions' question is `list`, NOT a question. If the message is about "
            "digest FREQUENCY (mentions 'digest', daily, weekly, or off as a cadence), it "
            "is always a `digest` command — never subscribe/unsubscribe. Anything that is "
            "a question ABOUT a project's code/history, a greeting, or unclear is NONE. "
            "When genuinely unsure, output NONE.\n\nProjects:\n" + listing
        )
        options = ClaudeAgentOptions(
            model=model, system_prompt=system_prompt, allowed_tools=[]
        )
        reply = ""
        async for message in query_fn(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                reply = captured
        return reply

    return intent_fn


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
