import pytest

from babbla.app import build_orchestrator, load_secrets
from babbla.orchestrator import Orchestrator

ENV = {
    "SLACK_BOT_TOKEN": "xoxb-x",
    "SLACK_APP_TOKEN": "xapp-x",
    "GITHUB_TOKEN": "ghp_x",
    "ANTHROPIC_API_KEY": "sk-x",
}


def test_load_secrets_defaults():
    s = load_secrets(ENV)
    assert s.github_token == "ghp_x"
    assert s.agentmemory_url == "http://localhost:3111"
    assert s.agentmemory_secret == ""


def test_load_secrets_missing_required_raises():
    broken = dict(ENV)
    del broken["GITHUB_TOKEN"]
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        load_secrets(broken)


def test_load_secrets_custom_agentmemory():
    env = dict(ENV, AGENTMEMORY_URL="http://localhost:9999", AGENTMEMORY_SECRET="shh")
    s = load_secrets(env)
    assert s.agentmemory_url == "http://localhost:9999"
    assert s.agentmemory_secret == "shh"


def test_build_orchestrator(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert isinstance(orch, Orchestrator)
