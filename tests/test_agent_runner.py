import os

import pytest
from pathlib import Path

from babbla.agent_runner import AgentRunner, Artifact, CitedAnswer, Secrets
from babbla.config import ProjectBinding

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
SECRETS = Secrets(github_token="ghp_x")


class FakeResultMessage:
    def __init__(self, result, session_id):
        self.result = result
        self.session_id = session_id


def make_query_fn(captured, *, result="Because of PR #58 https://github.com/Wkkkkk/MyTV/pull/58",
                  session_id="sess-new"):
    async def fake_query(prompt, options=None):
        captured["prompt"] = prompt
        captured["options"] = options
        yield FakeResultMessage(result=result, session_id=session_id)
    return fake_query


async def test_run_ask_returns_cited_answer():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    ans = await runner.run_ask("why pr 58?", BINDING, resume_session_id=None)
    assert isinstance(ans, CitedAnswer)
    assert "PR #58" in ans.text
    assert ans.session_id == "sess-new"


async def test_run_ask_passes_readonly_options():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    opts = captured["options"]
    assert opts.permission_mode == "dontAsk"
    assert opts.permission_mode != "bypassPermissions"
    assert all(t.startswith("mcp__") for t in opts.allowed_tools)
    assert "github" in opts.mcp_servers and "agentmemory" not in opts.mcp_servers


async def test_run_ask_new_session_has_no_resume():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    # resume is None/unset for a brand-new session
    assert getattr(captured["options"], "resume", None) in (None, "")


async def test_run_ask_resume_sets_session():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured, session_id="sess-resumed"))
    ans = await runner.run_ask("follow up", BINDING, resume_session_id="sess-old")
    assert captured["options"].resume == "sess-old"
    assert ans.session_id == "sess-resumed"


async def test_no_answer_fallback_names_the_bound_project():
    # When the agent yields no text, the fallback must name the bound project,
    # not a hardcoded "MyTV" — onboarding a second project depends on this.
    other = ProjectBinding("Acme", "acme-org", "acme", "public", "C999", False)
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured, result=None))
    ans = await runner.run_ask("anything?", other, resume_session_id=None)
    assert "Acme" in ans.text
    assert "MyTV" not in ans.text


def _pool_with(tmp_path, name="architecture-diagram"):
    d = tmp_path / "pool" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n" % name)
    return str(tmp_path / "pool")


SKILLED_BINDING = ProjectBinding(
    "MyTV", "Wkkkkk", "MyTV", "public", "C123", True, skills=("architecture-diagram",)
)


async def test_unskilled_options_have_no_scratch_or_skills():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    opts = captured["options"]
    assert getattr(opts, "cwd", None) is None
    assert not getattr(opts, "skills", None)
    assert opts.setting_sources == []  # isolation, NOT None (which loads host settings)
    assert "Write" not in opts.allowed_tools


async def test_skilled_options_carry_scratch_skills_and_hook(tmp_path):
    captured = {}
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    opts = captured["options"]
    assert opts.skills == ["architecture-diagram"]
    assert opts.setting_sources == ["project"]
    assert opts.cwd                                    # the per-thread scratch dir
    assert "PreToolUse" in (opts.hooks or {})          # scratch guard wired
    # Builtins are hook-gated, NOT allow-listed (read-only posture preserved).
    assert "Write" not in opts.allowed_tools and "Bash" not in opts.allowed_tools


