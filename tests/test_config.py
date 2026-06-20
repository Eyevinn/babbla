from pathlib import Path

import logging

import pytest

from babbla.config import (
    Config, ProjectBinding, load_config, QuizConfig, PersonalDigestConfig,
    StalePRConfig, AdrConfig,
)

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


STALE_PR_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    stale_prs:
      cadence: weekly
      tz: Europe/Stockholm
      threshold_days: 21
      include_drafts: true
"""

ADR_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    adr:
      cadence: weekly
      tz: Europe/Stockholm
"""


def test_stale_prs_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, STALE_PR_FIXTURE))
    assert cfg.bindings[0].stale_prs == StalePRConfig(
        cadence="weekly", tz="Europe/Stockholm", threshold_days=21, include_drafts=True
    )


def test_stale_prs_defaults(tmp_path):
    text = STALE_PR_FIXTURE.replace("      threshold_days: 21\n", "").replace(
        "      include_drafts: true\n", ""
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].stale_prs.threshold_days == 14
    assert cfg.bindings[0].stale_prs.include_drafts is False


def test_stale_prs_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].stale_prs is None


def test_stale_prs_bad_threshold_raises(tmp_path):
    text = STALE_PR_FIXTURE.replace("threshold_days: 21", "threshold_days: 0")
    with pytest.raises(ValueError, match="threshold_days"):
        load_config(_write(tmp_path, text))


def test_stale_pr_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, STALE_PR_FIXTURE))
    assert tuple(b.name for b in cfg.stale_pr_bindings()) == ("MyTV",)
    text = STALE_PR_FIXTURE.replace("channel_id: C123", "channel_id: null")
    assert load_config(_write(tmp_path, text)).stale_pr_bindings() == ()


def test_adr_parsed_with_dir_default(tmp_path):
    cfg = load_config(_write(tmp_path, ADR_FIXTURE))
    assert cfg.bindings[0].adr == AdrConfig(cadence="weekly", tz="Europe/Stockholm", dir="docs/adr")


def test_adr_custom_dir(tmp_path):
    text = ADR_FIXTURE.replace(
        "      tz: Europe/Stockholm\n",
        "      tz: Europe/Stockholm\n      dir: documentation/decisions\n",
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].adr.dir == "documentation/decisions"


def test_adr_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].adr is None


def test_adr_bad_cadence_raises(tmp_path):
    text = ADR_FIXTURE.replace("cadence: weekly", "cadence: hourly")
    with pytest.raises(ValueError, match="adr.cadence"):
        load_config(_write(tmp_path, text))


def test_adr_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, ADR_FIXTURE))
    assert tuple(b.name for b in cfg.adr_bindings()) == ("MyTV",)
    text = ADR_FIXTURE.replace("channel_id: C123", "channel_id: null")
    assert load_config(_write(tmp_path, text)).adr_bindings() == ()


def _make_pool(tmp_path: Path, *names: str) -> None:
    for n in names:
        d = tmp_path / "skills" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n" % n)


def test_skills_parse_to_tuple(tmp_path):
    _make_pool(tmp_path, "architecture-diagram")
    text = FIXTURE + "    skills:\n      - architecture-diagram\n"
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].skills == ("architecture-diagram",)


def test_skills_absent_is_empty_tuple(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].skills == ()


def test_unknown_skill_raises(tmp_path):
    text = FIXTURE + "    skills:\n      - nope\n"
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        load_config(_write(tmp_path, text))
