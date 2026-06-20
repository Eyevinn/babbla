import pytest

from babbla.read_only import (
    AGENTMEMORY_READERS,
    AGENTMEMORY_WRITERS,
    ALLOWED_TOOLS,
    DEFAULT_MODEL,
    GITHUB_WILDCARD,
    build_agent_config,
)
from babbla.read_only import _within, make_scratch_guard, skill_loading_kwargs

FORBIDDEN_BUILTINS = ("Bash", "Write", "Edit", "Read", "NotebookEdit", "WebFetch", "WebSearch")


def _cfg(**over):
    args = dict(
        owner="Wkkkkk", repo="MyTV", github_token="ghp_dummy",
        agentmemory_url="http://localhost:3111", agentmemory_secret="",
    )
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


def test_no_agentmemory_writer_allowlisted(cfg):
    for writer in AGENTMEMORY_WRITERS:
        assert writer not in cfg.allowed_tools


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


def test_agentmemory_present_when_configured(cfg):
    am_tools = [t for t in cfg.allowed_tools if t.startswith("mcp__agentmemory__")]
    assert set(am_tools) == set(AGENTMEMORY_READERS)
    assert "agentmemory" in cfg.mcp_servers


def test_agentmemory_omitted_when_url_empty():
    cfg = _cfg(agentmemory_url="")
    assert "agentmemory" not in cfg.mcp_servers
    assert cfg.allowed_tools == (GITHUB_WILDCARD,)
    for t in cfg.allowed_tools:
        assert not t.startswith("mcp__agentmemory__")


def test_allowed_tools_matches_frozen_set_when_agentmemory_on(cfg):
    assert cfg.allowed_tools == ALLOWED_TOOLS


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


def test_system_prompt_frames_agentmemory_as_optional_enrichment(cfg):
    prompt = cfg.system_prompt
    assert "agentmemory" in prompt.lower()
    assert "optional" in prompt.lower() or "enrich" in prompt.lower()


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


def test_skilled_build_keeps_readers_only_allowed_tools():
    cfg = _cfg(skills=("architecture-diagram",))
    assert cfg.skills == ("architecture-diagram",)
    assert cfg.allowed_tools == ALLOWED_TOOLS         # builtins NOT allow-listed
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools
    for writer in AGENTMEMORY_WRITERS:
        assert writer not in cfg.allowed_tools


def test_skill_loading_kwargs_shape(tmp_path):
    kw = skill_loading_kwargs(scratch_dir=str(tmp_path), skills=("a", "b"))
    assert kw["cwd"] == str(tmp_path)
    assert kw["setting_sources"] == ["project"]
    assert kw["skills"] == ["a", "b"]
    assert "PreToolUse" in kw["hooks"]
