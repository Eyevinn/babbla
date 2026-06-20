# Project Onboarding Runbook (+ read-access preflight) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a repeatable project-onboarding runbook (`docs/ONBOARDING.md`), an ADR resolving private-repo GitHub-token access, and a small read-only preflight (`babbla doctor`) that catches a missing token scope at boot instead of as a silent empty answer.

**Architecture:** The only new code is a `babbla.doctor` package: a pure `check_access(config, *, get_json) -> list[RepoCheck]` that does one metadata `GET /repos/{owner}/{repo}` per binding and classifies reachable / 404 (private-scope hint) / error, plus a `python -m babbla.doctor` CLI mirroring `babbla.audit`, plus a graceful startup hook in `app.main()` that logs a WARNING per unreachable repo and continues. Everything else is documentation: the runbook, an ADR, and a ROADMAP/README note. No behavior change for the running pilot.

**Tech Stack:** Python 3.14, `pytest` (`asyncio_mode = "auto"`), stdlib `dataclasses`/`argparse`, the read-only `make_get_json` GitHub path (`babbla.digest.anchors`), existing `babbla.config.load_config`.

## Global Constraints

- **Read-only by construction (ADR 0003):** the preflight and audit are GET-only. Onboarding adds no write path. `check_access` does exactly one `GET /repos/{owner}/{repo}` per binding.
- **`doctor` is a package, not a module.** The spec's `src/babbla/doctor.py` + `src/babbla/doctor/__main__.py` cannot coexist; mirror `babbla/audit/`: `src/babbla/doctor/__init__.py` holds `check_access`/`RepoCheck`, `src/babbla/doctor/__main__.py` holds the CLI. `python -m babbla.doctor` runs `__main__.py`.
- **`get_json` contract (from `babbla.digest.anchors.make_get_json`):** `get_json(path) -> object | None`; returns parsed JSON, returns `None` on HTTP 404, and **raises** on other HTTP errors / network failures. `check_access` must catch those raises per-binding (never let one bad repo abort the sweep).
- **Classification:** `dict` result → `reachable=True`, detail `"ok"`; `None` (404) → `reachable=False`, detail `"404 (private repo not in token scope?)"`; exception → `reachable=False`, detail = the exception text (fall back to class name when empty).
- **Graceful startup:** the boot preflight logs `WARNING` per unreachable repo and **continues** — never raises, never crashes Babbla. `BABBLA_SKIP_PREFLIGHT` (any truthy value) skips it entirely (offline/dev).
- **Config is unchanged:** onboarding uses the existing `Config`/`ProjectBinding` and `load_config`; iterate `config.bindings` (each has `.name`, `.owner`, `.repo`, `.visibility`).
- **Next ADR number is 0014** (`docs/adr/` currently ends at `0013`).
- **Run tests with:** `.venv/bin/python -m pytest` from the repo root.
- **Commit style:** Conventional Commits; match existing history.

## File Structure

- `src/babbla/doctor/__init__.py` — **new.** `RepoCheck` dataclass + pure `check_access(config, *, get_json)`.
- `src/babbla/doctor/__main__.py` — **new.** `main(argv=None, get_json=None) -> int` CLI: load `BABBLA_CONFIG`, build `get_json` from `GITHUB_TOKEN`, run `check_access`, print per-project report, exit non-zero if any unreachable.
- `src/babbla/app.py` — **modify.** Add `run_preflight(config, *, get_json, env=None)` helper; call it from `main()` after `load_config`.
- `docs/ONBOARDING.md` — **new.** The repeatable runbook (documentation; validated by review).
- `docs/adr/0014-private-repo-token-access.md` — **new.** Policy ADR for the token-scope expansion.
- `docs/ROADMAP.md` — **modify.** One note that the onboarding runbook lands.
- `README.md` — **modify.** Link the runbook.
- Tests: `tests/test_doctor.py` (new), `tests/test_doctor_cli.py` (new), `tests/test_app.py` (extend).

---

### Task 1: `check_access` + `RepoCheck`

