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


def build_system_prompt(owner: str, repo: str) -> str:
    slug = f"{owner}/{repo}"
    return (
        f"You are Babbla, a read-only assistant answering questions about the "
        f"{slug} project on GitHub. Answer ONLY from {slug}'s pushed history and code "
        f"(commits, pull requests, branches, files) reachable via the github tools, plus "
        f"rationale from the agentmemory tools. You have no write access and no local files.\n\n"
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


def build_agent_config(
    *,
    owner: str,
    repo: str,
    github_token: str,
    agentmemory_url: str,
    agentmemory_secret: str,
    model: str = DEFAULT_MODEL,
) -> AgentConfig:
    mcp_servers = {
        "github": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "-e",
                "GITHUB_READ_ONLY",
                "-e",
                "GITHUB_TOOLSETS",
                "ghcr.io/github/github-mcp-server",
                "stdio",
            ],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": github_token,
                "GITHUB_READ_ONLY": "1",
                "GITHUB_TOOLSETS": "context,repos,pull_requests,issues",
            },
        },
        "agentmemory": {
            "command": "npx",
            "args": ["-y", "@agentmemory/mcp"],
            "env": {
                "AGENTMEMORY_URL": agentmemory_url,
                "AGENTMEMORY_SECRET": agentmemory_secret,
            },
        },
    }
    return AgentConfig(
        model=model,
        system_prompt=build_system_prompt(owner, repo),
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",
        mcp_servers=mcp_servers,
    )
