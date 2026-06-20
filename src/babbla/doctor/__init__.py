from __future__ import annotations

from dataclasses import dataclass

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
