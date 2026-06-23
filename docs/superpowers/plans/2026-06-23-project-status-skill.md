# Project Status Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-written `project-status` skill into Babbla so that any binding that opts in can serve issue-based project status questions.

**Architecture:** The `config/skills/project-status/SKILL.md` was created and committed in `26dc247`. The skill infrastructure (`config.py`, `agent_runner.py`, `doctor`) already discovers, validates, and stages skills automatically — no source code changes are needed. The only remaining work is to add `project-status` to the `skills:` list in `config/channels.yaml` and verify the end-to-end wiring passes the doctor check.

**Tech Stack:** YAML config, existing `babbla` package, `python -m babbla.doctor`.

## Global Constraints

- Skill file must remain read-only compliant (no writes outside scratch dir) — already satisfied by the SKILL.md.
- `config/channels.yaml` is the committed null template. Real channel IDs stay in the local-only override; the template is safe to commit.
- Run the full suite with `python -m pytest -q` from the repo root (use `.venv`).

---

## Task 1: Add `project-status` to `config/channels.yaml`

**Files:**
- Modify: `config/channels.yaml`

**Interfaces:**
- Produces: `project-status` appears in the `skills:` list for each binding that should serve status questions. `load_config` validates the skill exists in the pool at parse time, so a typo fails loudly.

The current `skills:` block for the MyTV binding (and likely the other bindings) looks like:

```yaml
    skills:
      - architecture-diagram
      - onboarding-guide
      - change-impact
```

- [ ] **Step 1: Add the skill to each binding's `skills:` list**

Open `config/channels.yaml`. For each binding that has a `skills:` block, append `project-status`:

```yaml
    skills:
      - architecture-diagram
      - onboarding-guide
      - change-impact
      - project-status
```

Apply this to every binding in the file that currently lists the other three skills.

- [ ] **Step 2: Verify `load_config` accepts the new skill**

Run:
```
.venv/bin/python -c "from babbla.config import load_config; c = load_config('config/channels.yaml'); print([b.skills for b in c.bindings])"
```
Expected: output includes `('architecture-diagram', 'onboarding-guide', 'change-impact', 'project-status')` for each binding. No `ValueError`.

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: same pass count as before (all green). No new failures — this is a config-only change.

- [ ] **Step 4: Commit**

```bash
git add config/channels.yaml
git commit -m "config: opt MyTV into the project-status skill"
```

---

## Task 2: Verify doctor reports the skill as stageable

**Files:**
- No file changes. Read-only verification step.

**Interfaces:**
- Consumes: `babbla.doctor.check_skills`, `BABBLA_SKILLS_POOL=config/skills`.

- [ ] **Step 1: Run the doctor's skills check against the real pool**

```bash
.venv/bin/python -c "
from babbla.config import load_config
from babbla.doctor import check_skills
cfg = load_config('config/channels.yaml')
results = check_skills(cfg, skills_pool='config/skills')
for r in results:
    print(r.name, r.skill, 'OK' if r.ok else 'MISSING:', r.detail)
"
```

Expected: every row prints `OK`. In particular, `project-status` should appear as `ok` for each binding that lists it. No `MISSING` lines.

- [ ] **Step 2: Confirm preflight passes at boot (optional — requires live tokens)**

If `GITHUB_TOKEN`, `SLACK_BOT_TOKEN`, and `SLACK_APP_TOKEN` are set:

```bash
BABBLA_SKIP_PREFLIGHT= .venv/bin/python -m babbla --dry-run 2>&1 | grep -i "skill\|preflight\|warn\|error" | head
```

Expected: no `WARN preflight: skill 'project-status'…not stageable` lines.

---

## Self-Review

**Spec coverage:**
- Skill file (`config/skills/project-status/SKILL.md`) → pre-existing, committed in `26dc247`. ✓
- Wire into channels.yaml → Task 1. ✓
- Doctor/preflight verification → Task 2. ✓
- No source code changes needed (infrastructure already handles discovery/staging) → confirmed by reading `config.py`, `agent_runner.py`, `doctor/__init__.py`. ✓

**Placeholder scan:** No TBD/TODO. All commands are runnable as written.

**Type consistency:** N/A — no new types or functions.