**Files:**
- Create: `src/babbla/doctor/__init__.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: a `config` with a `.bindings` iterable whose items have `.name`, `.owner`, `.repo`; a `get_json(path) -> object | None` callable (same shape as `babbla.digest.anchors.make_get_json`, which returns `None` on 404 and raises otherwise).
- Produces:
  - `@dataclass(frozen=True) class RepoCheck: name: str; slug: str; reachable: bool; detail: str`
  - `def check_access(config, *, get_json) -> list[RepoCheck]` — one `RepoCheck` per binding, in `config.bindings` order.

- [ ] **Step 1: Write the failing test**

Create `tests/test_doctor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.doctor'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/doctor/__init__.py`:

```python
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
        if isinstance(data, dict):
            checks.append(RepoCheck(b.name, slug, True, "ok"))
        else:
            checks.append(RepoCheck(b.name, slug, False, _NOT_IN_SCOPE))
    return checks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/doctor/__init__.py tests/test_doctor.py
git commit -m "feat: babbla.doctor check_access read-access preflight core"
```

---

### Task 2: `python -m babbla.doctor` CLI

**Files:**
- Create: `src/babbla/doctor/__main__.py`
- Test: `tests/test_doctor_cli.py`

**Interfaces:**
- Consumes: `babbla.config.load_config`, `babbla.doctor.check_access`/`RepoCheck`, `babbla.digest.anchors.make_get_json`; env vars `BABBLA_CONFIG` (default `config/channels.yaml`) and `GITHUB_TOKEN`.
- Produces: `def main(argv: list[str] | None = None, get_json=None) -> int` — prints a per-project ok/unreachable report to stdout; returns `0` when all reachable, `1` when any unreachable, `2` on usage/setup error (missing `GITHUB_TOKEN`). When `get_json` is passed (tests), no token is required and no network is used.

- [ ] **Step 1: Write the failing test**

Create `tests/test_doctor_cli.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_doctor_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` for `babbla.doctor.__main__`.

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/doctor/__main__.py`:

```python
from __future__ import annotations

import argparse
import os
import sys

from babbla.config import load_config
from babbla.doctor import check_access


