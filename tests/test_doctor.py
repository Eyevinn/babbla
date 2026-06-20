from dataclasses import dataclass

from babbla.doctor import RepoCheck, check_access


@dataclass(frozen=True)
class _B:
    name: str
    owner: str
    repo: str


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
