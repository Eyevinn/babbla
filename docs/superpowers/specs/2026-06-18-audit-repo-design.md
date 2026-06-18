# Design — `audit-repo.sh` (repo onboarding audit)

**Status:** Approved design, ready for implementation plan
**Date:** 2026-06-18
**Roadmap:** Phase 2 ([`docs/ROADMAP.md`](../../ROADMAP.md))
**Relevant ADRs:** [0009](../../adr/0009-repo-is-source-of-truth-for-why.md) (repo = source of truth for "why"),
[0006](../../adr/0006-stateful-config.md) (config populated cleanly),
[0003](../../adr/0003-read-only-by-construction.md) (read-only)

## Purpose

A per-repo onboarding routine that reads a new repository's **existing docs and
history** and tells a developer two things at once:

1. **Readiness report** — how legible is this repo for Babbla? Which "why"
   surfaces exist and how rich are they, and what deploy style does it use?
2. **Config stub** — a paste-ready `config/channels.yaml` binding block so the
   project can be added cleanly.

It generalizes the by-hand MyTV audit (which left no artifact). It is **advisory
and non-blocking**: a thin repo still onboards (graceful degradation, ADR 0009);
the audit informs, it does not gate.

## Non-goals (YAGNI)

- No LLM / agent involvement. Assessment is purely deterministic.
- No live sample of Babbla answering questions (that belongs to the integration
  smoke test).
- No write to `config/channels.yaml`. The audit prints a stub; a human pastes it.
- No numeric "score". Findings are qualitative (`ok` / `thin` / `missing`).

## Implementation shape

A **Python CLI** under `src/babbla/audit/`, invoked as `python -m babbla.audit`,
with a thin `audit-repo.sh` wrapper at the repo root (`exec python -m babbla.audit "$@"`).
This fits the existing Python codebase and its pytest suite while honoring the
roadmap/proposal name.

Reads the **GitHub REST API** with the read-only `GITHUB_TOKEN` already in the
environment — the same remote and credential the agent uses (ADR 0009: Babbla
reads the pushed remote, never a local working tree). The HTTP reader is the only
component that touches the network and is **fetch-only by construction** — the
read-only guarantee extends to onboarding.

### Modules

```
src/babbla/audit/
  __init__.py
  github_reader.py   # thin GitHub REST client (read-only token). Fetches RepoFacts.
  assess.py          # PURE: RepoFacts -> AuditReport. No I/O. Holds all thresholds.
  report.py          # renders AuditReport -> (human text, channels.yaml stub)
  __main__.py        # CLI: parse args, wire reader -> assess -> report, exit codes
audit-repo.sh        # thin wrapper: exec python -m babbla.audit "$@"
```

The split keeps assessment logic pure and network-free, so it is unit-tested over
fixtures. `github_reader` is injected into the CLI so tests substitute a fake that
returns fixture `RepoFacts` — no HTTP, no Docker, no token.

## Data flow

```
./audit-repo.sh Wkkkkk/MyTV
  -> __main__: parse "owner/repo"; read GITHUB_TOKEN
  -> github_reader.fetch(owner, repo) -> RepoFacts   (one read-only REST pass)
  -> assess.evaluate(RepoFacts)       -> AuditReport  (pure; no network)
  -> report.render(AuditReport)       -> human readout (stdout) + channels.yaml stub
  -> exit code: 0 good / 1 partial|thin / 2 error
```

### Facts collected (one pass; each individually failable)

| Fact | REST source |
|------|-------------|
| repo metadata: visibility, default branch, has_issues | `GET /repos/{o}/{r}` |
| README present + size | `GET /repos/{o}/{r}/readme` |
| `CLAUDE.md` present | `GET …/contents/CLAUDE.md` |
| `docs/` entries; `docs/adr/` ADR count | `GET …/contents/docs`, `…/contents/docs/adr` |
| last 20 commits: first line + body-present | `GET …/commits` |
| last 20 merged PRs: body present + length | `GET …/pulls?state=closed` (merged only) |
| open/closed issue counts | `GET …/issues` |
| workflows present + names | `GET …/contents/.github/workflows` |
| GitHub Environments | `GET …/environments` |
| Pages enabled | `GET …/pages` |

`RepoFacts` is a frozen dataclass of these raw values. A per-surface 404 (no
`/pages`, no `CLAUDE.md`) records "absent" and continues — a missing surface is a
*fact*, not an error.

## Assessment rubric

Deterministic and threshold-based; thresholds are named constants in `assess.py`
(one place to tune). Each surface resolves to `ok` / `thin` / `missing`.

### Why-surfaces (legibility)

| Surface | `ok` | `thin` | `missing` |
|---------|------|--------|-----------|
| README | present, > 500 bytes | present, ≤ 500 bytes | absent |
| CLAUDE.md | present | — | absent (informational, not penalized) |
| `docs/` | ≥ 1 non-ADR doc file | — | absent / empty |
| `docs/adr/` | ≥ 1 ADR file | dir exists, 0 ADRs | absent |
| PR bodies | ≥ 50% of last 20 merged PRs have body > 80 chars | 1 ≤ … < 50% | 0 (or no PRs) |
| Commit messages | ≥ 50% of last 20 commits "descriptive" | 1 ≤ … < 50% | none descriptive |
| Issues | enabled & ≥ 1 ever | enabled, 0 issues | disabled |