def main(argv: list[str] | None = None, get_json=None) -> int:
    parser = argparse.ArgumentParser(
        prog="babbla-doctor",
        description="Check that the configured GitHub token can read every configured repo.",
    )
    parser.parse_args(argv)

    config_path = os.environ.get("BABBLA_CONFIG", "config/channels.yaml")
    config = load_config(config_path)

    if get_json is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("error: GITHUB_TOKEN is not set", file=sys.stderr)
            return 2
        # Lazy import: only build the network reader when actually needed.
        from babbla.digest.anchors import make_get_json
        get_json = make_get_json(token)

    checks = check_access(config, get_json=get_json)
    for c in checks:
        marker = "ok" if c.reachable else "UNREACHABLE"
        print(f"[{marker}] {c.name} ({c.slug}): {c.detail}")

    return 0 if all(c.reachable for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_doctor_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/doctor/__main__.py tests/test_doctor_cli.py
git commit -m "feat: python -m babbla.doctor CLI read-access report"
```

---

### Task 3: Graceful startup preflight in `app.main()`

**Files:**
- Modify: `src/babbla/app.py` (add `run_preflight`; call it from `main()`)
- Test: `tests/test_app.py` (extend)

**Interfaces:**
- Consumes: `babbla.doctor.check_access`, the module logger, `os.environ`.
- Produces: `def run_preflight(config, *, get_json, env=None) -> list[RepoCheck] | None` — logs a `WARNING` per unreachable repo and returns the checks; returns `None` (logging an info line, calling nothing) when `BABBLA_SKIP_PREFLIGHT` is truthy in `env` (defaults to `os.environ`). Never raises.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (top-level imports + new tests):

```python
import logging

from babbla.app import run_preflight
from babbla.config import load_config


def _cfg_two(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C1\n    dm: true\n"
        "  - name: Secret\n    owner: Eyevinn\n    repo: secret\n"
        "    visibility: private\n    channel_id: C2\n    dm: false\n"
    )
    return load_config(str(cfg))


def test_run_preflight_warns_for_unreachable_and_does_not_raise(tmp_path, caplog):
    config = _cfg_two(tmp_path)

    def gj(path):
        return {"x": 1} if "MyTV" in path else None   # Secret unreachable

    with caplog.at_level(logging.WARNING):
        checks = run_preflight(config, get_json=gj, env={})

    assert [c.reachable for c in checks] == [True, False]
    assert "Eyevinn/secret" in caplog.text
    assert "MyTV" not in caplog.text   # reachable repos do not warn


def test_run_preflight_skipped_does_not_call_get_json(tmp_path):
    config = _cfg_two(tmp_path)

    def boom(path):
        raise AssertionError("get_json must not be called when skipped")

    assert run_preflight(config, get_json=boom, env={"BABBLA_SKIP_PREFLIGHT": "1"}) is None


def test_run_preflight_swallows_get_json_errors(tmp_path, caplog):
    config = _cfg_two(tmp_path)

    def gj(path):
        raise RuntimeError("network down")

    with caplog.at_level(logging.WARNING):
        checks = run_preflight(config, get_json=gj, env={})

    assert all(not c.reachable for c in checks)   # nothing raised out of run_preflight
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k preflight -v`
Expected: FAIL with `ImportError: cannot import name 'run_preflight' from 'babbla.app'`.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/app.py`, add the import near the other `babbla.*` imports:

```python
from babbla.doctor import check_access
```

Add `run_preflight` (e.g. just below `build_scheduler`, before `async def main`):

```python
def run_preflight(config, *, get_json, env=None):
    """Read-access preflight: WARN per unreachable repo, then continue.

    Never raises — a partially unreachable GitHub must not crash Babbla; the
    warning makes a token-scope miss visible at boot instead of as a silent
    empty answer later. Returns the checks, or None when skipped.
    """
    env = os.environ if env is None else env
    if env.get("BABBLA_SKIP_PREFLIGHT"):
        logger.info("Read-access preflight skipped (BABBLA_SKIP_PREFLIGHT set)")
        return None
    checks = check_access(config, get_json=get_json)
    for c in checks:
        if not c.reachable:
            logger.warning("Preflight: cannot read %s (%s): %s", c.name, c.slug, c.detail)
    return checks
```

Wire it into `main()` immediately after `config = load_config(config_path)` (around `app.py:129`):

```python
    config = load_config(config_path)
    run_preflight(config, get_json=make_get_json(secrets.github_token))
```

(`make_get_json` is already imported in `app.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (existing tests + 3 new preflight tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/app.py tests/test_app.py
git commit -m "feat: graceful read-access preflight at app startup"
```

---

### Task 4: The runbook (`docs/ONBOARDING.md`) + ROADMAP/README pointers

**Files:**
- Create: `docs/ONBOARDING.md`
- Modify: `docs/ROADMAP.md` (one note), `README.md` (one link)

**Interfaces:** Documentation only — validated by review, no tests. Must match the procedure in the design spec (`docs/superpowers/specs/2026-06-20-project-onboarding-runbook-design.md`) and reference real commands/tools that now exist (`./audit-repo.sh`, `python -m babbla.doctor`).

- [ ] **Step 1: Write `docs/ONBOARDING.md`**

Create `docs/ONBOARDING.md` with the seven-step runbook. Use this content:

````markdown
# Onboarding a project to Babbla

Babbla serves *many* projects, but onboarding the Nth one is almost entirely
**config + the existing audit tool** — not feature work. This is the repeatable
procedure; "onboard project X" means "run this runbook for X". MyTV is the
worked example throughout.

> Read-only by construction (ADR 0003): every step below is config, docs, or a
> read-only GitHub call. Babbla never writes to the project repo.

## 1. Audit the repo

```bash
./audit-repo.sh <owner>/<repo>          # e.g. ./audit-repo.sh Wkkkkk/MyTV
```

Reads the repo's "why" surfaces (README, `docs/`, `docs/adr/`, PR bodies, commit
messages) and reports each as ok / thin / missing, with advisory pointers to
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md). Thin surfaces mean thinner answers
(graceful) — **never a blocker**.

```bash
./audit-repo.sh <owner>/<repo> --emit-binding   # prints a starting channels.yaml block
```

