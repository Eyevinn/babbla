# Project Onboarding Runbook (+ read-access preflight) — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-20
**Builds on:** [Audit-repo routine](2026-06-18-audit-repo-design.md) (the doc-surface audit + `--emit-binding`),
[Visibility enforcement](2026-06-18-visibility-enforcement-design.md) (`authorize_ask` / surfaces),
[Lobby](2026-06-18-lobby-design.md) (multi-project discovery catalog),
[Always-on Babbla](2026-06-18-always-on-babbla-design.md) (the OSC-hosted token secret)
**Related:** [ROADMAP — relationship to the project spine](../../ROADMAP.md#relationship-to-the-project-spine),
[ADR 0011 — always-on container hosting](../../adr/0011-always-on-container-hosting.md),
[RECOMMENDATIONS.md](../../RECOMMENDATIONS.md)

## Why this slice exists

Babbla's whole point is to serve *many* projects, but only MyTV and Babbla-itself are bound today. Onboarding
the Nth project is overwhelmingly **config + the existing audit tool** — not new feature work — yet the steps
live only in people's heads, and one real decision is unresolved: a **private/internal repo needs GitHub-token
read access** that today's public-repo-only fine-grained token does not have.

This slice produces a **repeatable onboarding runbook** (`docs/ONBOARDING.md`), resolves the token-access
policy for both open-tier and private repos (with an ADR), and adds a small **read-access preflight**
(`babbla doctor`) so a missing token scope is caught at boot rather than as a confusing empty answer.

### Decisions made during brainstorming

- **Reusable runbook, not a one-off.** Spec the repeatable procedure with a worked example; onboarding any
  specific project is then "run the runbook."
- **Cover both visibilities.** Public/internal works on today's token; private documents the token-scope
  expansion + OSC-secret update + an ADR.
- **Include the preflight.** A startup read-access check (and a standalone CLI) is in-scope, not optional.
- **No new feature code for an open-tier project.** The audit tool + config already suffice; the only code is
  the preflight.

## The runbook (`docs/ONBOARDING.md` — the procedure)

1. **Audit the repo.** `./audit-repo.sh <owner>/<repo>` reads the repo's "why" surfaces
   (README, `docs/`, `docs/adr/`, PR bodies, commit messages) and reports each as ok/thin/missing with
   advisory pointers to `RECOMMENDATIONS.md`. Thin surfaces mean thinner answers (graceful) — **never a
   blocker**. `--emit-binding` prints a starting `channels.yaml` block.
2. **Decide visibility** — `public` / `internal` / `private`. Per `access.py`: on a project's own channel,
   membership *is* the access (always allowed); `public`/`internal` are also answerable on DM/Lobby; `private`
   is **points-don't-reveal** everywhere except its own channel.
3. **GitHub token access** (covers both):
   - **public** → the existing public-repo-read fine-grained token already works. Nothing to change.
   - **private/internal** → grant the fine-grained token read on *that specific repo*: repository access +
     **Contents, Metadata, Pull requests, Issues = Read**. Update the **local** `GITHUB_TOKEN` env **and** the
     **OSC-hosted secret** (always-on). Confirm with the project team that Babbla reading the private repo's
     "why" is acceptable (it is surfaced only on that project's channel). Record the policy change as an ADR
     (today's documented policy is "public-repo read-only").
4. **Add the binding** to `config/channels.yaml` (local-only real values; the committed file stays a NULL
   template):
   ```yaml
   - name: <Name>
     owner: <owner>
     repo: <repo>
     visibility: public | internal | private
     channel_id: <C…>          # once the Slack channel exists; null = no channel yet
     dm: false                 # the single dm:true stays on the pilot project (config enforces one)
     # optional: digest:/quiz:/stale_prs:/adr: blocks
   ```
   Config load already enforces the invariants (exactly one `dm: true`; a private+`dm:true` warning;
   valid cadences/tz).
5. **Slack channel** — create it, invite `@Babbla`, set `channel_id`. For a private project the channel
   membership is the access boundary.
6. **Lobby** — if a lobby is configured, the new project **auto-joins** the discovery catalog
   (`build_catalog` runs over all bindings); routing already requires ≥2 projects. Private projects are
   points-don't-reveal in the lobby.
7. **Verify (live).**
   - `babbla doctor` (below) → confirms the token can read the new repo.
   - Ask in the new channel → a cited answer drawn from the new repo.
   - Lobby ask that should route to it → routes (open-tier) or points-don't-reveal (private).
   - Optional: configure a `digest:` and run `python -m babbla.digest --once --project <Name>`.

A private-repo token-scope miss manifests as empty/failed reads at step 7 — which `babbla doctor` now catches
at step 1 of verification (and at boot).

## The read-access preflight (`babbla doctor`) — the only new code

A small, pure, testable check that the configured token can read every configured repo.

### New: `src/babbla/doctor.py`

```python
@dataclass(frozen=True)
class RepoCheck:
    name: str          # project name
    slug: str          # owner/repo
    reachable: bool
    detail: str        # "ok" | "404 (private repo not in token scope?)" | "<error>"

def check_access(config, *, get_json) -> list[RepoCheck]:
    """For each binding, GET /repos/{owner}/{repo}; classify reachable vs not.
    Pure over an injected get_json — no network in tests."""
```

- For each `binding`: `get_json(f"/repos/{owner}/{repo}")`. A dict → reachable; `None` (404) → not reachable,
  with a hint that a private repo may be outside the token's scope; an exception (auth/network) → not
  reachable with the error text.
- No writes, read-only, fast (one metadata GET per project).

### Standalone CLI: `python -m babbla.doctor`

`src/babbla/doctor/__main__.py` (mirrors `babbla.audit`): loads `BABBLA_CONFIG`, builds `make_get_json` from
`GITHUB_TOKEN`, runs `check_access`, prints a per-project ok/unreachable report, and exits non-zero if **any**
configured repo is unreachable. Usable in CI / pre-deploy.

### Startup hook (graceful) in `app.main()`

After loading config, run `check_access` and **log a WARNING per unreachable repo** — then continue. A
transiently or partially unreachable repo must **not** crash Babbla (it still serves the others); the warning
makes a token-scope miss visible at boot instead of as a silent empty answer later. (Behind a
`BABBLA_SKIP_PREFLIGHT` env escape hatch for offline/dev startup.)

## Error handling & edge cases

- **Repo unreachable at boot** → WARNING logged, startup continues; the project's channel Ask will fail
  loudly (the agent reports it can't read the repo) — consistent with graceful degradation.
- **Private repo, token not yet scoped** → `babbla doctor` flags it with the "not in token scope?" hint; the
  runbook's step 3 is the fix.
- **`dm: true` on a second project** → config load rejects (>1 dm) — caught before runtime.
- **Private + `dm: true`** → existing config warning (a dead DM surface); runbook says keep new projects
  `dm: false`.
- **Thin/missing doc surfaces** → audit reports them; onboarding proceeds (answers are just thinner).
- **No lobby configured** → the new project is reachable on its own channel only; lobby auto-join is a no-op.
- **Read-only preserved** — the preflight and audit are GET-only; onboarding adds no write path.

## Testing

- **`tests/test_doctor.py`** (new): `check_access` with a fake `get_json` — all reachable; a 404 → not
  reachable with the private-scope hint; an exception → not reachable with the error; mixed; empty config →
  empty result.
- **`tests/test_doctor_cli.py`** (new): the CLI prints a per-project report and returns exit code 0 when all
  reachable, non-zero when any is unreachable (fake reader injected, like `test_audit` does).
- **`tests/test_app.py`** (extend): the startup preflight logs a WARNING for an unreachable repo and does
  **not** raise; `BABBLA_SKIP_PREFLIGHT` skips it.
- The runbook itself (`docs/ONBOARDING.md`) is documentation — validated by review, not tests.

## Scope summary

- **New:** `src/babbla/doctor.py` (`check_access`, `RepoCheck`), `src/babbla/doctor/__main__.py` (CLI),
  `docs/ONBOARDING.md` (the runbook), an ADR for private-repo token access; `tests/test_doctor.py`,
  `tests/test_doctor_cli.py`.
- **Changed:** `app.py` (startup preflight hook + `BABBLA_SKIP_PREFLIGHT`), `docs/ROADMAP.md` (note the
  onboarding runbook lands), possibly `README.md` (link the runbook).
- **Unchanged:** `orchestrator.py`, `lobby.py`, `access.py`, `config.py` (onboarding uses existing config),
  the audit tool (reused as-is).
- **No behavior change** for the running pilot: the preflight only logs; everything else is config + docs.

## Out of scope (future)

- **Automated Slack channel creation / bot invite** — done by hand per the runbook (creating channels is a
  human/Slack-admin act, not Babbla's read-only remit).
- **A token broker / per-repo credentials** — one fine-grained token with the needed repos in scope is
  sufficient at this scale; revisit only if the project count or org boundaries demand it.
- **Auto-discovery of org repos** — onboarding is deliberate, one project at a time.
