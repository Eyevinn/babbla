from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from claude_agent_sdk import ClaudeAgentOptions

from babbla.agent_runner import _extract_text

_CADENCES = {"daily", "weekly", "off"}
# Verbs that count as a subscription-management intent. Anything else the
# classifier emits (prose, a greeting, "NONE") falls through to the Q&A agent.
_MGMT_VERBS = {"subscribe", "unsubscribe", "list", "subscriptions", "digest", "topic"}


@dataclass(frozen=True)
class Command:
    verb: str               # subscribe | unsubscribe | list | digest | help
                            # | topic-add | topic-remove | topic-list
    arg: str | None = None  # project name (sub/unsub) or cadence (digest)
    project: str | None = None
    name: str | None = None
    description: str | None = None


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
    if verb == "topic":
        body = text.split(None, 2)            # ["topic", sub, "rest..."]
        sub = body[1].lower() if len(body) > 1 else ""
        rest = body[2] if len(body) > 2 else ""
        if sub == "list" and len(body) == 2:
            return Command("topic-list")
        parts = [p.strip() for p in rest.split("|")]
        if sub == "add" and len(parts) >= 3 and all(parts[:3]):
            return Command("topic-add", project=parts[0], name=parts[1], description=parts[2])
        if sub == "remove" and len(parts) >= 2 and all(parts[:2]):
            return Command("topic-remove", project=parts[0], name=parts[1])
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
            "(a) MANAGING their personal project subscriptions, (b) MANAGING their "
            "personal digest TOPICS (thematic filters on a followed project), or (c) "
            "asking a question about a project. Output EXACTLY one of:\n"
            "  subscribe <project name>\n"
            "  unsubscribe <project name>\n"
            "  list\n"
            "  digest daily   (or: digest weekly | digest off)\n"
            "  topic add <project name> | <topic name> | <description>\n"
            "  topic remove <project name> | <topic name>\n"
            "  topic list\n"
            "  NONE\n\n"
            "Map the user's wording, e.g.:\n"
            "  'follow MyTV' / 'subscribe me to MyTV' / 'add MyTV'        -> subscribe MyTV\n"
            "  'stop following MyTV' / 'drop MyTV' / 'mute MyTV'          -> unsubscribe MyTV\n"
            "  'what am I following?' / 'my subs'                         -> list\n"
            "  'send my digest daily' / 'pause my digest'                -> digest daily|weekly|off\n"
            "  'only show me security in MyTV' / 'filter MyTV to security'\n"
            "        -> topic add MyTV | security | auth, secrets, access control, CVEs, dependency security bumps\n"
            "  'stop filtering MyTV to security' / 'remove the security topic from MyTV'\n"
            "        -> topic remove MyTV | security\n"
            "  'what topics do I have' / 'my filters'                     -> topic list\n"
            "  'how does the digest work?' / 'what's in MyTV?' / 'hi'     -> NONE\n\n"
            "For `topic add`, ALWAYS supply a useful <description>: expand the user's short "
            "topic name into a comma-separated phrase of the concepts it should match, so the "
            "digest can filter on it. Use the user's own description verbatim if they gave one. "
            "Copy a project name EXACTLY as written in the list. A 'what/show/list my "
            "subscriptions' question is `list`; 'what topics do I have' is `topic list`. If the "
            "message is about digest FREQUENCY (daily/weekly/off), it is a `digest` command. "
            "Anything that is a question ABOUT a project's code/history, a greeting, or unclear "
            "is NONE. When genuinely unsure, output NONE.\n\nProjects:\n" + listing
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


def render_topic_added(project: str, name: str, description: str) -> str:
    return (
        f"✅ Added topic *{name}* to *{project}* — your digest's *{project}* section will "
        f"now include only changes about _{description}_.\n"
        f"Restate it to refine the description, or say \"remove the {name} topic from "
        f"{project}\" to drop it."
    )


def render_topic_removed(project: str, name: str) -> str:
    return f"Removed topic *{name}* from *{project}*."


def render_topic_list(topics_by_project: dict) -> str:
    if not topics_by_project:
        return (
            "You have no digest topics. In a DM, say something like "
            "\"only show me security changes in MyTV\" and I'll add one."
        )
    lines = []
    for project, topics in topics_by_project.items():
        labels = ", ".join(f"*{n}*" for n, _ in topics)
        lines.append(f"• *{project}*: {labels}")
    return "Your digest topics (your digest is filtered to these per project):\n" + "\n".join(lines)


def render_topic_needs_follow(project: str) -> str:
    return (
        f"You're not following *{project}* yet, so it isn't in your digest. "
        f"Follow it first (e.g. \"subscribe to {project}\"), then add a topic."
    )
