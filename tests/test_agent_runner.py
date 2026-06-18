import pytest

from babbla.agent_runner import AgentRunner, CitedAnswer, Secrets
from babbla.config import ProjectBinding

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
SECRETS = Secrets(github_token="ghp_x", agentmemory_url="http://localhost:3111", agentmemory_secret="")


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
    assert "github" in opts.mcp_servers and "agentmemory" in opts.mcp_servers


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