**"Descriptive commit"** = first line ≥ 15 chars AND first line does not match a
junk pattern (`^(wip|fix|update|stuff|.|..)$`, case-insensitive), OR the commit
has a non-empty body.

### Deploy-style detection (first match wins)

1. `environments` non-empty → **Environments** (note stage/prod names found)
2. else workflow references `fastly` or a root `fastly.toml` exists → **Fastly**
3. else Pages enabled or a `pages`/`deploy-pages` workflow → **Pages**
4. else any workflow whose name/file suggests deploy (`deploy`, `release`, `cd`)
   → **head_sha-fallback**
5. else → **none** (no CD detected)

Deploy style is reported for onboarding context (e.g. the internal-service spine
project). It does not affect the legibility verdict.

### Overall verdict (drives exit code)

Evaluated top-down; first match wins, so the tiers are mutually exclusive:

- **Thin (exit 1)** — README `missing`, OR every why-surface is `missing`.
  Onboarding still works; expect frequent "I don't know".
- **Good (exit 0)** — README `ok` AND at least two of {`docs/adr/`, PR bodies,
  commit messages} are `ok`. Babbla should answer "why" well.
- **Partial (exit 1)** — everything else (README present but not enough "why"
  surfaces reach `ok`). Usable; answers shallower. Report lists the specific gaps.

For every `thin`/`missing` surface the report emits a one-line actionable
recommendation pointing at the matching section of
[`docs/RECOMMENDATIONS.md`](../../RECOMMENDATIONS.md). The verdict never blocks
onboarding.

## Output format

Default run prints a human readout to stdout, ending with a delimited,
paste-ready `channels.yaml` block:

```
Babbla repo audit — Wkkkkk/MyTV
Visibility: public · default branch: main

Why-surfaces (repo = source of truth for "why")
  ✓ README           1.8 KB
  ✗ CLAUDE.md        absent
  ✓ docs/            3 files
  ✓ docs/adr/        10 ADRs
  ⚠ PR bodies        4/20 recent PRs have descriptive bodies
  ✓ commit messages  17/20 descriptive
  ✓ issues           enabled (28 total)

Deploy style: Pages  (workflow: deploy-pages.yml)

Verdict: GOOD — Babbla should answer "why" well.
Recommendations:
  • PR bodies are thin — see docs/RECOMMENDATIONS.md §1 (descriptive PR bodies).
  • No CLAUDE.md — optional; see §2.

Add this to config/channels.yaml under `projects:` ───────────────
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: null  # set to your Slack channel id
    dm: false         # set true for the one DM-bound pilot project
────────────────────────────────────────────────────────────────
```

### Flags (only two)

- `--emit-binding` — print only the YAML binding block to stdout (report
  suppressed), for piping: `./audit-repo.sh o/r --emit-binding >> config/channels.yaml`.
- `--no-color` — plain `OK`/`THIN`/`MISSING` markers instead of `✓⚠✗`; also
  auto-disabled when stdout is not a TTY (clean CI logs).

The YAML stub is serialized through the same `yaml` library `config.py` uses, so
it round-trips back through `load_config` (asserted in tests). `visibility` maps
GitHub `public→public`, `private→private`, with a trailing comment noting
`internal` is an org choice to set by hand. `dm: false` always — the "exactly one
`dm: true`" invariant is the operator's call, not the audit's.

## Error handling & exit codes

| Condition | Behavior | Exit |
|-----------|----------|------|
| Good / Partial / Thin verdict | normal report | 0 / 1 / 1 |
| Bad args (no `owner/repo`) | usage to stderr | 2 |
| `GITHUB_TOKEN` unset | clear message to stderr, no network call | 2 |
| Repo 404 / 401 / 403 | "cannot read {slug}: {reason}" to stderr | 2 |
| Network / timeout | error to stderr, suggest retry | 2 |
| Per-surface 404 (no `/pages`, no `CLAUDE.md`, …) | recorded as absent | — |

A **missing surface is a finding**; only an **unreadable repo is an error**.
Errors go to stderr so `--emit-binding` stdout stays uncontaminated. The reader
sends `Accept: application/vnd.github+json` plus the read-only token and makes no
write calls by construction.

## Testing

- **`assess.evaluate`** — pure unit tests over hand-built `RepoFacts` fixtures:
  one per verdict (Good / Partial / Thin), one per deploy style, and edge cases on
  each threshold (e.g. README exactly 500 bytes, PR bodies exactly at 50%).
- **`report.render`** — unit tests: the YAML stub parses and round-trips through
  `config.load_config`; `--emit-binding` emits only YAML; `--no-color` markers.
- **CLI (`__main__`)** — wiring test with a fake reader returning fixture facts;
  asserts exit codes per verdict and that errors go to stderr.
- **Integration (`-m integration`)** — one live run against `Wkkkkk/MyTV` needing
  a real `GITHUB_TOKEN`; asserts it produces a Good verdict and a parseable stub.

## Open items deferred to the plan

- Sync (`urllib`) vs reuse of the project's existing `aiohttp` dependency for the
  reader. Either works; the reader interface (`fetch(owner, repo) -> RepoFacts`)
  is the same. Pick the simpler at implementation time.
- Exact pagination handling for the 20-item commit/PR windows (single page of
  `per_page=20` is expected to suffice; confirm against the API).
