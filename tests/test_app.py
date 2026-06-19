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


def test_build_orchestrator_without_lobby_has_empty_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert orch._catalog == ()            # lobby inert: no catalog built, no network
    assert orch._lobby_store is None


def test_build_orchestrator_with_lobby_builds_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "lobby_channel_id: C0LOBBY\n"
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    calls = []

    def fake_get_json(path):
        calls.append(path)
        return {"description": "desc"}

    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"),
        secrets=load_secrets(ENV), get_json=fake_get_json,
    )
    assert calls == ["/repos/Wkkkkk/MyTV"]            # used injected reader, not the network
    assert len(orch._catalog) == 1
    assert orch._catalog[0].description == "desc"
    assert orch._lobby_store is not None
    assert orch._classify_fn is not None


def test_build_orchestrator_with_subscriptions_builds_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "subscriptions:\n  - channel_id: C900\n    projects: [MyTV]\n"
    )
    calls = []

    def fake_get_json(path):
        calls.append(path)
        return {"description": "desc"}

    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"),
        secrets=load_secrets(ENV), get_json=fake_get_json,
    )
    assert calls == ["/repos/Wkkkkk/MyTV"]            # catalog built even without a lobby
    assert len(orch._catalog) == 1
    assert orch._lobby_store is not None
    assert orch._classify_fn is not None


from babbla.digest.scheduler import ActionScheduler
from babbla.digest.actions import PerProjectDigestAction, SharedDigestAction, QuizAction
from babbla.app import build_scheduler
from babbla.config import load_config


def test_build_scheduler_assembles_actions(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "    quiz:\n      cadence: weekly\n      tz: UTC\n"
        "  - name: Stream\n    owner: Wkkkkk\n    repo: stream\n    visibility: internal\n"
        "    channel_id: C456\n    dm: false\n"
        "subscriptions:\n"
        "  - channel_id: C900\n    projects: [MyTV, Stream]\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert isinstance(sched, ActionScheduler)
    kinds = sorted(type(a).__name__ for a in sched._actions)
    assert kinds == ["PerProjectDigestAction", "QuizAction", "SharedDigestAction"]


def test_build_scheduler_inert_when_nothing_configured(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert sched._actions == ()