## 2. Decide visibility — `public` / `internal` / `private`

Per `access.py`: on a project's own channel, **membership *is* the access**
(always allowed). `public`/`internal` are also answerable on DM and in the Lobby.
`private` is **points-don't-reveal** everywhere except its own channel.

## 3. GitHub token access

- **public** → the existing public-repo-read fine-grained token already works.
  Nothing to change.
- **private / internal** → grant the fine-grained token read on *that specific
  repo*: **repository access** + **Contents, Metadata, Pull requests, Issues =
  Read**. Update **both** the local `GITHUB_TOKEN` env **and** the OSC-hosted
  secret (always-on). Confirm with the project team that Babbla reading the
  private repo's "why" is acceptable — it is surfaced only on that project's
  channel. This expands the documented "public-repo read-only" policy; the
  rationale is recorded in
  [ADR 0014](adr/0014-private-repo-token-access.md).

## 4. Add the binding

Edit `config/channels.yaml` (the committed file is a NULL template; real values
stay local-only):

```yaml
- name: <Name>
  owner: <owner>
  repo: <repo>
  visibility: public | internal | private
  channel_id: <C…>          # once the Slack channel exists; null = no channel yet
  dm: false                 # the single dm:true stays on the pilot project
  # optional: digest: / quiz: / stale_prs: / adr: blocks
```

Config load enforces the invariants: exactly one `dm: true`, a private+`dm:true`
warning, valid cadences/timezones.

## 5. Slack channel

Create the channel, invite `@Babbla`, and set its `channel_id` in the binding.
For a private project, channel membership *is* the access boundary.

## 6. Lobby (if configured)

If a lobby is configured, the new project **auto-joins** the discovery catalog
(`build_catalog` runs over all bindings); routing requires ≥2 projects. Private
projects are points-don't-reveal in the lobby. No lobby configured → the project
is reachable on its own channel only (auto-join is a no-op).

## 7. Verify (live)

```bash
python -m babbla.doctor          # confirms the token can read every configured repo
```

- **`babbla doctor`** → the new repo reports `ok`. A private-repo scope miss
  shows as `UNREACHABLE … not in token scope?` — go back to step 3.
- **Ask in the new channel** → a cited answer drawn from the new repo.
- **Lobby ask** that should route to it → routes (open-tier) or
  points-don't-reveal (private).
- **Optional:** configure a `digest:` block and run
  `python -m babbla.digest --once --project <Name>`.

A private-repo token-scope miss otherwise manifests as empty/failed reads at
this step — which `babbla doctor` now catches up front (and at boot, as a logged
WARNING).
````

- [ ] **Step 2: Add a ROADMAP note**

In `docs/ROADMAP.md`, near the Phase 2 `audit-repo.sh` discussion (around line 85), add a one-line note that onboarding is now a documented runbook:

```markdown
The repeatable onboarding procedure is documented in
[`ONBOARDING.md`](ONBOARDING.md) (audit → visibility → token → binding → channel
→ lobby → verify), with a `python -m babbla.doctor` read-access preflight.
```

- [ ] **Step 3: Link the runbook from README**

In `README.md`, add a link to `docs/ONBOARDING.md` wherever onboarding/setup is
discussed (e.g. a "Onboarding a project" bullet or docs index entry):

```markdown
- [Onboarding a project](docs/ONBOARDING.md) — the repeatable runbook for binding the Nth project.
```

- [ ] **Step 4: Review for accuracy**

Re-read `docs/ONBOARDING.md` against the design spec's "The runbook" section.
Confirm every command exists (`./audit-repo.sh`, `python -m babbla.doctor`,
`python -m babbla.digest`) and the ADR link target matches Task 5's filename.

- [ ] **Step 5: Commit**

```bash
git add docs/ONBOARDING.md docs/ROADMAP.md README.md
git commit -m "docs: project onboarding runbook + roadmap/readme pointers"
```

---

### Task 5: ADR 0014 — private-repo token access

**Files:**
- Create: `docs/adr/0014-private-repo-token-access.md`

