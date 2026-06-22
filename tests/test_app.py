import logging

import pytest

from babbla.app import build_orchestrator, load_secrets, run_preflight
from babbla.membership import deny_membership
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


def test_load_secrets_missing_required_raises():
    broken = dict(ENV)
    del broken["GITHUB_TOKEN"]
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        load_secrets(broken)


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


def test_build_orchestrator_always_has_personal_store(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert orch._personal_store is not None
    assert orch._catalog == ()            # plain pilot: still no catalog, no network


def test_build_orchestrator_personal_digest_builds_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "personal_digest:\n  default_cadence: weekly\n  tz: UTC\n"
    )
    calls = []
    def fake_get_json(path):
        calls.append(path)
        return {"description": "desc"}
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"),
        secrets=load_secrets(ENV), get_json=fake_get_json,
    )
    assert calls == ["/repos/Wkkkkk/MyTV"]
    assert len(orch._catalog) == 1
    assert orch._personal_store is not None


def test_build_scheduler_includes_personal_digest(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "personal_digest:\n  default_cadence: weekly\n  tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert "PersonalDigestAction" in [type(a).__name__ for a in sched._actions]


from babbla.digest.scheduler import ActionScheduler
from babbla.digest.actions import PerProjectDigestAction, QuizAction
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
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert isinstance(sched, ActionScheduler)
    kinds = sorted(type(a).__name__ for a in sched._actions)
    assert kinds == ["PerProjectDigestAction", "QuizAction"]


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


from babbla.digest.actions import StalePRAction, AdrDigestAction


def test_build_scheduler_assembles_stale_pr_and_adr(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    stale_prs:\n      cadence: weekly\n      tz: UTC\n"
        "    adr:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    kinds = sorted(type(a).__name__ for a in sched._actions)
    assert kinds == ["AdrDigestAction", "StalePRAction"]


def test_build_scheduler_stale_pr_only(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    stale_prs:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert [type(a).__name__ for a in sched._actions] == ["StalePRAction"]


def test_build_scheduler_inert_includes_no_new_actions(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    names = [type(a).__name__ for a in sched._actions]
    assert "StalePRAction" not in names and "AdrDigestAction" not in names
    assert sched._actions == ()


# ---------------------------------------------------------------------------
# run_preflight tests
# ---------------------------------------------------------------------------

def _cfg_two(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C1\n    dm: true\n"
        "  - name: Secret\n    owner: Eyevinn\n    repo: secret\n"
        "    visibility: private\n    channel_id: C2\n    dm: false\n"
    )
    return load_config(str(cfg))


def test_run_preflight_warns_for_unreachable_and_does_not_raise(tmp_path, caplog):
    config = _cfg_two(tmp_path)

    def gj(path):
        return {"x": 1} if "MyTV" in path else None   # Secret unreachable

    with caplog.at_level(logging.WARNING, logger="babbla.app"):
        checks = run_preflight(config, get_json=gj, env={})

    assert [c.reachable for c in checks] == [True, False]
    assert "Eyevinn/secret" in caplog.text
    assert "MyTV" not in caplog.text   # reachable repos do not warn


def test_run_preflight_skipped_does_not_call_get_json(tmp_path):
    config = _cfg_two(tmp_path)

    def boom(path):
        raise AssertionError("get_json must not be called when skipped")

    assert run_preflight(config, get_json=boom, env={"BABBLA_SKIP_PREFLIGHT": "1"}) is None


def test_run_preflight_swallows_get_json_errors(tmp_path, caplog):
    config = _cfg_two(tmp_path)

    def gj(path):
        raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING, logger="babbla.app"):
        checks = run_preflight(config, get_json=gj, env={})

    assert all(not c.reachable for c in checks)   # nothing raised out of run_preflight


def _cfg_with_skill(tmp_path, skills_subdir="skills"):
    """A config whose Babbla binding references the 'architecture-diagram' skill.

    load_config validates skills against <config dir>/skills, so that pool must
    hold the skill for the config to load at all; the runtime pool checked by
    run_skills_preflight is passed separately.
    """
    pool = tmp_path / skills_subdir / "architecture-diagram"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("# skill")
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n"
        "  - name: Babbla\n    owner: Eyevinn\n    repo: babbla\n"
        "    visibility: public\n    channel_id: C1\n    dm: false\n"
        "    skills: [architecture-diagram]\n"
    )
    return load_config(str(cfg))


def test_run_skills_preflight_warns_for_runtime_pool_mismatch(tmp_path, caplog):
    from babbla.app import run_skills_preflight
    config = _cfg_with_skill(tmp_path)   # skill exists under <config dir>/skills

    # Runtime pool points elsewhere (the exact container bug): empty dir.
    runtime_pool = tmp_path / "elsewhere"
    runtime_pool.mkdir()
    with caplog.at_level(logging.WARNING, logger="babbla.app"):
        checks = run_skills_preflight(config, skills_pool=str(runtime_pool), env={})

    assert [c.present for c in checks] == [False]
    assert "architecture-diagram" in caplog.text
    assert "Babbla" in caplog.text


def test_run_skills_preflight_silent_when_pool_matches(tmp_path, caplog):
    from babbla.app import run_skills_preflight
    config = _cfg_with_skill(tmp_path)
    with caplog.at_level(logging.WARNING, logger="babbla.app"):
        checks = run_skills_preflight(config, skills_pool=str(tmp_path / "skills"), env={})
    assert [c.present for c in checks] == [True]
    assert caplog.text == ""   # present skills do not warn


def test_run_skills_preflight_skipped_returns_none(tmp_path):
    from babbla.app import run_skills_preflight
    config = _cfg_with_skill(tmp_path)
    assert run_skills_preflight(
        config, skills_pool=str(tmp_path / "nope"), env={"BABBLA_SKIP_PREFLIGHT": "1"}
    ) is None


def test_load_secrets_default_skills_pool():
    from babbla.app import load_secrets
    env = {"SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "z"}
    assert load_secrets(env).skills_pool == "config/skills"


def test_load_secrets_skills_pool_override():
    from babbla.app import load_secrets
    env = {"SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "z",
           "BABBLA_SKILLS_POOL": "/srv/pool"}
    assert load_secrets(env).skills_pool == "/srv/pool"


def test_load_secrets_resolves_per_surface_profiles():
    from babbla.app import load_secrets
    env = {
        "SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "g",
        "BABBLA_ASK_EFFORT": "high",
        "BABBLA_CLASSIFIER_MODEL": "claude-haiku-4-5",
    }
    s = load_secrets(env)
    assert s.ask.effort == "high"
    assert s.ask.model == "claude-opus-4-8"          # BABBLA_MODEL default
    assert s.classifier.model == "claude-haiku-4-5"


def test_load_secrets_backcompat_babbla_model():
    from babbla.app import load_secrets
    env = {
        "SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "g",
        "BABBLA_MODEL": "claude-sonnet-4-6",
    }
    s = load_secrets(env)
    assert s.ask.model == "claude-sonnet-4-6"
    assert s.classifier.model == "claude-sonnet-4-6"
    assert s.ask.effort is None                      # inert by default


def test_build_orchestrator_without_client_uses_deny_default(tmp_path):
    # With no Slack client, private stays locked: the orchestrator's oracle is deny_membership.
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert orch._membership is deny_membership
