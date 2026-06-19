from pathlib import Path

import logging

import pytest

from babbla.config import Config, ProjectBinding, load_config, SubscriptionDigest, QuizConfig, PersonalDigestConfig

FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "channels.yaml"
    p.write_text(text)
    return p


def test_loads_binding(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings == (
        ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True),
    )


def test_for_channel_matches(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.for_channel("C123").name == "MyTV"
    assert cfg.for_channel("CNOPE") is None


def test_for_dm_returns_dm_project(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.for_dm().name == "MyTV"


def test_null_channel_id_is_none(tmp_path):
    text = FIXTURE.replace("channel_id: C123", "channel_id: null")
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].channel_id is None
    assert cfg.for_channel("C123") is None
    assert cfg.for_dm().name == "MyTV"


def test_rejects_multiple_dm_projects(tmp_path):
    text = FIXTURE + """  - name: Other
    owner: o
    repo: r
    visibility: public
    channel_id: C999
    dm: true
"""
    with pytest.raises(ValueError, match="exactly one"):
        load_config(_write(tmp_path, text))


def test_private_dm_logs_warning_but_loads(tmp_path, caplog):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n"
        "  - name: Secret\n"
        "    owner: Wkkkkk\n"
        "    repo: Secret\n"
        "    visibility: private\n"
        "    channel_id: C777\n"
        "    dm: true\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="babbla.config"):
        config = load_config(cfg)
    assert config.bindings[0].name == "Secret"     # load succeeded
    assert any("private" in r.message and "dm" in r.message for r in caplog.records)


def test_lobby_channel_id_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE + "lobby_channel_id: C0LOBBY\n"))
    assert cfg.lobby_channel_id == "C0LOBBY"


def test_lobby_channel_id_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.lobby_channel_id is None


SUBS_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
  - name: Stream
    owner: Wkkkkk
    repo: stream-starter
    visibility: internal
    channel_id: C456
    dm: false
subscriptions:
  - channel_id: C900
    projects: [MyTV, Stream]
"""


def test_subscriptions_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_FIXTURE))
    assert len(cfg.subscriptions) == 1
    sub = cfg.subscriptions[0]
    assert sub.channel_id == "C900"
    assert sub.project_names == ("MyTV", "Stream")


def test_subscription_for_hit_and_miss(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_FIXTURE))
    assert cfg.subscription_for("C900").project_names == ("MyTV", "Stream")
    assert cfg.subscription_for("CNOPE") is None


def test_subscriptions_absent_is_empty(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.subscriptions == ()


def test_subscription_unknown_project_raises(tmp_path):
    text = SUBS_FIXTURE.replace("projects: [MyTV, Stream]", "projects: [MyTV, Ghost]")
    with pytest.raises(ValueError, match="unknown project"):
        load_config(_write(tmp_path, text))


def test_subscription_empty_projects_raises(tmp_path):
    text = SUBS_FIXTURE.replace("projects: [MyTV, Stream]", "projects: []")
    with pytest.raises(ValueError, match="at least one project"):
        load_config(_write(tmp_path, text))


def test_subscription_duplicate_channel_raises(tmp_path):
    text = SUBS_FIXTURE + "  - channel_id: C900\n    projects: [MyTV]\n"
    with pytest.raises(ValueError, match="more than one subscription"):
        load_config(_write(tmp_path, text))


def test_subscription_collides_with_lobby_warns(tmp_path, caplog):
    text = SUBS_FIXTURE + "lobby_channel_id: C900\n"
    with caplog.at_level(logging.WARNING, logger="babbla.config"):
        cfg = load_config(_write(tmp_path, text))
    assert cfg.subscription_for("C900") is not None      # still loads
    assert any("shadowed" in r.message for r in caplog.records)


SUBS_DIGEST_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
subscriptions:
  - channel_id: C900
    projects: [MyTV]
    digest:
      cadence: weekly
      tz: Europe/Stockholm
"""


def test_subscription_digest_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_DIGEST_FIXTURE))
    sub = cfg.subscription_for("C900")
    assert sub.digest == SubscriptionDigest(cadence="weekly", tz="Europe/Stockholm")


def test_subscription_without_digest_is_none(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace(
        "    digest:\n      cadence: weekly\n      tz: Europe/Stockholm\n", ""
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.subscription_for("C900").digest is None


def test_digest_subscriptions_filters(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_DIGEST_FIXTURE))
    assert tuple(s.channel_id for s in cfg.digest_subscriptions()) == ("C900",)


def test_subscription_digest_bad_cadence_raises(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace("cadence: weekly", "cadence: hourly")
    with pytest.raises(ValueError, match="digest.cadence"):
        load_config(_write(tmp_path, text))


def test_subscription_digest_bad_tz_raises(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace("tz: Europe/Stockholm", "tz: Mars/Phobos")
    with pytest.raises(ValueError, match="time zone"):
        load_config(_write(tmp_path, text))


QUIZ_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    quiz:
      cadence: weekly
      tz: Europe/Stockholm
      count: 5
"""


def test_quiz_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, QUIZ_FIXTURE))
    assert cfg.bindings[0].quiz == QuizConfig(cadence="weekly", tz="Europe/Stockholm", count=5)


def test_quiz_count_defaults_to_three(tmp_path):
    text = QUIZ_FIXTURE.replace("      count: 5\n", "")
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].quiz.count == 3


def test_quiz_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].quiz is None


def test_quiz_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, QUIZ_FIXTURE))
    assert tuple(b.name for b in cfg.quiz_bindings()) == ("MyTV",)
    text = QUIZ_FIXTURE.replace("channel_id: C123", "channel_id: null")
    cfg2 = load_config(_write(tmp_path, text))
    assert cfg2.quiz_bindings() == ()          # no channel to post to


def test_quiz_bad_count_raises(tmp_path):
    text = QUIZ_FIXTURE.replace("count: 5", "count: 0")
    with pytest.raises(ValueError, match="quiz.count"):
        load_config(_write(tmp_path, text))


_PROJECT = (
    "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n"
    "    visibility: public\n    channel_id: C1\n    dm: true\n"
)


def test_personal_digest_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, _PROJECT))
    assert cfg.personal_digest is None


def test_personal_digest_parses(tmp_path):
    body = _PROJECT + "personal_digest:\n  default_cadence: daily\n  tz: Europe/Stockholm\n"
    cfg = load_config(_write(tmp_path, body))
    assert cfg.personal_digest == PersonalDigestConfig(default_cadence="daily", tz="Europe/Stockholm")


def test_personal_digest_invalid_cadence_raises(tmp_path):
    body = _PROJECT + "personal_digest:\n  default_cadence: hourly\n  tz: UTC\n"
    with pytest.raises(ValueError, match="default_cadence"):
        load_config(_write(tmp_path, body))


def test_personal_digest_invalid_tz_raises(tmp_path):
    body = _PROJECT + "personal_digest:\n  default_cadence: weekly\n  tz: Mars/Phobos\n"
    with pytest.raises(ValueError, match="time zone"):
        load_config(_write(tmp_path, body))
