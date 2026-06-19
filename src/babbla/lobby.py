from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Sequence

from claude_agent_sdk import ClaudeAgentOptions

from babbla.agent_runner import _extract_text
from babbla.config import ProjectBinding

# Mirror access._OPEN_TIERS: which tiers the open discovery list may advertise.
_OPEN_TIERS = {"public", "internal"}


@dataclass(frozen=True)
class CatalogEntry:
    binding: ProjectBinding
    description: str | None


def build_catalog(
    bindings: Sequence[ProjectBinding],
    get_json: Callable[[str], object | None],
) -> tuple[CatalogEntry, ...]:
    """Fetch each repo's GitHub description once. One failure never blocks the rest."""
    entries = []
    for b in bindings:
        description = None
        try:
            data = get_json(f"/repos/{b.owner}/{b.repo}")
            if isinstance(data, dict):
                desc = data.get("description")
                if isinstance(desc, str) and desc.strip():
                    description = desc.strip()
        except Exception:
            description = None  # degrade to name/repo routing for this entry
        entries.append(CatalogEntry(binding=b, description=description))
    return tuple(entries)


def _normalize_line(line: str) -> str:
    """Strip whitespace, surrounding markdown emphasis, and trailing punctuation."""
    line = line.strip().strip("*_`").strip()
    return line.rstrip(".!:").strip()


async def route(
    text: str,
    catalog: Sequence[CatalogEntry],
    classify_fn: Callable[[str, Sequence[CatalogEntry]], Awaitable[str]],
) -> CatalogEntry | None:
    """Ask the classifier for a project, map its reply to an entry by name.

    Chatty models (Opus 4.8) reason first, then state the bare name on its own
    final line. So scan lines bottom-up — the conclusion is at the end — and
    return the first line that exactly matches a project name. A name embedded
    mid-sentence does NOT match (stays conservative); 'NONE' matches nothing.
    """
    reply = await classify_fn(text, catalog) or ""
    names = {entry.binding.name: entry for entry in catalog}
    for line in reversed(reply.splitlines()):
        entry = names.get(_normalize_line(line))
        if entry is not None:
            return entry
    return None  # "NONE", prose, or any unrecognised reply


def make_classify_fn(query_fn, model: str):
    """Default classifier: a tools-less SDK query that returns exactly a name or NONE."""

    async def classify_fn(text: str, catalog: Sequence[CatalogEntry]) -> str:
        listing = "\n".join(
            f"- {e.binding.name}: {e.description or e.binding.repo}" for e in catalog
        )
        system_prompt = (
            "You route a question to one project. Reply with the EXACT name of the single "
            "best-matching project from the list, or the word NONE if none clearly fits. "
            "Reply with ONLY the name or NONE — no other text.\n\nProjects:\n" + listing
        )
        # A pure label-emitter: no tools, no MCP servers, and no filesystem
        # settings (CLAUDE.md / project settings / agentmemory wiring). Without
        # setting_sources=[] the SDK loads project context and the classifier
        # starts answering like a full assistant — emitting prose, not a name.
        options = ClaudeAgentOptions(
            model=model,
            system_prompt=system_prompt,
            allowed_tools=[],
            mcp_servers={},
            setting_sources=[],
        )
        reply = ""
        async for message in query_fn(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                reply = captured
        return reply

    return classify_fn


def discovery_reply(catalog: Sequence[CatalogEntry]) -> str:
    visible = [e for e in catalog if e.binding.visibility in _OPEN_TIERS]
    if not visible:
        return (
            "🔎 I'm not sure which project that's about, and I don't have any "
            "projects to suggest yet."
        )
    lines = []
    for e in visible:
        if e.binding.channel_id:
            lines.append(f"• *{e.binding.name}* — <#{e.binding.channel_id}>")
        else:
            lines.append(f"• *{e.binding.name}*")
    return (
        "🔎 I'm not sure which project that's about. I can help with:\n"
        + "\n".join(lines)
        + "\nAsk in a project's channel, or rephrase your question here."
    )


def pointer_suffix(entry: CatalogEntry) -> str:
    b = entry.binding
    if b.channel_id:
        return f"\n\n↪ This is about *{b.name}* — for ongoing updates, join <#{b.channel_id}>."
    return f"\n\n↪ This is about *{b.name}*."
