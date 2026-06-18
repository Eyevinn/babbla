import os
import re

import pytest

from babbla.agent_runner import AgentRunner, Secrets
from babbla.config import ProjectBinding

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", None, True)

CITATION_RE = re.compile(r"github\.com/Wkkkkk/MyTV/(commit|pull|blob)/", re.IGNORECASE)


@pytest.mark.integration
async def test_live_ask_cites_a_source():
    # Claude auth comes from the Claude Code CLI subscription login (Path B), so
    # only GITHUB_TOKEN is gated here; set ANTHROPIC_API_KEY to use an API key.
    if not os.environ.get("GITHUB_TOKEN"):
        pytest.skip("integration test needs GITHUB_TOKEN (Claude auth via CLI login)")
    secrets = Secrets(
        github_token=os.environ["GITHUB_TOKEN"],
        agentmemory_url=os.environ.get("AGENTMEMORY_URL", "http://localhost:3111"),
        agentmemory_secret=os.environ.get("AGENTMEMORY_SECRET", ""),
    )
    runner = AgentRunner(secrets)
    answer = await runner.run_ask(
        "What does the MyTV repository do? Cite a specific file or commit.",
        BINDING,
        resume_session_id=None,
    )
    assert answer.text  # non-empty
    assert CITATION_RE.search(answer.text), f"answer carried no GitHub citation:\n{answer.text}"
