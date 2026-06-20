from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_NOT_IN_SCOPE = "404 (private repo not in token scope?)"


@dataclass(frozen=True)
class RepoCheck:
    name: str   # project name
    slug: str   # owner/repo
    reachable: bool
    detail: str   # "ok" | "404 (private repo not in token scope?)" | "<error>"


def check_access(config, *, get_json) -> list[RepoCheck]:
    """For each binding, GET /repos/{owner}/{repo}; classify reachable vs not.

    Pure over an injected ``get_json`` (no network in tests). ``get_json``
    returns parsed JSON, ``None`` on a 404, and raises on other failures; a
    raise on one binding never aborts the sweep.
    """
    checks: list[RepoCheck] = []
    for b in config.bindings:
        slug = f"{b.owner}/{b.repo}"
        try:
            data = get_json(f"/repos/{b.owner}/{b.repo}")
        except Exception as exc:   # auth/network — capture, do not propagate
            checks.append(RepoCheck(b.name, slug, False, str(exc) or type(exc).__name__))
            continue
        # get_json returns the repo JSON object on success and None only on a
        # 404 (its sole non-success sentinel for this endpoint), so reachability
        # keys off that sentinel rather than the response's shape.
        if data is not None:
            checks.append(RepoCheck(b.name, slug, True, "ok"))
        else:
            checks.append(RepoCheck(b.name, slug, False, _NOT_IN_SCOPE))
    return checks


@dataclass(frozen=True)
class SkillCheck:
    name: str    # project name
    skill: str   # skill name from the binding
    present: bool
    detail: str  # "ok" | "missing (no <pool>/<skill>/SKILL.md)"


def check_skills(config, *, skills_pool) -> list[SkillCheck]:
    """For each binding skill, check it is stageable from ``skills_pool``.

    A skill is stageable when ``<skills_pool>/<skill>/SKILL.md`` is a file —
    the same path ``agent_runner._stage_skills`` copies from. ``load_config``
    already validates skills against the *config-dir* pool and raises; this
    checks the *runtime* pool so a deploy/mount mismatch (the two pools
    diverging) surfaces at boot rather than as a silent ask-time failure.
    """
    pool = Path(skills_pool)
    checks: list[SkillCheck] = []
    for b in config.bindings:
        for skill in getattr(b, "skills", ()):
            md = pool / skill / "SKILL.md"
            if md.is_file():
                checks.append(SkillCheck(b.name, skill, True, "ok"))
            else:
                checks.append(SkillCheck(b.name, skill, False, f"missing (no {md})"))
    return checks