async def test_skilled_cwd_is_stable_per_scratch_key(tmp_path):
    # Resume needs the same cwd across a thread's turns. Same key -> same path;
    # different key -> different path.
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))
    a, b, c = {}, {}, {}
    await AgentRunner(secrets, query_fn=make_query_fn(a)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-A")
    await AgentRunner(secrets, query_fn=make_query_fn(b)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-A")
    await AgentRunner(secrets, query_fn=make_query_fn(c)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-B")
    assert a["options"].cwd == b["options"].cwd        # stable across turns
    assert a["options"].cwd != c["options"].cwd        # distinct per thread


async def test_skilled_branch_skipped_without_scratch_key(tmp_path):
    # A skilled binding with NO scratch_key (e.g. the digest path) must take the
    # plain branch — never load skills or a scratch.
    captured = {}
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("digest", SKILLED_BINDING, resume_session_id=None)  # no scratch_key
    opts = captured["options"]
    assert getattr(opts, "cwd", None) is None
    assert not getattr(opts, "skills", None)


async def test_skilled_scratch_is_removed_after_run(tmp_path):
    captured = {}
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert not Path(captured["options"].cwd).exists()  # wiped in finally


async def test_skilled_scratch_removed_even_on_exception(tmp_path):
    from babbla.agent_runner import _scratch_path
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))

    async def boom(prompt, options=None):
        raise RuntimeError("agent died")
        yield  # pragma: no cover

    runner = AgentRunner(secrets, query_fn=boom)
    with pytest.raises(RuntimeError):
        await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert not Path(_scratch_path("t1")).exists()


async def test_skilled_captures_artifacts(tmp_path):
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))

    async def writing_query(prompt, options=None):
        # Simulate a skill writing an artifact into the scratch cwd.
        (Path(options.cwd) / "architecture.html").write_text("<svg/>")
        yield FakeResultMessage(result="drew it", session_id="s1")

    runner = AgentRunner(secrets, query_fn=writing_query)
    ans = await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert ans.text == "drew it"
    assert Artifact("architecture.html", b"<svg/>") in ans.artifacts


# --- Plain-path runtime confinement (incident 2026-06-20 remediation) -----------
# These assert what the runner ACTUALLY sends to query() — the runtime gap the
# old config-only tests (build_agent_config asserts) never covered. The leak was:
# the plain path left setting_sources unset, so the CLI loaded the operator's
# ~/.claude settings and their allow-rules pre-approved Bash/Read/Write/etc.


async def test_plain_path_isolates_filesystem_settings():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    opts = captured["options"]
    # [] = SDK isolation mode: do NOT load ~/.claude/settings.json (user) etc.
    # None would mean "load all filesystem settings" — the bug.
    assert opts.setting_sources == []


async def test_plain_path_uses_strict_mcp_config():
    # Only the github MCP server we pass is used; the operator's connected
    # claude.ai MCP integrations (settings-based) are ignored.
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    assert captured["options"].strict_mcp_config is True


async def test_plain_path_installs_deny_by_default_hook():
    # Independent enforcement layer (ADR 0003): a PreToolUse guard denies any
    # non-github tool even if the permission layer would have allowed it.
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    assert "PreToolUse" in (captured["options"].hooks or {})


async def test_skilled_path_also_uses_strict_mcp_config(tmp_path):
    captured = {}
    secrets = Secrets(github_token="g", skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert captured["options"].strict_mcp_config is True
    # skilled keeps project-only settings (excludes the operator's user settings)
    assert captured["options"].setting_sources == ["project"]


# --- Opt-in end-to-end enforcement smoke test -----------------------------------
# Launches the REAL bundled CLI and confirms a non-github builtin is actually
# denied at runtime (not just named in config). Needs working Claude auth + the
# CLI, so it is gated behind BABBLA_E2E=1 and skipped by default.

E2E_SENTINEL = "BABBLA_BASH_ESCAPE_7Q2"


@pytest.mark.skipif(os.environ.get("BABBLA_E2E") != "1", reason="set BABBLA_E2E=1 to run live CLI test")
async def test_e2e_plain_path_denies_bash():
    runner = AgentRunner(SECRETS)  # real _sdk_query
    ans = await runner.run_ask(
        f"Run the shell command `echo {E2E_SENTINEL}` using your Bash tool and "
        f"reply with its exact output. If you cannot, say DENIED.",
        BINDING,
        resume_session_id=None,
    )
    # Bash is denied -> the command never runs -> its output never appears.
    assert E2E_SENTINEL not in ans.text
