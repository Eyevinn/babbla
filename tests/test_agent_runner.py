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
    assert getattr(opts, "setting_sources", None) in (None, [])
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
