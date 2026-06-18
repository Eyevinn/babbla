from __future__ import annotations

import re
from dataclasses import dataclass

from babbla.audit.github_reader import CommitMsg, RepoFacts

OK = "ok"
THIN = "thin"
MISSING = "missing"

# --- Tunable thresholds (one place to change) ---
README_OK_BYTES = 500           # strictly greater than this is OK
PR_BODY_MIN_CHARS = 80          # a PR body longer than this counts as "descriptive"
PR_BODY_OK_RATIO = 0.5
COMMIT_OK_RATIO = 0.5
COMMIT_MIN_FIRST_LINE = 15
RECENT_WINDOW = 20
_JUNK_RE = re.compile(r"^(wip|fix|update|stuff|\.|\.\.)$", re.IGNORECASE)

# Recommendation lines for thin/missing surfaces (point at the advisory guide).
_RECS = {
    "README": "Add or expand the README — see docs/RECOMMENDATIONS.md §2.",
    "docs/adr/": "Record notable decisions as ADRs — see docs/RECOMMENDATIONS.md §3.",
    "PR bodies": "Write descriptive PR bodies — see docs/RECOMMENDATIONS.md §1.",
    "commit messages": "Write descriptive commit messages — see docs/RECOMMENDATIONS.md §1.",
    "docs/": "Keep project docs under docs/ — see docs/RECOMMENDATIONS.md §2.",
}


@dataclass(frozen=True)
class SurfaceFinding:
    name: str
    status: str
    detail: str
    recommendation: str | None


def _rec(name: str, status: str) -> str | None:
    return _RECS.get(name) if status in (THIN, MISSING) else None


def _finding(name: str, status: str, detail: str) -> SurfaceFinding:
    return SurfaceFinding(name=name, status=status, detail=detail, recommendation=_rec(name, status))


def _is_descriptive(c: CommitMsg) -> bool:
    line = c.first_line.strip()
    return c.has_body or (len(line) >= COMMIT_MIN_FIRST_LINE and not _JUNK_RE.match(line))


def _ratio_status(hits: int, total: int, ratio: float) -> str:
    if total == 0 or hits == 0:
        return MISSING
    return OK if (hits / total) >= ratio else THIN


def surface_findings(facts: RepoFacts) -> tuple[SurfaceFinding, ...]:
    out: list[SurfaceFinding] = []

    # README
    if facts.readme_bytes is None:
        out.append(_finding("README", MISSING, "absent"))
    elif facts.readme_bytes > README_OK_BYTES:
        out.append(_finding("README", OK, f"{facts.readme_bytes / 1024:.1f} KB"))
    else:
        out.append(_finding("README", THIN, f"{facts.readme_bytes} bytes"))

    # CLAUDE.md (informational; OK or MISSING only, never penalized in verdict)
    out.append(
        _finding("CLAUDE.md", OK, "present") if facts.has_claude_md
        else _finding("CLAUDE.md", MISSING, "absent")
    )

    # docs/
    if facts.docs_file_count >= 1:
        out.append(_finding("docs/", OK, f"{facts.docs_file_count} files"))
    else:
        out.append(_finding("docs/", MISSING, "absent/empty"))

    # docs/adr/
    if facts.adr_count >= 1:
        out.append(_finding("docs/adr/", OK, f"{facts.adr_count} ADRs"))
    elif facts.docs_adr_dir_exists:
        out.append(_finding("docs/adr/", THIN, "dir present, 0 ADRs"))
    else:
        out.append(_finding("docs/adr/", MISSING, "absent"))

    # PR bodies
    recent_prs = facts.pr_bodies[:RECENT_WINDOW]
    total_pr = len(recent_prs)
    good_pr = sum(1 for p in recent_prs if p.length > PR_BODY_MIN_CHARS)
    out.append(_finding(
        "PR bodies", _ratio_status(good_pr, total_pr, PR_BODY_OK_RATIO),
        f"{good_pr}/{total_pr} recent PRs have descriptive bodies" if total_pr else "no merged PRs",
    ))

    # commit messages
    recent_commits = facts.commits[:RECENT_WINDOW]
    total_c = len(recent_commits)
    good_c = sum(1 for c in recent_commits if _is_descriptive(c))
    out.append(_finding(
        "commit messages", _ratio_status(good_c, total_c, COMMIT_OK_RATIO),
        f"{good_c}/{total_c} descriptive" if total_c else "no commits",
    ))

    # issues
    if not facts.has_issues:
        out.append(_finding("issues", MISSING, "disabled"))
    elif facts.issue_count >= 1:
        out.append(_finding("issues", OK, f"enabled ({facts.issue_count} total)"))
    else:
        out.append(_finding("issues", THIN, "enabled, 0 issues"))

    return tuple(out)


_FASTLY_RE = re.compile(r"fastly", re.IGNORECASE)
_PAGES_RE = re.compile(r"pages", re.IGNORECASE)
_DEPLOYISH_RE = re.compile(r"deploy|release|\bcd\b", re.IGNORECASE)


def detect_deploy(facts: RepoFacts) -> tuple[str, str]:
    """Return (deploy_style, detail). First match wins."""
    if facts.environments:
        return "Environments", "environments: " + ", ".join(facts.environments)
    if facts.has_fastly_toml or any(_FASTLY_RE.search(w) for w in facts.workflow_names):
        src = "fastly.toml" if facts.has_fastly_toml else next(w for w in facts.workflow_names if _FASTLY_RE.search(w))
        return "Fastly", f"signal: {src}"
    if facts.pages_enabled or any(_PAGES_RE.search(w) for w in facts.workflow_names):
        src = "Pages enabled" if facts.pages_enabled else next(w for w in facts.workflow_names if _PAGES_RE.search(w))
        return "Pages", f"signal: {src}"
    deployish = [w for w in facts.workflow_names if _DEPLOYISH_RE.search(w)]
    if deployish:
        return "head_sha-fallback", f"workflow: {deployish[0]}"
    return "none", "no CD workflow detected"


@dataclass(frozen=True)
class AuditReport:
    owner: str
    repo: str
    visibility: str
    default_branch: str
    findings: tuple[SurfaceFinding, ...]
    deploy_style: str
    deploy_detail: str
    verdict: str
    exit_code: int


def _verdict(findings: tuple[SurfaceFinding, ...]) -> str:
    by_name = {f.name: f for f in findings}
    readme = by_name["README"].status
    why_surfaces = ("README", "docs/", "docs/adr/", "PR bodies", "commit messages", "issues")

    # Thin: README missing, OR every why-surface is missing.
    if readme == MISSING or all(by_name[n].status == MISSING for n in why_surfaces):
        return "THIN"
    # Good: README ok AND >=2 of {ADRs, PR bodies, commit messages} ok.
    core = ("docs/adr/", "PR bodies", "commit messages")
    if readme == OK and sum(1 for n in core if by_name[n].status == OK) >= 2:
        return "GOOD"
    # Everything else.
    return "PARTIAL"


def evaluate(facts: RepoFacts) -> AuditReport:
    findings = surface_findings(facts)
    style, detail = detect_deploy(facts)
    verdict = _verdict(findings)
    return AuditReport(
        owner=facts.owner,
        repo=facts.repo,
        visibility=facts.visibility,
        default_branch=facts.default_branch,
        findings=findings,
        deploy_style=style,
        deploy_detail=detail,
        verdict=verdict,
        exit_code=0 if verdict == "GOOD" else 1,
    )
