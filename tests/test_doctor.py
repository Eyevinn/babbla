from dataclasses import dataclass

from babbla.doctor import RepoCheck, SkillCheck, check_access, check_skills


@dataclass(frozen=True)
class _B:
    name: str
    owner: str
    repo: str
    skills: tuple = ()


@dataclass(frozen=True)
class _Cfg:
    bindings: tuple


def _cfg(*bindings):
    return _Cfg(bindings=tuple(bindings))


def test_all_reachable():
    cfg = _cfg(_B("MyTV", "Wkkkkk", "MyTV"), _B("Babbla", "Eyevinn", "babbla"))
    paths = []

    def gj(path):
        paths.append(path)
        return {"full_name": path}

    out = check_access(cfg, get_json=gj)
    assert paths == ["/repos/Wkkkkk/MyTV", "/repos/Eyevinn/babbla"]
    assert out == [
        RepoCheck("MyTV", "Wkkkkk/MyTV", True, "ok"),
        RepoCheck("Babbla", "Eyevinn/babbla", True, "ok"),
    ]


def test_404_is_private_scope_hint():
    cfg = _cfg(_B("Secret", "Eyevinn", "secret"))
    out = check_access(cfg, get_json=lambda p: None)
    assert out == [
        RepoCheck("Secret", "Eyevinn/secret", False, "404 (private repo not in token scope?)")
    ]


def test_exception_is_captured_not_raised():
    cfg = _cfg(_B("Boom", "o", "r"))

    def gj(path):
        raise RuntimeError("403 Forbidden")

    out = check_access(cfg, get_json=gj)
    assert out == [RepoCheck("Boom", "o/r", False, "403 Forbidden")]


def test_exception_with_empty_text_falls_back_to_class_name():
    cfg = _cfg(_B("Boom", "o", "r"))

    def gj(path):
        raise RuntimeError("")

    out = check_access(cfg, get_json=gj)
    assert out == [RepoCheck("Boom", "o/r", False, "RuntimeError")]


def test_mixed_results_one_bad_repo_does_not_abort_sweep():
    cfg = _cfg(_B("Ok", "o", "ok"), _B("Gone", "o", "gone"), _B("Err", "o", "err"))

    def gj(path):
        if path.endswith("/ok"):
            return {"x": 1}
        if path.endswith("/gone"):
            return None
        raise RuntimeError("boom")

    out = check_access(cfg, get_json=gj)
    assert [c.reachable for c in out] == [True, False, False]
    assert out[1].detail == "404 (private repo not in token scope?)"
    assert out[2].detail == "boom"


def test_empty_config_is_empty_result():
    assert check_access(_cfg(), get_json=lambda p: {"x": 1}) == []


# ---------------------------------------------------------------------------
# check_skills tests
# ---------------------------------------------------------------------------

def _make_skill(pool, name):
    (pool / name).mkdir(parents=True)
    (pool / name / "SKILL.md").write_text("# skill")


def test_check_skills_present(tmp_path):
    _make_skill(tmp_path, "architecture-diagram")
    cfg = _cfg(_B("Babbla", "Eyevinn", "babbla", skills=("architecture-diagram",)))
    out = check_skills(cfg, skills_pool=str(tmp_path))
    assert out == [SkillCheck("Babbla", "architecture-diagram", True, "ok")]


def test_check_skills_missing_points_at_expected_path(tmp_path):
    cfg = _cfg(_B("Babbla", "Eyevinn", "babbla", skills=("architecture-diagram",)))
    out = check_skills(cfg, skills_pool=str(tmp_path))
    assert len(out) == 1
    c = out[0]
    assert (c.name, c.skill, c.present) == ("Babbla", "architecture-diagram", False)
    assert str(tmp_path / "architecture-diagram" / "SKILL.md") in c.detail


def test_check_skills_dir_without_skill_md_is_missing(tmp_path):
    (tmp_path / "half-baked").mkdir()   # dir exists but no SKILL.md
    cfg = _cfg(_B("P", "o", "r", skills=("half-baked",)))
    out = check_skills(cfg, skills_pool=str(tmp_path))
    assert [(c.skill, c.present) for c in out] == [("half-baked", False)]


def test_check_skills_only_covers_bindings_with_skills(tmp_path):
    cfg = _cfg(
        _B("MyTV", "Wkkkkk", "MyTV"),                               # no skills
        _B("Babbla", "Eyevinn", "babbla", skills=("missing",)),
    )
    out = check_skills(cfg, skills_pool=str(tmp_path))
    assert [c.name for c in out] == ["Babbla"]   # MyTV contributes nothing


def test_check_skills_empty_when_no_skills_anywhere(tmp_path):
    cfg = _cfg(_B("MyTV", "Wkkkkk", "MyTV"))
    assert check_skills(cfg, skills_pool=str(tmp_path)) == []
