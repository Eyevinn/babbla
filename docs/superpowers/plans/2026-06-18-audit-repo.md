# audit-repo (repo onboarding audit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic, read-only CLI that audits a GitHub repo's existing docs + history, prints a readiness report (why-surface rubric + deploy style + verdict), and emits a paste-ready `config/channels.yaml` binding stub.

**Architecture:** A Python package `src/babbla/audit/` split into four modules — `github_reader` (the only network I/O; fetch-only), `assess` (pure rubric: `RepoFacts -> AuditReport`), `report` (rendering), and `__main__` (CLI wiring + exit codes) — plus a thin `audit-repo.sh` wrapper. Assessment is pure so it is unit-tested over fixtures with no network.

**Tech Stack:** Python 3.12, stdlib `urllib` for HTTP (no new dependency), `pyyaml` (already a dep) for round-trip validation, `pytest` + `pytest-asyncio` (the latter unused here).

## Global Constraints

- Python ≥ 3.12; `from __future__ import annotations` at the top of every module (matches the codebase).
- Read-only by construction: the reader makes **GET requests only**; never any write verb. No new runtime dependency may be added — use stdlib `urllib`.
- Reads the **GitHub remote** over REST with the read-only `GITHUB_TOKEN`; never a local working tree (ADR 0009).
- The emitted binding must round-trip through `babbla.config.load_config` unchanged.
- Advisory, non-blocking: a thin repo still produces a valid stub. Only an unreadable *repo* is an error.
- Frozen dataclasses for all data types (matches `config.py`, `read_only.py`).
- Commit after every green step.

---

## Canonical interfaces (locked; later tasks rely on these exact names/types)

```python
# src/babbla/audit/github_reader.py
@dataclass(frozen=True)
class CommitMsg:
    first_line: str
    has_body: bool

@dataclass(frozen=True)
class PrBody:
    length: int                       # len(body or ""); merged PRs only

@dataclass(frozen=True)
class RepoFacts:
    owner: str
    repo: str
    visibility: str                   # GitHub value: "public" | "private" | "internal"
    default_branch: str
    has_issues: bool
    issue_count: int                  # total issues ever (excludes PRs); 0 if none/disabled
    readme_bytes: int | None          # None if absent
    has_claude_md: bool
    docs_file_count: int              # non-ADR files directly under docs/
    docs_adr_dir_exists: bool
    adr_count: int                    # .md files under docs/adr/ excluding README.md
    commits: tuple[CommitMsg, ...]    # most recent 20
    pr_bodies: tuple[PrBody, ...]     # most recent 20 merged PRs
    workflow_names: tuple[str, ...]   # filenames under .github/workflows
    has_fastly_toml: bool
    environments: tuple[str, ...]     # GitHub Environment names
    pages_enabled: bool

class RepoUnreachable(Exception): ...

class GithubReader:
    def __init__(self, get_json): ...          # get_json: Callable[[str], dict|list|None]  (None == HTTP 404)
    def fetch(self, owner: str, repo: str) -> RepoFacts: ...

def make_reader(token: str, *, api_base: str = "https://api.github.com") -> GithubReader: ...

# src/babbla/audit/assess.py
OK = "ok"; THIN = "thin"; MISSING = "missing"

@dataclass(frozen=True)
class SurfaceFinding:
    name: str
    status: str                       # OK | THIN | MISSING
    detail: str
    recommendation: str | None

@dataclass(frozen=True)
class AuditReport:
    owner: str
    repo: str
    visibility: str
    default_branch: str
    findings: tuple[SurfaceFinding, ...]
    deploy_style: str                 # "Environments"|"Fastly"|"Pages"|"head_sha-fallback"|"none"
    deploy_detail: str
    verdict: str                      # "GOOD" | "PARTIAL" | "THIN"
    exit_code: int                    # 0 (GOOD) | 1 (PARTIAL/THIN)

def evaluate(facts: RepoFacts) -> AuditReport: ...

# src/babbla/audit/report.py
def render_binding(report: AuditReport) -> str: ...   # the "  - name: ..." list-item lines, no "projects:" header
def render_report(report: AuditReport, *, color: bool = True) -> str: ...

# src/babbla/audit/__main__.py
def main(argv: list[str] | None = None, reader: GithubReader | None = None) -> int: ...
```

---

### Task 1: Scaffold package and data types

**Files:**
- Create: `src/babbla/audit/__init__.py`
- Create: `src/babbla/audit/github_reader.py`
- Test: `tests/audit/__init__.py` (empty), `tests/audit/test_github_reader.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RepoFacts`, `CommitMsg`, `PrBody`, `RepoUnreachable` (per canonical block).

- [ ] **Step 1: Write the failing test**

Create `tests/audit/__init__.py` (empty file), then `tests/audit/test_github_reader.py`:

