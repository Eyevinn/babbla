from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL = "claude-opus-4-8"

GITHUB_TOOL_PREFIX = "mcp__github__"

# The ONLY agentmemory tools the agent may call. Adding one here also requires
# updating the guard test's expectation. Never add a writer.
AGENTMEMORY_READERS: tuple[str, ...] = (
    "mcp__agentmemory__memory_recall",
    "mcp__agentmemory__memory_smart_search",
    "mcp__agentmemory__memory_facet_query",
    "mcp__agentmemory__memory_relations",
)

# agentmemory mutating tools — listed so the guard test can assert none leak in.
AGENTMEMORY_WRITERS: tuple[str, ...] = (
    "mcp__agentmemory__memory_save",
    "mcp__agentmemory__memory_action_create",
    "mcp__agentmemory__memory_action_update",
    "mcp__agentmemory__memory_governance_delete",
)

# GitHub read-only-ness is enforced server-side (GITHUB_READ_ONLY=1 + the `stdio`
# subcommand), so a wildcard over that server is safe: the server cannot expose a
# writer. agentmemory exposes writers, so it is allowlisted tool-by-tool above.
GITHUB_WILDCARD = "mcp__github__*"

ALLOWED_TOOLS: tuple[str, ...] = (GITHUB_WILDCARD, *AGENTMEMORY_READERS)


@dataclass(frozen=True)
class AgentConfig:
    model: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    permission_mode: str
    mcp_servers: dict


# Digests are summarization, not Q&A: the runner hands over an authoritative,
# pre-gathered commit/PR list, so the agent must not re-verify or hedge about
# scope. Using the Q&A prompt here is what produced the "I'm scoped to X,
# summarizing from the lists you provided…" disclaimer.
DIGEST_SYSTEM_PROMPT = (
    "You are Babbla, writing a short release digest for Slack. You are handed an "
    "authoritative, already-gathered list of commits and pull requests, grouped by "
    "project. Treat that list as the complete, verified set of facts — do not try to "
    "verify it, fetch anything, or reason about what you can or cannot see.\n\n"
    "Write ONLY the digest itself. Do NOT add any preamble, note, caveat, or disclaimer "
    "about your scope, which repository you are 'scoped to', what you did or didn't verify, "
    "or that you are working from a provided list. No meta-commentary of any kind.\n\n"
    "Lead with a short headline, then summarize at a reader-friendly altitude and group "
    "related work. Cite commits by SHA and pull requests by number as GitHub links, using "
    "the owner/repo shown in each project's section heading (for a single-project digest, "
    "the project named in the instruction). Keep it concise and Slack-friendly."
)


def build_system_prompt(owner: str, repo: str) -> str:
    slug = f"{owner}/{repo}"
    return (
        f"You are Babbla, a read-only assistant answering questions about the "
        f"{slug} project on GitHub. Answer ONLY from {slug}'s pushed history and code "
        f"(commits, pull requests, issues, branches, files) reachable via the github tools. "
        f"The agentmemory tools are an OPTIONAL enrichment — extra per-commit rationale when "
        f"it happens to exist — not a required or co-equal source; treat them as "
        f"supplementary and never assume they hold the answer. "
        f"You have no write access and no local files.\n\n"
        f"The repository itself is the source of truth for \"why\". Before answering a "
        f"\"why\" or \"how does this work\" question, consult the project's own documentation "
        f"surfaces over the github tools: README, CLAUDE.md, anything under docs/ "
        f"(architecture notes and Architecture Decision Records in docs/adr/), commit "
        f"messages, pull request descriptions, and issues. These carry the rationale; the "
        f"diff alone only shows what changed, not why.\n\n"
        f"Rules:\n"
        f"- Default to the repository's default branch (main) as the shared truth; inspect a "
        f"specific PR or pushed branch only when the question calls for it.\n"
        f"- ALWAYS cite your sources as GitHub links: commit SHAs, pull request numbers, and "
        f"file paths (e.g. https://github.com/{slug}/commit/<sha>, "
        f"https://github.com/{slug}/pull/<n>, https://github.com/{slug}/blob/main/<path>).\n"
        f"- If the answer is not in {slug}'s history, say so plainly "
        f"(\"I don't know — that's not in {slug}'s history\"). Never guess or invent sources.\n"
        f"- Keep answers concise and Slack-friendly."
    )


def _github_server(token: str, launcher: str) -> dict:
    env = {
        "GITHUB_PERSONAL_ACCESS_TOKEN": token,
        "GITHUB_READ_ONLY": "1",
        "GITHUB_TOOLSETS": "context,repos,pull_requests,issues",
    }
    if launcher == "binary":
        return {"command": "github-mcp-server", "args": ["stdio"], "env": env}
    if launcher == "docker":
        return {
            "command": "docker",
            "args": [
                "run", "-i", "--rm",
                "-e", "GITHUB_PERSONAL_ACCESS_TOKEN",
                "-e", "GITHUB_READ_ONLY",
                "-e", "GITHUB_TOOLSETS",
                "ghcr.io/github/github-mcp-server", "stdio",
            ],
            "env": env,
        }
    raise ValueError(f"unknown github_launcher: {launcher!r}")


def _agentmemory_server(url: str, secret: str) -> dict:
    return {
        "command": "npx",
        "args": ["-y", "@agentmemory/mcp"],
        "env": {"AGENTMEMORY_URL": url, "AGENTMEMORY_SECRET": secret},
    }


def build_agent_config(
    *,
    owner: str,
    repo: str,
    github_token: str,
    agentmemory_url: str,
    agentmemory_secret: str,
    model: str = DEFAULT_MODEL,
    github_launcher: str = "docker",
) -> AgentConfig:
    mcp_servers = {"github": _github_server(github_token, github_launcher)}
    allowed_tools: tuple[str, ...] = (GITHUB_WILDCARD,)
    if agentmemory_url:  # agentmemory is OPTIONAL local enrichment (ADR 0009)
        mcp_servers["agentmemory"] = _agentmemory_server(agentmemory_url, agentmemory_secret)
        allowed_tools = ALLOWED_TOOLS
    return AgentConfig(
        model=model,
        system_prompt=build_system_prompt(owner, repo),
        allowed_tools=allowed_tools,
        permission_mode="dontAsk",
        mcp_servers=mcp_servers,
    )
