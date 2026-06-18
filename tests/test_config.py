from pathlib import Path

import logging

import pytest

from babbla.config import Config, ProjectBinding, load_config

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