```python
from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts


def test_repofacts_is_constructible_and_frozen():
    facts = RepoFacts(
        owner="Wkkkkk",
        repo="MyTV",
        visibility="public",
        default_branch="main",
        has_issues=True,
        issue_count=28,
        readme_bytes=1800,
        has_claude_md=False,
        docs_file_count=3,
        docs_adr_dir_exists=True,
        adr_count=10,
        commits=(CommitMsg("add deploy workflow", True),),
        pr_bodies=(PrBody(120),),
        workflow_names=("deploy-pages.yml",),
        has_fastly_toml=False,
        environments=(),
        pages_enabled=True,
    )
    assert facts.owner == "Wkkkkk"
    assert facts.commits[0].has_body is True
    import dataclasses
    try:
        facts.owner = "x"  # frozen -> should raise
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_github_reader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.audit'`

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/audit/__init__.py`:

```python
```
(empty file)

Create `src/babbla/audit/github_reader.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitMsg:
    first_line: str
    has_body: bool


@dataclass(frozen=True)
class PrBody:
    length: int


@dataclass(frozen=True)
class RepoFacts:
    owner: str
    repo: str
    visibility: str
    default_branch: str
    has_issues: bool
    issue_count: int
    readme_bytes: int | None
    has_claude_md: bool
    docs_file_count: int
    docs_adr_dir_exists: bool
    adr_count: int
    commits: tuple[CommitMsg, ...]
    pr_bodies: tuple[PrBody, ...]
    workflow_names: tuple[str, ...]
    has_fastly_toml: bool
    environments: tuple[str, ...]
    pages_enabled: bool