**Interfaces:** Documentation only. Mirrors the existing ADR format (Status / Date / Deciders, Context, Decision, Consequences, Links). Records the expansion of the "public-repo read-only" token policy to allow read on specific private/internal repos.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0014-private-repo-token-access.md`:

```markdown
# ADR 0014: GitHub token may read specific private/internal repos for onboarding

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Kun Wu

## Context

Babbla reads each project's "why" over a read-only GitHub path
([0003](0003-read-only-by-construction.md),
[0009](0009-repo-is-source-of-truth-for-why.md)). Today's documented policy is a
fine-grained token with **public-repo read-only** access — sufficient for the
MyTV pilot and Babbla itself, both public. Onboarding the Nth project
([`../ONBOARDING.md`](../ONBOARDING.md)) will eventually include a private or
internal repo, whose "why" the public-only token cannot read. The failure mode
is silent: reads return empty and answers look thin for no obvious reason.

## Decision

**The fine-grained GitHub token may be granted read access to specific private
or internal repos that are onboarded to Babbla.** For such a repo, grant
repository access plus **Contents, Metadata, Pull requests, Issues = Read** —
read scopes only, on *named* repos, never org-wide write. Both the local
`GITHUB_TOKEN` env and the OSC-hosted secret ([0011](0011-always-on-container-hosting.md))
are updated together.

Access stays **read-only by construction** ([0003](0003-read-only-by-construction.md)):
expanding *which* repos the token can read does not grant any write capability.
A private repo's "why" is surfaced only on that project's own channel, where
membership is the access boundary ([0007](0007-access-visibility-redaction.md));
elsewhere it is points-don't-reveal.

A read-access preflight (`python -m babbla.doctor`, and a boot-time WARNING)
verifies the token can read every configured repo, so a missing scope is caught
explicitly instead of as a silent empty answer.

## Consequences

- Onboarding a private/internal project is possible without a second auth system
  — one fine-grained token with the needed repos in scope.
- Each private onboarding requires a deliberate token-scope update **and** team
  confirmation that Babbla reading the repo's "why" is acceptable — friction by
  design, matching "onboarding is deliberate, one project at a time".
- **Trade-off:** the token's blast radius grows by one repo per private
  onboarding. Mitigated by read-only, named-repo scopes and the preflight; a
  token broker / per-repo credentials remains out of scope until project count
  or org boundaries demand it.
- Supersedes the implicit "public-repo read-only" framing for the token; the
  read-only *construction* (0003) is unchanged.

## Links

- Runbook: [`../ONBOARDING.md`](../ONBOARDING.md) — step 3 (GitHub token access)
- Design: [`../superpowers/specs/2026-06-20-project-onboarding-runbook-design.md`](../superpowers/specs/2026-06-20-project-onboarding-runbook-design.md)
- Related: [0003](0003-read-only-by-construction.md), [0007](0007-access-visibility-redaction.md), [0009](0009-repo-is-source-of-truth-for-why.md), [0011](0011-always-on-container-hosting.md)
```

- [ ] **Step 2: Verify cross-links resolve**

Confirm the linked ADR filenames exist (`0003`, `0007`, `0009`, `0011`) and that
`docs/ONBOARDING.md` step 3 links back to `adr/0014-private-repo-token-access.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0014-private-repo-token-access.md
git commit -m "docs: ADR 0014 private-repo token access for onboarding"
```

---

## Final verification

- [ ] Run the full suite: `.venv/bin/python -m pytest` — all green (existing + new `test_doctor`, `test_doctor_cli`, preflight tests).
- [ ] Smoke the CLI against the local config: `GITHUB_TOKEN=… python -m babbla.doctor` prints a per-project report and exits 0/1 appropriately.
- [ ] Re-read `docs/ONBOARDING.md` end-to-end as if onboarding a new project; every referenced command exists and every link resolves.

## Scope notes (from the design)

- **Out of scope:** automated Slack channel creation / bot invite (a human/Slack-admin act); a token broker / per-repo credentials; auto-discovery of org repos. Onboarding stays deliberate, one project at a time.
- **Unchanged:** `orchestrator.py`, `lobby.py`, `access.py`, `config.py`, and the audit tool — onboarding reuses existing config and tooling. No behavior change for the running pilot: the preflight only logs.
