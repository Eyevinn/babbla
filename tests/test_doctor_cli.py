from babbla.doctor.__main__ import main

_CFG = (
    "projects:\n"
    "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
    "    visibility: public\n    channel_id: C1\n    dm: true\n"
    "  - name: Secret\n    owner: Eyevinn\n    repo: secret\n"
    "    visibility: private\n    channel_id: C2\n    dm: false\n"
)


def _write_cfg(tmp_path):
    p = tmp_path / "channels.yaml"
    p.write_text(_CFG)
    return str(p)


def test_all_reachable_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg(tmp_path))
    code = main([], get_json=lambda path: {"full_name": path})
    out = capsys.readouterr().out
    assert code == 0
    assert "MyTV" in out and "Wkkkkk/MyTV" in out
    assert "ok" in out


def test_any_unreachable_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg(tmp_path))

    def gj(path):
        return {"x": 1} if "MyTV" in path else None   # Secret is a 404

    code = main([], get_json=gj)
    out = capsys.readouterr().out
    assert code == 1
    assert "Eyevinn/secret" in out
    assert "not in token scope" in out


def test_missing_token_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg(tmp_path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main([])   # no injected get_json, no token
    err = capsys.readouterr().err
    assert code == 2
    assert "GITHUB_TOKEN" in err


def test_missing_token_exits_two_even_without_config(tmp_path, monkeypatch, capsys):
    # Token is guarded before load_config, so a missing token returns a clean
    # exit 2 rather than a load_config stack trace when the config is also absent.
    monkeypatch.setenv("BABBLA_CONFIG", str(tmp_path / "does-not-exist.yaml"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main([])
    err = capsys.readouterr().err
    assert code == 2
    assert "GITHUB_TOKEN" in err


def _write_cfg_with_skill(tmp_path):
    pool = tmp_path / "skills" / "architecture-diagram"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("# skill")
    p = tmp_path / "channels.yaml"
    p.write_text(
        "projects:\n"
        "  - name: Babbla\n    owner: Eyevinn\n    repo: babbla\n"
        "    visibility: public\n    channel_id: C1\n    dm: false\n"
        "    skills: [architecture-diagram]\n"
    )
    return str(p)


def test_skill_present_reported_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg_with_skill(tmp_path))
    monkeypatch.setenv("BABBLA_SKILLS_POOL", str(tmp_path / "skills"))
    code = main([], get_json=lambda path: {"full_name": path})
    out = capsys.readouterr().out
    assert code == 0
    assert "architecture-diagram" in out


def test_skill_missing_from_runtime_pool_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg_with_skill(tmp_path))
    # Runtime pool diverges from the config-dir pool — repos all fine, skill not.
    monkeypatch.setenv("BABBLA_SKILLS_POOL", str(tmp_path / "elsewhere"))
    code = main([], get_json=lambda path: {"full_name": path})
    out = capsys.readouterr().out
    assert code == 1
    assert "architecture-diagram" in out
    assert "MISSING" in out


def test_doctor_prints_resolved_tiers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("BABBLA_CLASSIFIER_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("BABBLA_ASK_EFFORT", "high")
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg(tmp_path))
    code = main([], get_json=lambda path: {"full_name": path})
    out = capsys.readouterr().out
    assert "Ask tier" in out and "claude-opus-4-8" in out and "effort=high" in out
    assert "Classifier tier" in out and "claude-haiku-4-5" in out


def test_doctor_rejects_bad_effort(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("BABBLA_ASK_EFFORT", "turbo")
    monkeypatch.setenv("BABBLA_CONFIG", _write_cfg(tmp_path))
    code = main([], get_json=lambda path: {"full_name": path})
    assert code == 2
    assert "EFFORT" in capsys.readouterr().err