class RepoUnreachable(Exception):
    """Raised when the repository itself cannot be read (404/401/403/network)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_github_reader.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/__init__.py src/babbla/audit/github_reader.py tests/audit/
git commit -m "feat: scaffold audit package with RepoFacts data types"
```

---

### Task 2: Assessment — per-surface findings

**Files:**
- Create: `src/babbla/audit/assess.py`
- Test: `tests/audit/test_assess_surfaces.py`

**Interfaces:**
- Consumes: `RepoFacts`, `CommitMsg`, `PrBody` (Task 1).
- Produces: `OK`, `THIN`, `MISSING`, `SurfaceFinding`, and `surface_findings(facts) -> tuple[SurfaceFinding, ...]` (internal helper that Task 4's `evaluate` will call).

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_assess_surfaces.py`. The `_facts` helper builds a fully-populated `RepoFacts` with overridable fields so each test varies one surface:

```python
from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit import assess
from babbla.audit.assess import OK, THIN, MISSING


def _facts(**over):
    base = dict(
        owner="o", repo="r", visibility="public", default_branch="main",
        has_issues=True, issue_count=5, readme_bytes=1800, has_claude_md=True,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=tuple(CommitMsg(f"descriptive commit message {i}", False) for i in range(20)),
        pr_bodies=tuple(PrBody(120) for _ in range(20)),
        workflow_names=(), has_fastly_toml=False, environments=(), pages_enabled=False,
    )
    base.update(over)
    return RepoFacts(**base)


def _find(facts, name):
    return next(f for f in assess.surface_findings(facts) if f.name == name)


def test_readme_ok_thin_missing():
    assert _find(_facts(readme_bytes=1800), "README").status == OK
    assert _find(_facts(readme_bytes=500), "README").status == THIN     # boundary: 500 is not > 500
    assert _find(_facts(readme_bytes=None), "README").status == MISSING


def test_adr_dir_present_but_empty_is_thin():
    assert _find(_facts(adr_count=10), "docs/adr/").status == OK
    assert _find(_facts(docs_adr_dir_exists=True, adr_count=0), "docs/adr/").status == THIN
    assert _find(_facts(docs_adr_dir_exists=False, adr_count=0), "docs/adr/").status == MISSING


def test_pr_bodies_ratio():
    ok = tuple(PrBody(120) for _ in range(10)) + tuple(PrBody(0) for _ in range(10))   # 10/20 = 50%
    thin = tuple(PrBody(120) for _ in range(3)) + tuple(PrBody(0) for _ in range(17))  # 3/20
    assert _find(_facts(pr_bodies=ok), "PR bodies").status == OK
    assert _find(_facts(pr_bodies=thin), "PR bodies").status == THIN
    assert _find(_facts(pr_bodies=()), "PR bodies").status == MISSING


def test_commit_messages_descriptive_rule():
    junk = tuple(CommitMsg("wip", False) for _ in range(20))
    assert _find(_facts(commits=junk), "commit messages").status == MISSING
    short_with_body = tuple(CommitMsg("fix", True) for _ in range(20))   # body rescues it
    assert _find(_facts(commits=short_with_body), "commit messages").status == OK


def test_thin_surface_carries_recommendation():
    f = _find(_facts(readme_bytes=None), "README")
    assert f.recommendation is not None and "RECOMMENDATIONS.md" in f.recommendation
    assert _find(_facts(readme_bytes=1800), "README").recommendation is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_surfaces.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.audit.assess'`

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/audit/assess.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from babbla.audit.github_reader import RepoFacts

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
_JUNK_RE = re.compile(r"^(wip|fix|update|stuff|.|..)$", re.IGNORECASE)

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


def _is_descriptive(c) -> bool:
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
    total_pr = len(facts.pr_bodies)
    good_pr = sum(1 for p in facts.pr_bodies if p.length > PR_BODY_MIN_CHARS)
    out.append(_finding(
        "PR bodies", _ratio_status(good_pr, total_pr, PR_BODY_OK_RATIO),
        f"{good_pr}/{total_pr} recent PRs have descriptive bodies" if total_pr else "no merged PRs",
    ))

    # commit messages
    total_c = len(facts.commits)
    good_c = sum(1 for c in facts.commits if _is_descriptive(c))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_surfaces.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/assess.py tests/audit/test_assess_surfaces.py
git commit -m "feat: per-surface legibility findings for repo audit"
```

---

### Task 3: Assessment — deploy-style detection

**Files:**
- Modify: `src/babbla/audit/assess.py`
- Test: `tests/audit/test_assess_deploy.py`

**Interfaces:**
- Consumes: `RepoFacts` (Task 1).
- Produces: `detect_deploy(facts) -> tuple[str, str]` (style, detail) — called by Task 4's `evaluate`.

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_assess_deploy.py`:

```python
from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit.assess import detect_deploy


def _facts(**over):
    base = dict(
        owner="o", repo="r", visibility="public", default_branch="main",
        has_issues=True, issue_count=5, readme_bytes=1800, has_claude_md=True,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=(CommitMsg("a descriptive message here", False),),
        pr_bodies=(PrBody(120),),
        workflow_names=(), has_fastly_toml=False, environments=(), pages_enabled=False,
    )
    base.update(over)
    return RepoFacts(**base)


def test_environments_wins():
    style, detail = detect_deploy(_facts(environments=("stage", "prod"), pages_enabled=True))
    assert style == "Environments"
    assert "stage" in detail and "prod" in detail


def test_fastly_by_toml_or_workflow():
    assert detect_deploy(_facts(has_fastly_toml=True))[0] == "Fastly"
    assert detect_deploy(_facts(workflow_names=("fastly-deploy.yml",)))[0] == "Fastly"


def test_pages_by_flag_or_workflow():
    assert detect_deploy(_facts(pages_enabled=True))[0] == "Pages"
    assert detect_deploy(_facts(workflow_names=("deploy-pages.yml",)))[0] == "Pages"


def test_head_sha_fallback_for_generic_deploy_workflow():
    assert detect_deploy(_facts(workflow_names=("release.yml",)))[0] == "head_sha-fallback"


def test_none_when_no_cd():
    assert detect_deploy(_facts(workflow_names=("test.yml", "lint.yml")))[0] == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_deploy.py -q`
Expected: FAIL — `ImportError: cannot import name 'detect_deploy'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/babbla/audit/assess.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_deploy.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/assess.py tests/audit/test_assess_deploy.py
git commit -m "feat: deploy-style detection for repo audit"
```

---

### Task 4: Assessment — verdict and `evaluate` assembly

**Files:**
- Modify: `src/babbla/audit/assess.py`
- Test: `tests/audit/test_assess_evaluate.py`

**Interfaces:**
- Consumes: `surface_findings`, `detect_deploy` (Tasks 2–3), `RepoFacts` (Task 1).
- Produces: `AuditReport`, `evaluate(facts) -> AuditReport` (per canonical block).

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_assess_evaluate.py`:

```python
from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts
from babbla.audit.assess import evaluate


def _facts(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        has_issues=True, issue_count=28, readme_bytes=1800, has_claude_md=False,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=tuple(CommitMsg(f"descriptive commit message {i}", False) for i in range(20)),
        pr_bodies=tuple(PrBody(120) for _ in range(20)),
        workflow_names=("deploy-pages.yml",), has_fastly_toml=False, environments=(), pages_enabled=True,
    )
    base.update(over)
    return RepoFacts(**base)


def test_good_verdict_exit_zero():
    r = evaluate(_facts())
    assert r.verdict == "GOOD"
    assert r.exit_code == 0
    assert r.deploy_style == "Pages"
    assert r.visibility == "public"


def test_thin_when_readme_missing():
    r = evaluate(_facts(readme_bytes=None))
    assert r.verdict == "THIN"
    assert r.exit_code == 1


def test_partial_when_readme_present_but_why_thin():
    # README ok, but ADRs absent, PR bodies & commits all thin/missing -> not GOOD, not THIN
    r = evaluate(_facts(
        docs_adr_dir_exists=False, adr_count=0,
        pr_bodies=tuple(PrBody(0) for _ in range(20)),
        commits=tuple(CommitMsg("wip", False) for _ in range(20)),
    ))
    assert r.verdict == "PARTIAL"
    assert r.exit_code == 1


def test_report_carries_all_findings():
    r = evaluate(_facts())
    names = {f.name for f in r.findings}
    assert {"README", "CLAUDE.md", "docs/", "docs/adr/", "PR bodies", "commit messages", "issues"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_evaluate.py -q`
Expected: FAIL — `ImportError: cannot import name 'evaluate'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/babbla/audit/assess.py` (add `AuditReport` dataclass and `evaluate`; note the top-of-file `dataclass` import already exists):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_assess_evaluate.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/assess.py tests/audit/test_assess_evaluate.py
git commit -m "feat: overall audit verdict and evaluate() assembly"
```

---

### Task 5: Report — `channels.yaml` binding stub

**Files:**
- Create: `src/babbla/audit/report.py`
- Test: `tests/audit/test_report_binding.py`

**Interfaces:**
- Consumes: `AuditReport` (Task 4).
- Produces: `render_binding(report) -> str` (per canonical block).

- [ ] **Step 1: Write the failing test**

The key property: the emitted stub round-trips through `babbla.config.load_config`. Create `tests/audit/test_report_binding.py`:

```python
from babbla.audit.assess import AuditReport, SurfaceFinding, OK
from babbla.audit.report import render_binding
from babbla.config import load_config


def _report(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        findings=(SurfaceFinding("README", OK, "1.8 KB", None),),
        deploy_style="Pages", deploy_detail="signal: Pages enabled",
        verdict="GOOD", exit_code=0,
    )
    base.update(over)
    return AuditReport(**base)


def test_binding_roundtrips_through_load_config(tmp_path):
    block = render_binding(_report())
    cfg_file = tmp_path / "channels.yaml"
    cfg_file.write_text("projects:\n" + block, encoding="utf-8")

    cfg = load_config(cfg_file)
    assert len(cfg.bindings) == 1
    b = cfg.bindings[0]
    assert (b.name, b.owner, b.repo, b.visibility) == ("MyTV", "Wkkkkk", "MyTV", "public")
    assert b.channel_id is None
    assert b.dm is False


def test_binding_carries_helpful_comments():
    block = render_binding(_report())
    assert "set to your Slack channel id" in block
    assert "dm: true" in block  # the guidance comment mentions the dm flag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_report_binding.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.audit.report'`

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/audit/report.py` (hand-format so inline comments survive; the round-trip test guards validity):

```python
from __future__ import annotations

from babbla.audit.assess import AuditReport


def render_binding(report: AuditReport) -> str:
    """The channels.yaml list-item block (indented under `projects:`)."""
    return (
        f"  - name: {report.repo}\n"
        f"    owner: {report.owner}\n"
        f"    repo: {report.repo}\n"
        f"    visibility: {report.visibility}  # GitHub value; 'internal' is an org choice\n"
        f"    channel_id: null  # set to your Slack channel id\n"
        f"    dm: false         # set true for the one DM-bound pilot project (dm: true)\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_report_binding.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/report.py tests/audit/test_report_binding.py
git commit -m "feat: render channels.yaml binding stub from audit report"
```

---

### Task 6: Report — full human readout

**Files:**
- Modify: `src/babbla/audit/report.py`
- Test: `tests/audit/test_report_readout.py`

**Interfaces:**
- Consumes: `AuditReport`, `render_binding` (Task 5).
- Produces: `render_report(report, *, color=True) -> str` (per canonical block).

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_report_readout.py`:

```python
from babbla.audit.assess import AuditReport, SurfaceFinding, OK, THIN, MISSING
from babbla.audit.report import render_report


def _report(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        findings=(
            SurfaceFinding("README", OK, "1.8 KB", None),
            SurfaceFinding("PR bodies", THIN, "4/20 recent PRs have descriptive bodies",
                           "Write descriptive PR bodies — see docs/RECOMMENDATIONS.md §1."),
            SurfaceFinding("CLAUDE.md", MISSING, "absent", None),
        ),
        deploy_style="Pages", deploy_detail="signal: Pages enabled",
        verdict="GOOD", exit_code=0,
    )
    base.update(over)
    return AuditReport(**base)


def test_readout_has_header_findings_deploy_verdict_and_binding():
    text = render_report(_report(), color=False)
    assert "Wkkkkk/MyTV" in text
    assert "README" in text and "1.8 KB" in text
    assert "Deploy style: Pages" in text
    assert "Verdict: GOOD" in text
    assert "config/channels.yaml" in text          # the stub section header
    assert "  - name: MyTV" in text                 # the stub itself is embedded


def test_recommendations_listed_for_thin_surfaces():
    text = render_report(_report(), color=False)
    assert "docs/RECOMMENDATIONS.md §1" in text


def test_no_color_uses_ascii_markers():
    text = render_report(_report(), color=False)
    assert "OK" in text and "THIN" in text and "MISSING" in text
    assert "✓" not in text and "⚠" not in text and "✗" not in text


def test_color_uses_symbols():
    text = render_report(_report(), color=True)
    assert "✓" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_report_readout.py -q`
Expected: FAIL — `ImportError: cannot import name 'render_report'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/babbla/audit/report.py`:

```python
from babbla.audit.assess import OK, THIN, MISSING

_SYMBOLS = {OK: "✓", THIN: "⚠", MISSING: "✗"}
_ASCII = {OK: "OK", THIN: "THIN", MISSING: "MISSING"}
_RULE = "─" * 64


def _marker(status: str, color: bool) -> str:
    return _SYMBOLS[status] if color else f"[{_ASCII[status]}]"


def render_report(report: AuditReport, *, color: bool = True) -> str:
    lines: list[str] = []
    lines.append(f"Babbla repo audit — {report.owner}/{report.repo}")
    lines.append(f"Visibility: {report.visibility} · default branch: {report.default_branch}")
    lines.append("")
    lines.append('Why-surfaces (repo = source of truth for "why")')
    for f in report.findings:
        lines.append(f"  {_marker(f.status, color)} {f.name:<16} {f.detail}")
    lines.append("")
    lines.append(f"Deploy style: {report.deploy_style}  ({report.deploy_detail})")
    lines.append("")
    lines.append(f"Verdict: {report.verdict}")

    recs = [f.recommendation for f in report.findings if f.recommendation]
    if recs:
        lines.append("Recommendations:")
        for r in recs:
            lines.append(f"  • {r}")
    lines.append("")
    lines.append("Add this to config/channels.yaml under `projects:` " + _RULE[:20])
    lines.append(render_binding(report).rstrip("\n"))
    lines.append(_RULE)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_report_readout.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/report.py tests/audit/test_report_readout.py
git commit -m "feat: full human readout for repo audit"
```

---

### Task 7: CLI wiring, flags, exit codes, errors

**Files:**
- Create: `src/babbla/audit/__main__.py`
- Test: `tests/audit/test_cli.py`

**Interfaces:**
- Consumes: `evaluate` (Task 4), `render_report`/`render_binding` (Tasks 5–6), `GithubReader`, `RepoUnreachable`, `make_reader` (Tasks 1, 8).
- Produces: `main(argv=None, reader=None) -> int` (per canonical block).

Note: `main` accepts an injected `reader` so the test passes a fake (no network). When `reader is None`, `main` builds one via `make_reader(token)` — that real path is exercised only by Task 8's integration test.

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_cli.py`. A `_FakeReader` returns canned facts; tests assert exit codes and stream routing:

```python
import pytest

from babbla.audit.github_reader import CommitMsg, PrBody, RepoFacts, RepoUnreachable
from babbla.audit.__main__ import main


def _facts(**over):
    base = dict(
        owner="Wkkkkk", repo="MyTV", visibility="public", default_branch="main",
        has_issues=True, issue_count=28, readme_bytes=1800, has_claude_md=False,
        docs_file_count=3, docs_adr_dir_exists=True, adr_count=10,
        commits=tuple(CommitMsg(f"descriptive commit message {i}", False) for i in range(20)),
        pr_bodies=tuple(PrBody(120) for _ in range(20)),
        workflow_names=("deploy-pages.yml",), has_fastly_toml=False, environments=(), pages_enabled=True,
    )
    base.update(over)
    return RepoFacts(**base)


class _FakeReader:
    def __init__(self, facts=None, exc=None):
        self._facts = facts
        self._exc = exc

    def fetch(self, owner, repo):
        if self._exc:
            raise self._exc
        return self._facts


def test_good_repo_exits_zero_and_prints_report(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(_facts()))
    out = capsys.readouterr().out
    assert code == 0
    assert "Verdict: GOOD" in out
    assert "  - name: MyTV" in out


def test_thin_repo_exits_one(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(_facts(readme_bytes=None)))
    assert code == 1


def test_emit_binding_prints_only_yaml(capsys):
    code = main(["Wkkkkk/MyTV", "--emit-binding"], reader=_FakeReader(_facts()))
    out = capsys.readouterr().out
    assert out.lstrip().startswith("- name: MyTV")
    assert "Verdict:" not in out
    assert code == 0


def test_bad_args_exit_two(capsys):
    code = main(["not-a-slug"], reader=_FakeReader(_facts()))
    err = capsys.readouterr().err
    assert code == 2
    assert "owner/repo" in err


def test_unreachable_repo_exit_two(capsys):
    code = main(["Wkkkkk/MyTV"], reader=_FakeReader(exc=RepoUnreachable("404 Not Found")))
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot read" in err.lower()


def test_missing_token_without_reader_exit_two(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main(["Wkkkkk/MyTV"])   # no injected reader -> needs token
    err = capsys.readouterr().err
    assert code == 2
    assert "GITHUB_TOKEN" in err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.audit.__main__'`

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/audit/__main__.py`:

```python
from __future__ import annotations

import argparse
import os
import sys

from babbla.audit.assess import evaluate
from babbla.audit.github_reader import RepoUnreachable, make_reader
from babbla.audit.report import render_binding, render_report


def _parse_slug(slug: str) -> tuple[str, str]:
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(slug)
    return parts[0], parts[1]


def main(argv: list[str] | None = None, reader=None) -> int:
    parser = argparse.ArgumentParser(prog="audit-repo", description="Audit a repo for Babbla onboarding.")
    parser.add_argument("slug", help="owner/repo, e.g. Wkkkkk/MyTV")
    parser.add_argument("--emit-binding", action="store_true", help="print only the channels.yaml block")
    parser.add_argument("--no-color", action="store_true", help="ASCII status markers")
    args = parser.parse_args(argv)

    try:
        owner, repo = _parse_slug(args.slug)
    except ValueError:
        print(f"error: expected owner/repo, got '{args.slug}'", file=sys.stderr)
        return 2

    if reader is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("error: GITHUB_TOKEN is not set", file=sys.stderr)
            return 2
        reader = make_reader(token)

    try:
        facts = reader.fetch(owner, repo)
    except RepoUnreachable as exc:
        print(f"error: cannot read {owner}/{repo}: {exc}", file=sys.stderr)
        return 2

    report = evaluate(facts)

    if args.emit_binding:
        print(render_binding(report).rstrip("\n"))
        return report.exit_code

    color = sys.stdout.isatty() and not args.no_color
    print(render_report(report, color=color), end="")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
```

Note: `render_binding` returns lines indented with two spaces (`  - name:`). The `--emit-binding` test asserts the *stripped* output starts with `- name:`, so printing `render_binding(...)` verbatim (then the test's `.lstrip()`) is correct.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_cli.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/babbla/audit/__main__.py tests/audit/test_cli.py
git commit -m "feat: audit CLI wiring, flags, and exit codes"
```

---

### Task 8: Live GitHub reader + shell wrapper + integration test

**Files:**
- Modify: `src/babbla/audit/github_reader.py` (add `GithubReader.fetch`, `make_reader`)
- Create: `audit-repo.sh` (repo root)
- Test: `tests/audit/test_github_reader_fetch.py` (unit, mocked `get_json`), `tests/audit/test_audit_integration.py` (live, `-m integration`)

**Interfaces:**
- Consumes: `RepoFacts`, `CommitMsg`, `PrBody`, `RepoUnreachable` (Task 1).
- Produces: `GithubReader(get_json).fetch(owner, repo) -> RepoFacts`, `make_reader(token, *, api_base=...) -> GithubReader`.

- [ ] **Step 1: Write the failing test**

Create `tests/audit/test_github_reader_fetch.py`. A dict-backed fake `get_json` maps API paths → canned JSON (or `None` for 404):

```python
import pytest

from babbla.audit.github_reader import GithubReader, RepoUnreachable


def _canned(over=None):
    data = {
        "/repos/o/r": {"visibility": "public", "default_branch": "main", "has_issues": True},
        "/repos/o/r/readme": {"size": 1800},
        "/repos/o/r/contents/CLAUDE.md": {"name": "CLAUDE.md", "type": "file"},
        "/repos/o/r/contents/docs": [
            {"name": "PROPOSAL.md", "type": "file"},
            {"name": "adr", "type": "dir"},
        ],
        "/repos/o/r/contents/docs/adr": [
            {"name": "0001-x.md", "type": "file"},
            {"name": "README.md", "type": "file"},
        ],
        "/repos/o/r/commits?per_page=20": [
            {"commit": {"message": "a descriptive subject line here\n\nwith body"}},
            {"commit": {"message": "wip"}},
        ],
        "/repos/o/r/pulls?state=closed&per_page=20": [
            {"merged_at": "2026-01-01T00:00:00Z", "body": "x" * 120},
            {"merged_at": None, "body": "ignored, not merged"},
        ],
        "/search/issues?q=repo:o/r+type:issue": {"total_count": 28},
        "/repos/o/r/contents/.github/workflows": [{"name": "deploy-pages.yml", "type": "file"}],
        "/repos/o/r/contents/fastly.toml": None,
        "/repos/o/r/environments": {"environments": []},
        "/repos/o/r/pages": {"status": "built"},
    }
    if over:
        data.update(over)
    return data


def _reader(over=None):
    # Override values may be None to simulate a 404 for that path; data.get
    # returns None for both "overridden to None" and "absent", which is exactly
    # what the real get_json does on a 404.
    data = _canned(over)
    return GithubReader(lambda path: data.get(path))


def test_fetch_maps_all_facts():
    facts = _reader().fetch("o", "r")
    assert facts.visibility == "public"
    assert facts.readme_bytes == 1800
    assert facts.has_claude_md is True
    assert facts.docs_file_count == 1          # PROPOSAL.md; 'adr' dir excluded
    assert facts.docs_adr_dir_exists is True
    assert facts.adr_count == 1                 # 0001-x.md; README.md excluded
    assert facts.commits[0].has_body is True
    assert facts.commits[1].has_body is False
    assert len(facts.pr_bodies) == 1           # only the merged PR
    assert facts.pr_bodies[0].length == 120
    assert facts.issue_count == 28
    assert facts.workflow_names == ("deploy-pages.yml",)
    assert facts.has_fastly_toml is False
    assert facts.environments == ()
    assert facts.pages_enabled is True


def test_fetch_absent_surfaces_are_facts_not_errors():
    facts = _reader({
        "/repos/o/r/readme": None,
        "/repos/o/r/contents/CLAUDE.md": None,
        "/repos/o/r/pages": None,
    }).fetch("o", "r")
    assert facts.readme_bytes is None
    assert facts.has_claude_md is False
    assert facts.pages_enabled is False


def test_unreachable_repo_raises():
    reader = GithubReader(lambda path: None)   # repo metadata 404
    with pytest.raises(RepoUnreachable):
        reader.fetch("o", "r")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/audit/test_github_reader_fetch.py -q`
Expected: FAIL — `ImportError: cannot import name 'GithubReader'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/babbla/audit/github_reader.py`:

```python
import json
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"


def _first_line_and_body(message: str) -> tuple[str, bool]:
    parts = message.split("\n", 1)
    first = parts[0]
    body = parts[1].strip() if len(parts) > 1 else ""
    return first, bool(body)


class GithubReader:
    def __init__(self, get_json):
        # get_json: Callable[[str], dict | list | None]; None means HTTP 404.
        self._get = get_json

    def fetch(self, owner: str, repo: str) -> RepoFacts:
        base = f"/repos/{owner}/{repo}"
        meta = self._get(base)
        if meta is None:
            raise RepoUnreachable(f"{owner}/{repo}: not found or no read access")

        readme = self._get(f"{base}/readme")
        claude = self._get(f"{base}/contents/CLAUDE.md")

        docs = self._get(f"{base}/contents/docs") or []
        docs_files = [e for e in docs if e.get("type") == "file"]
        docs_adr_dir = any(e.get("type") == "dir" and e.get("name") == "adr" for e in docs)

        adr = self._get(f"{base}/contents/docs/adr") or []
        adr_count = sum(
            1 for e in adr
            if e.get("type") == "file"
            and e.get("name", "").lower().endswith(".md")
            and e.get("name", "").lower() != "readme.md"
        )

        commits_raw = self._get(f"{base}/commits?per_page=20") or []
        commits = []
        for c in commits_raw:
            first, has_body = _first_line_and_body(c["commit"]["message"])
            commits.append(CommitMsg(first_line=first, has_body=has_body))

        pulls_raw = self._get(f"{base}/pulls?state=closed&per_page=20") or []
        pr_bodies = [PrBody(length=len(p.get("body") or "")) for p in pulls_raw if p.get("merged_at")]

        issues = self._get(f"/search/issues?q=repo:{owner}/{repo}+type:issue") or {}
        issue_count = int(issues.get("total_count", 0))

        workflows = self._get(f"{base}/contents/.github/workflows") or []
        workflow_names = tuple(e["name"] for e in workflows if e.get("type") == "file")

        fastly = self._get(f"{base}/contents/fastly.toml")
        env_resp = self._get(f"{base}/environments") or {}
        environments = tuple(e["name"] for e in env_resp.get("environments", []))
        pages = self._get(f"{base}/pages")

        return RepoFacts(
            owner=owner,
            repo=repo,
            visibility=meta.get("visibility", "public"),
            default_branch=meta.get("default_branch", "main"),
            has_issues=bool(meta.get("has_issues", False)),
            issue_count=issue_count,
            readme_bytes=(readme.get("size") if readme else None),
            has_claude_md=claude is not None,
            docs_file_count=len(docs_files),
            docs_adr_dir_exists=docs_adr_dir,
            adr_count=adr_count,
            commits=tuple(commits),
            pr_bodies=tuple(pr_bodies),
            workflow_names=workflow_names,
            has_fastly_toml=fastly is not None,
            environments=environments,
            pages_enabled=pages is not None,
        )


def make_reader(token: str, *, api_base: str = GITHUB_API) -> GithubReader:
    def get_json(path: str):
        req = urllib.request.Request(
            f"{api_base}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "babbla-audit",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RepoUnreachable(f"HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise RepoUnreachable(f"network error for {path}: {exc.reason}") from exc

    return GithubReader(get_json)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/audit/test_github_reader_fetch.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Create the shell wrapper and integration test**

Create `audit-repo.sh` (repo root):

```bash
#!/usr/bin/env bash
# Thin wrapper around the Python audit CLI. Reads GITHUB_TOKEN from the env.
# Usage: ./audit-repo.sh <owner>/<repo> [--emit-binding] [--no-color]
exec python -m babbla.audit "$@"
```

Then make it executable:

Run: `chmod +x audit-repo.sh`

Create `tests/audit/test_audit_integration.py`:

```python
import os

import pytest

from babbla.audit.__main__ import main

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="needs GITHUB_TOKEN")
def test_live_audit_of_mytv(capsys):
    code = main(["Wkkkkk/MyTV"])
    out = capsys.readouterr().out
    assert "Wkkkkk/MyTV" in out
    assert "Verdict:" in out
    assert "  - name: MyTV" in out
    assert code in (0, 1)   # public repo with docs should not error
```

- [ ] **Step 6: Run unit + integration suites**

Run: `.venv/bin/python -m pytest tests/audit -q -m "not integration"`
Expected: PASS (all audit unit tests)

Run (optional, needs token): `set -a && source .env && set +a && .venv/bin/python -m pytest tests/audit/test_audit_integration.py -q -m integration -s`
Expected: PASS — prints a GOOD/PARTIAL verdict for MyTV.

- [ ] **Step 7: Commit**

```bash
git add src/babbla/audit/github_reader.py audit-repo.sh tests/audit/test_github_reader_fetch.py tests/audit/test_audit_integration.py
git commit -m "feat: live GitHub REST reader, audit-repo.sh wrapper, integration test"
```

---

### Task 9: Wire into the roadmap

**Files:**
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Check off Phase 2 and run the full suite**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS (all existing + new audit tests)

- [ ] **Step 2: Edit `docs/ROADMAP.md`** — change the Phase 2 checkbox:

```markdown
- [x] The per-repo onboarding routine, reframed as "read a new repo's existing docs + history"
  so a project can be added cleanly (the by-hand MyTV audit is its prototype). Unlocks
  onboarding the second spine project (the internal service). _(Done: `python -m babbla.audit`
  / `audit-repo.sh`; see `docs/superpowers/specs/2026-06-18-audit-repo-design.md`.)_
```

- [ ] **Step 3: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs: check off Phase 2 audit-repo in roadmap"
```

---

## Self-Review

**Spec coverage:**
- Readiness report + config stub → Tasks 5 (stub), 6 (report), 7 (CLI prints both). ✓
- Python CLI + thin `.sh` wrapper → Tasks 7, 8. ✓
- Deterministic, no LLM → entire plan; reader is REST-only. ✓
- Deploy-style detection → Task 3. ✓
- Module split (reader/assess/report/__main__) → Tasks 1–8. ✓
- Reads remote with read-only token, fetch-only → Task 8 (`make_reader`, GET-only). ✓
- Why-surface rubric + thresholds → Task 2 (constants). ✓
- Three-tier verdict + exit codes → Task 4, asserted in Task 7. ✓
- Output format (readout + delimited stub) + `--emit-binding`/`--no-color` → Tasks 6, 7. ✓
- Error handling table (bad args / no token / unreachable / per-surface 404) → Tasks 7, 8. ✓
- Binding round-trips through `load_config` → Task 5. ✓
- Testing (pure unit, CLI wiring, integration) → every task + Task 8. ✓
- Recommendations link to `RECOMMENDATIONS.md` → Task 2 (`_RECS`), shown in Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The spec's two "deferred to plan" items are resolved here (sync `urllib`; single-page `per_page=20`). ✓

**Type consistency:** `RepoFacts`/`CommitMsg`/`PrBody` fields are identical across the `_facts`/`_canned` helpers and the reader output; `AuditReport` fields match between `evaluate` (Task 4), `render_binding`/`render_report` (Tasks 5–6), and the CLI (Task 7); `surface_findings`/`detect_deploy`/`evaluate`/`render_binding`/`render_report`/`make_reader`/`GithubReader.fetch`/`main` signatures match the canonical block. ✓
