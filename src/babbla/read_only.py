from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import HookMatcher

DEFAULT_MODEL = "claude-opus-4-8"

GITHUB_TOOL_PREFIX = "mcp__github__"

# GitHub read-only-ness is enforced server-side (GITHUB_READ_ONLY=1 + the `stdio`
# subcommand), so a wildcard over that server is safe: the server cannot expose a
# writer. The github server is the agent's only tool source.
GITHUB_WILDCARD = "mcp__github__*"

ALLOWED_TOOLS: tuple[str, ...] = (GITHUB_WILDCARD,)


def _within(path: str, root: str) -> bool:
    """True iff `path` is inside `root`. Relative paths resolve against `root`
    (the agent's cwd = the scratch dir), not the host process cwd."""
    if not path:
        return False
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        p.resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _pre_tool(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}


def make_scratch_guard(scratch: str):
    """A PreToolUse hook confining a skill's file writes to `scratch`.

    Validated by Task 1 (Option D): under permission_mode="dontAsk", returning
    permissionDecision="allow" lets an in-scratch Write/Edit/Read through, while
    "deny" blocks out-of-scratch writes and Bash. Returning {} (no opinion)
    leaves MCP tools governed by allowed_tools + dontAsk exactly as today, so
    MCP writers stay denied.
    """
    async def guard(input, tool_use_id, context):
        tool = input.get("tool_name", "")
        if tool in ("Write", "Edit", "Read"):
            ti = input.get("tool_input", {})
            path = ti.get("file_path") or ti.get("path") or ""
            ok = _within(path, scratch)
            return _pre_tool("allow" if ok else "deny",
                             "scratch-scoped" if ok else "outside scratch workspace")
        if tool == "Bash":
            return _pre_tool("deny", "bash is not permitted on the skilled path")
        return {}
    return guard


def make_readonly_guard():
    """A PreToolUse hook for the plain (non-skilled) Ask path: deny-by-default.

    The plain path has no filesystem workspace, so the agent legitimately needs
    only the read-only github MCP tools. This guard denies every other tool
    (Bash, Read, Write, Edit, Agent, TaskCreate, WebFetch, the Skill tool, and
    any non-github MCP server) outright, independent of the permission layer.
    github tools get no opinion ({}) so they stay governed by allowed_tools +
    dontAsk.

    This is the runtime confinement layer the 2026-06-20 incident showed was
    missing on the plain path: with no hook AND no setting_sources isolation, the
    CLI loaded the operator's ~/.claude allow-rules, which pre-approved builtins.
    """
    async def guard(input, tool_use_id, context):
        tool = input.get("tool_name", "")
        if tool.startswith(GITHUB_TOOL_PREFIX):
            return {}  # no opinion -> governed by allowed_tools + dontAsk
        return _pre_tool("deny", "read-only: only the github tools are permitted")
    return guard


def skill_loading_kwargs(*, scratch_dir: str, skills: tuple[str, ...]) -> dict:
    """`ClaudeAgentOptions` kwargs that load ONLY `skills` from a clean scratch
    workspace headlessly, confine writes to scratch, and leak no Babbla-repo /
    user-global context. Validated by Task 1.

    - cwd=<scratch> — discovery + writes rooted at the clean temp dir.
    - setting_sources=["project"] — discover <scratch>/.claude/skills only.
    - skills=[names] — enable ONLY these (SDK appends Skill(<name>)).
    - hooks — PreToolUse scratch guard (see make_scratch_guard).

    Caller must stage the skills into <scratch>/.claude/skills/<name>.
    """
    return {
        "cwd": scratch_dir,
        "setting_sources": ["project"],
        "skills": list(skills),
        "hooks": {"PreToolUse": [HookMatcher(hooks=[make_scratch_guard(scratch_dir)])]},
    }


def readonly_hook_kwargs() -> dict:
    """`ClaudeAgentOptions` kwargs installing the plain-path deny-by-default guard
    (see make_readonly_guard). Parallel to skill_loading_kwargs for the plain path,
    which has no scratch workspace."""
    return {"hooks": {"PreToolUse": [HookMatcher(hooks=[make_readonly_guard()])]}}


@dataclass(frozen=True)
class AgentConfig:
    model: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    permission_mode: str
    mcp_servers: dict
    skills: tuple[str, ...] = ()


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


def build_agent_config(
    *,
    owner: str,
    repo: str,
    github_token: str,
    model: str = DEFAULT_MODEL,
    github_launcher: str = "docker",
    skills: tuple[str, ...] = (),
) -> AgentConfig:
    return AgentConfig(
        model=model,
        system_prompt=build_system_prompt(owner, repo),
        allowed_tools=ALLOWED_TOOLS,      # github only; builtins are hook-gated, NOT allow-listed
        permission_mode="dontAsk",
        mcp_servers={"github": _github_server(github_token, github_launcher)},
        skills=skills,
    )
