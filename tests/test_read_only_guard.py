import pytest

from babbla.read_only import (
    ALLOWED_TOOLS,
    DEFAULT_MODEL,
    GITHUB_WILDCARD,
    build_agent_config,
)
from babbla.read_only import _within, make_scratch_guard, skill_loading_kwargs

FORBIDDEN_BUILTINS = ("Bash", "Write", "Edit", "Read", "NotebookEdit", "WebFetch", "WebSearch")


def _cfg(**over):
    args = dict(owner="Wkkkkk", repo="MyTV", github_token="ghp_dummy")
    args.update(over)
    return build_agent_config(**args)


@pytest.fixture
def cfg():
    return _cfg()


def test_permission_mode_is_dontask(cfg):
    assert cfg.permission_mode == "dontAsk"


def test_permission_mode_never_bypass(cfg):
    assert cfg.permission_mode != "bypassPermissions"


def test_only_mcp_tools_allowed(cfg):
    for tool in cfg.allowed_tools:
        assert tool.startswith("mcp__"), f"non-MCP tool allowlisted: {tool}"
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools


def test_no_agentmemory_tool_anywhere(cfg):
    # agentmemory was removed entirely (ADR 0016): no agentmemory MCP server,
    # and nothing under its tool namespace is ever allow-listed.
    assert "agentmemory" not in cfg.mcp_servers
    for tool in cfg.allowed_tools:
        assert not tool.startswith("mcp__agentmemory__"), tool


@pytest.mark.parametrize("launcher", ["docker", "binary"])
def test_github_server_is_readonly_regardless_of_launcher(launcher):
    # The read-only guarantee is the transport + flags, not the launcher.
    gh = _cfg(github_launcher=launcher).mcp_servers["github"]
    assert "stdio" in gh["args"]
    assert "http" not in gh["args"]
    assert gh["env"]["GITHUB_READ_ONLY"] == "1"
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_dummy"
    assert gh["command"] in ("docker", "github-mcp-server")


def test_docker_launcher_shape():
    gh = _cfg(github_launcher="docker").mcp_servers["github"]
    assert gh["command"] == "docker"


def test_binary_launcher_shape():
    gh = _cfg(github_launcher="binary").mcp_servers["github"]
    assert gh["command"] == "github-mcp-server"
    assert gh["args"] == ["stdio"]


def test_github_toolsets_cover_why_surfaces(cfg):
    enabled = {t.strip() for t in cfg.mcp_servers["github"]["env"]["GITHUB_TOOLSETS"].split(",")}
    assert {"repos", "pull_requests", "issues"} <= enabled


def test_allowed_tools_is_github_only(cfg):
    assert cfg.allowed_tools == ALLOWED_TOOLS == (GITHUB_WILDCARD,)


def test_default_model(cfg):
    assert cfg.model == DEFAULT_MODEL


def test_system_prompt_names_repo(cfg):
    assert "Wkkkkk/MyTV" in cfg.system_prompt


def test_system_prompt_directs_to_repo_why_surfaces(cfg):
    prompt = cfg.system_prompt
    for surface in ("README", "CLAUDE.md", "docs/", "docs/adr"):
        assert surface in prompt


def test_system_prompt_covers_history_surfaces(cfg):
    prompt = cfg.system_prompt.lower()
    assert "commit message" in prompt
    assert "pull request" in prompt
    assert "issue" in prompt


def test_system_prompt_does_not_mention_agentmemory(cfg):
    assert "agentmemory" not in cfg.system_prompt.lower()


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def test_within_absolute_and_relative(tmp_path):
    root = str(tmp_path)
    assert _within(str(tmp_path / "architecture.html"), root)
    assert _within("architecture.html", root)        # relative -> resolved against root
    assert _within("sub/x.md", root)
    assert not _within("/etc/passwd", root)
    assert not _within("../escape.txt", root)
    assert not _within("", root)


async def test_guard_allows_in_scratch_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.html")}}, None, {}
    )
    assert _decision(out) == "allow"


async def test_guard_allows_relative_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": "a.html"}}, None, {}
    )
    assert _decision(out) == "allow"


async def test_guard_denies_out_of_scratch_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/evil.txt"}}, None, {}
    )
    assert _decision(out) == "deny"


async def test_guard_denies_bash(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}, None, {})
    assert _decision(out) == "deny"


async def test_guard_ignores_mcp_tools(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard({"tool_name": "mcp__github__search_code", "tool_input": {}}, None, {})
    assert out == {}  # no opinion -> governed by allowed_tools + dontAsk


def test_skilled_build_keeps_github_only_allowed_tools():
    cfg = _cfg(skills=("architecture-diagram",))
    assert cfg.skills == ("architecture-diagram",)
    assert cfg.allowed_tools == ALLOWED_TOOLS == (GITHUB_WILDCARD,)  # builtins NOT allow-listed
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools


def test_skill_loading_kwargs_shape(tmp_path):
    kw = skill_loading_kwargs(scratch_dir=str(tmp_path), skills=("a", "b"))
    assert kw["cwd"] == str(tmp_path)
    assert kw["setting_sources"] == ["project"]
    assert kw["skills"] == ["a", "b"]
    assert "PreToolUse" in kw["hooks"]
