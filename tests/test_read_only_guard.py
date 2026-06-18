import pytest

from babbla.read_only import (
    AGENTMEMORY_READERS,
    AGENTMEMORY_WRITERS,
    ALLOWED_TOOLS,
    DEFAULT_MODEL,
    build_agent_config,
)

# Built-in tool names that must NEVER be granted to a read-only agent.
FORBIDDEN_BUILTINS = ("Bash", "Write", "Edit", "Read", "NotebookEdit", "WebFetch", "WebSearch")


@pytest.fixture
def cfg():
    return build_agent_config(
        owner="Wkkkkk",
        repo="MyTV",
        github_token="ghp_dummy",
        agentmemory_url="http://localhost:3111",
        agentmemory_secret="",
    )


def test_permission_mode_is_dontask(cfg):
    assert cfg.permission_mode == "dontAsk"


def test_permission_mode_never_bypass(cfg):
    assert cfg.permission_mode != "bypassPermissions"


def test_only_mcp_tools_allowed(cfg):
    # Every allowlisted tool is an MCP tool — no built-in filesystem/bash/web tools.
    for tool in cfg.allowed_tools:
        assert tool.startswith("mcp__"), f"non-MCP tool allowlisted: {tool}"
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools


def test_agentmemory_only_readers(cfg):
    am_tools = [t for t in cfg.allowed_tools if t.startswith("mcp__agentmemory__")]
    assert set(am_tools) == set(AGENTMEMORY_READERS)


def test_no_agentmemory_writer_allowlisted(cfg):
    for writer in AGENTMEMORY_WRITERS:
        assert writer not in cfg.allowed_tools


def test_github_server_is_readonly_stdio(cfg):
    gh = cfg.mcp_servers["github"]
    assert gh["command"] == "docker"
    assert "stdio" in gh["args"]
    assert "http" not in gh["args"]
    assert gh["env"]["GITHUB_READ_ONLY"] == "1"
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_dummy"


def test_github_toolsets_cover_why_surfaces(cfg):
    # The github toolset must expose the surfaces that carry "why": repo files
    # (README/CLAUDE.md/docs/ADRs/commits), pull requests, and issues. Trimming
    # any of these would silently degrade answers (ADR 0009).
    toolsets = cfg.mcp_servers["github"]["env"]["GITHUB_TOOLSETS"]
    enabled = {t.strip() for t in toolsets.split(",")}
    assert {"repos", "pull_requests", "issues"} <= enabled


def test_agentmemory_server_configured(cfg):
    am = cfg.mcp_servers["agentmemory"]
    assert am["command"] == "npx"
    assert am["env"]["AGENTMEMORY_URL"] == "http://localhost:3111"


def test_allowed_tools_matches_frozen_set(cfg):
    assert cfg.allowed_tools == ALLOWED_TOOLS


def test_default_model(cfg):
    assert cfg.model == DEFAULT_MODEL


def test_system_prompt_names_repo(cfg):
    assert "Wkkkkk/MyTV" in cfg.system_prompt


def test_system_prompt_directs_to_repo_why_surfaces(cfg):
    # ADR 0009: the repo is the source of truth for "why". The prompt must point
    # the agent at the repo-resident surfaces that carry rationale, not leave it
    # to discover them.
    prompt = cfg.system_prompt
    for surface in ("README", "CLAUDE.md", "docs/", "docs/adr"):
        assert surface in prompt, f"prompt does not mention the {surface} surface"


def test_system_prompt_covers_history_surfaces(cfg):
    # The full set of pushed-history surfaces that carry "why": commit messages,
    # PR descriptions, and issues.
    prompt = cfg.system_prompt.lower()
    assert "commit message" in prompt
    assert "pull request" in prompt
    assert "issue" in prompt


def test_system_prompt_frames_agentmemory_as_optional_enrichment(cfg):
    # ADR 0009: agentmemory is optional local enrichment, not a required, co-equal
    # source. The prompt must not promise it carries the "why"; it may be empty.
    prompt = cfg.system_prompt
    assert "agentmemory" in prompt.lower()
    assert "optional" in prompt.lower() or "enrich" in prompt.lower()
