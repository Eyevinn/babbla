# Babbla — Post-Pilot Roadmap

**Status:** Living roadmap
**Date:** 2026-06-18
**Scope:** The cross-cutting work and the "known wall" that follow the MyTV Q&A pilot.
For the per-project feature spine (Deploy/Release MCP, Digests) see
[`PROPOSAL-design.md`](PROPOSAL-design.md); this document is the **platform/foundation**
track that those features sit on top of.

## Where we are

The MyTV Q&A pilot is built, open-sourced, and live-verified: a read-only Slack assistant
that answers questions about a single GitHub project, cited to commits/PRs/files. What remains
before Babbla can serve many projects, many people, and run unattended is a set of cross-cutting
items plus one architectural question the proposal called the migration blocker.

## Organizing principle (decided 2026-06-18)

Three commitments shape everything below:

1. **The project repo is the source of truth for "why."** Babbla reads a project's existing
   surfaces — `README`, `CLAUDE.md`, `docs/`, architecture notes, ADRs, commit messages, PR
   bodies, issues — over the read-only GitHub path it already has.
2. **No external memory service.** "Why" comes from the repo, not from a side database. Babbla
   was briefly wired to read an optional local [agentmemory](https://github.com/rohitg00/agentmemory)
   store, but since the repo is the source of truth that enrichment carried no weight in the
   critical path, so it was **removed entirely** ([ADR 0016](adr/0016-remove-agentmemory.md)). A
   developer is of course free to use any local tool to help *author* good PRs/docs — but the
   durable "why" must land in the repo, and Babbla reads only the repo.
3. **No pollution.** Babbla requires **no changes** to the projects it reads — no new artifacts,
   no mandated files. We *recommend* documentation routines (advisory). Sparse docs produce
   thinner answers, never failure (graceful degradation).

**Babbla is a thin connector** — Slack ↔ an agent runtime (Claude today, possibly Copilot
or Codex later) — plus a read-only GitHub path and a small SQLite session store. It is not the memory
and not the projects.

## The "known wall" — resolved by dissolving it

The proposal framed centralizing agentmemory off the laptop as the cloud-migration blocker.
Under the organizing principle above, **agentmemory left Babbla's critical path entirely**
(and has since been removed — [ADR 0016](adr/0016-remove-agentmemory.md)), so there
is no memory service left to centralize. The wall dissolves into ordinary work:

| Half of the wall | Resolution |
| --- | --- |
| **Read** ("why" reaches Babbla) | Read repo-resident surfaces over the existing GitHub path. Inherits the repo's access control for free (private repo → private "why"); no second auth system. Runtime-agnostic — a repo file needs no special MCP, so Copilot reads it as easily as Claude. |
| **Capture** ("why" reaches the repo) | Minimal + advisory. No artifact, no per-developer upload. Just recommended documentation hygiene (which teams should do regardless), authored with whatever local tools a developer likes. Babbla stays strictly read-only; all capture is normal PR/doc workflow with human review. |
| **Infra remnant** (always-on) | Host the thin connector on a server so Asks/Digests survive a sleeping laptop. Small, *because there is no agentmemory to host.* See the open question below. |

### ADR impact

- **Overturns** the deferred *"local-first — agentmemory centralization is the migration
  blocker"* ADR: that is no longer true.
- **Adds** a new ADR: *"the project repo is the source of truth for 'why'; agentmemory is
  optional local enrichment."*
- Recommending ADRs *to subject-project teams* is advisory only (no pollution). Babbla's **own**
  ADRs live in this repo and are not pollution.

## Roadmap

Phases are ordered so each unlocks the next, mirroring the spine philosophy of forcing one new
capability at a time. Phase 0 is immediately executable; later phases get their own spec→plan
cycle when reached.

### Phase 0 — Foundation (now; low-risk; unblocks a second project)

- [x] **Code cleanups.** Parameterize the agent-runner no-answer fallback — it hardcodes
  "MyTV" (`src/babbla/agent_runner.py:77`); it must name the bound project. Bound the
  orchestrator `_locks` dict so it doesn't grow unbounded (`src/babbla/orchestrator.py:18`).
  The first one literally blocks onboarding a second project. _(Done in `51ec467`.)_
- [x] **Write the ADRs** to `docs/adr/`: the 8 decided-but-unwritten decisions, **plus** the
  revised local-first ADR and the new repo-as-source-of-truth ADR above.
  _(Done: `docs/adr/0001`–`0010` + index.)_
- [x] **Advisory recommendations doc** — a short "Getting the most out of Babbla" guide for
  subject-project teams (descriptive PR bodies; keep `README`/`CLAUDE.md`/`docs/` current;
  ADRs for notable decisions; optionally run agentmemory locally as a drafting aid).
  Explicitly framed as recommendations, not requirements. _(Done: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md).)_

### Phase 1 — Read-inputs hardening

- [x] Ensure the agent actually consumes the full set of existing surfaces (`README`,
  `CLAUDE.md`, `docs/`, architecture notes, ADRs, PR bodies, issues, commits) — verify and, if
  needed, extend the system prompt and the GitHub toolset. This operationalizes "repo = source
  of truth." Small. _(Done: system prompt now names the repo "why" surfaces and demotes
  agentmemory to optional enrichment; GitHub toolset (`repos,pull_requests,issues`) already
  covered them and is now pinned by a guard test.)_

### Phase 2 — `audit-repo.sh` (onboarding routine)

- [x] The per-repo onboarding routine, reframed as "read a new repo's existing docs + history"
  so a project can be added cleanly (the by-hand MyTV audit is its prototype). Unlocks
  onboarding the second spine project (the internal service). _(Done: `python -m babbla.audit`
  / `audit-repo.sh`; see `docs/superpowers/specs/2026-06-18-audit-repo-design.md`.)_

  The repeatable onboarding procedure is documented in
  [`ONBOARDING.md`](ONBOARDING.md) (audit → visibility → token → binding → channel
  → lobby → verify), with a `python -m babbla.doctor` read-access preflight.

### Phase 3 — Always-on Babbla (the infra remnant of the wall)

- [x] Host the thin connector on a server so a sleeping laptop no longer drops
  Asks/Digests. _(Done: portable container (`Dockerfile`) → Eyevinn OSC; headless
  auth via a single injected `ANTHROPIC_API_KEY` (open question #1 resolved);
  in-process digest scheduler with per-project `branch`/`deploy` anchors. See
  `docs/superpowers/specs/2026-06-18-always-on-babbla-design.md`, ADRs 0011–0012.)_

### Phase 4 — Lobby + Subscriptions / Visibility (new feature subsystem)

The open discovery surface (Lobby), Shared/Personal Subscriptions, and Visibility tiers from the
proposal's surface model. Decomposed into independent slices, each with its own spec→plan→build cycle
under `docs/superpowers/`. All slices below are **built and merged to local `main` but NOT yet pushed
to origin**, and are **inert until configured** (no behavior change to the live MyTV pilot until a
lobby channel / subscription / digest / quiz is set in `config/channels.yaml`).

- [x] **Visibility enforcement** — `authorize_ask(binding, surface)` pre-flight gate; surface-based
  points-don't-reveal for `private` projects. (`docs/superpowers/specs/2026-06-18-visibility-enforcement-design.md`)
- [x] **Lobby** — open discovery surface; LLM classifier routes a free-text ask to a project, answers
  public/internal, points-don't-reveal for private; sticky per-thread routing. (`…/2026-06-18-lobby-design.md`)
- ~~**Shared Subscriptions** — a Channel follows a *set* of projects (portfolio channel).~~
  **Removed 2026-06-20** (built but never needed; the portfolio-channel routing path was only ever
  exercised by tests). Deleted the code, the `subscriptions:` config block, the `shared_digest_state`
  table, and the tests. The generic router it shared — `_resolve_subscription`,
  `subscriptions.entries_for`, `DigestRunner.summarize_shared` — stays, since Personal Subscriptions
  reuses it. Historical design: `…/2026-06-19-shared-subscriptions-design.md`.
- [x] **Scheduled Actions framework** — generalized the digest scheduler into `ActionScheduler` +
  `Action`; two actions: per-project digest (refactored) and a minimal read-only weekly quiz.
  (The shared/portfolio digest fan-out was removed with Shared Subscriptions above.)
  (`…/2026-06-19-scheduled-actions-design.md`)
- [x] **Personal Subscriptions** — per-user persisted interests (Babbla's first per-user write store)
  via a `/babbla` slash command (subscribe/unsubscribe/list/digest), personal DM-ask routing among the
  subscribed set (reusing the generic subscription router; empty set falls back to the `dm:true` project),
  and a Personal Digest delivered by DM on a per-user cadence (daily/weekly/off). Public/internal only,
  enforced at subscribe-time, ask-time (`Surface.DM`), and digest-send-time. (`…/2026-06-19-personal-subscriptions-design.md`)
- [x] **Topics** — thematic slices narrowing a digest. A `Topic` (name + description) attaches to a
  per-project digest; the summarizer covers only matching changes (LLM-scoped) and stays
  silent when none match this period (watermark still advances). Back-compatible / inert without a
  `topic:` block. Ask-scoped topics, personal-digest topics, and deterministic label/path matching
  deferred. (`…/2026-06-19-topics-design.md`)

Also deferred from the scheduled-actions slice: quiz scoring/per-user state,
more action types (ADR-of-the-week, stale-PR nudge), and summary customisation (per-digest
`audience` field). The skill-based-summary spike's blocker — the headless-SDK skill-loading
investigation — is now **resolved** (see Phase 5; live-validated on `claude-opus-4-8`), but
skills on the *digest/quiz* path stay out of scope: the skilled branch fires only when a
`scratch_key` (a thread) is supplied, and digest/quiz/adr runs pass none ([ADR 0015](adr/0015-skilled-answer-path.md)).

### Phase 5 — Per-project read-only skills (artifacts on the Ask path)

Some valuable per-project work is skill-shaped — *draw an architecture diagram*, *write a
document* — and must **produce a file**, which the locked read-only tool surface ([ADR 0003](adr/0003-read-only-by-construction.md))
forbids. Phase 5 defines read-only precisely and bounds exactly one loosening so Babbla can run
skills and return artifacts **without ever mutating the subject repo**. Decomposed into
independent slices, each with its own spec→plan→build cycle; all built and merged to `main`.
(`docs/superpowers/specs/2026-06-18-readonly-skills-design.md`,
`…/2026-06-20-per-project-skills-design.md`, [ADR 0015](adr/0015-skilled-answer-path.md).)

- [x] **Define read-only precisely; bound one loosening.** "Read-only" = no mutation of the
  subject GitHub repo (server stays `GITHUB_READ_ONLY=1`) and no agentmemory writes — unchanged.
  The loosening: a binding may declare `skills:`, and on the **interactive Ask path only** the run
  gets a per-thread **scratch dir** (derived from `thread_ts`, wiped + recreated each ask, cleaned
  in a `finally`, living outside any repo). Digest/quiz/adr runs pass no `scratch_key` and never
  take the skilled branch.
- [x] **Enforce the write surface with a hook, not allow-listing.** `permission_mode` stays
  `dontAsk`; a `PreToolUse` hook (`src/babbla/read_only.py`) returns `allow` for
  `Write`/`Edit`/`Read` resolving **inside** the scratch dir, `deny` outside and for `Bash`, and no
  opinion elsewhere (so `dontAsk` still governs the denied MCP writers). Live smokes ruled out
  `bypassPermissions`, `acceptEdits`, and `dontAsk`+allow-listed writes as unsafe or non-functional.
- [x] **Babbla-controlled pool, never the subject repo.** Skills load only from `config/skills/`
  (vetted, read-only) via the SDK's `ClaudeAgentOptions.skills=[names]`, staged into
  `<scratch>/.claude/skills/` with `setting_sources=["project"]` so no Babbla-repo or user-global
  context leaks. Seeded with `architecture-diagram` + `onboarding-guide`.
- [x] **Artifacts back to Slack.** Generated files upload threaded under the answer (needs the
  Slack `files:write` scope; missing it degrades to a logged no-op, never a failed ask).
- [x] **Resume-safe + Docker-portable + preflighted.** The scratch path is stable per thread (not
  per request) so the CLI's cwd-keyed transcripts still resume; skills add no new runtime and ride
  the same SDK→CLI path. A `babbla-doctor` / boot preflight (`check_skills`, `run_skills_preflight`)
  verifies every bound skill is stageable from the *runtime* pool (`BABBLA_SKILLS_POOL`), closing
  the gap where the config-dir and deploy-time pools diverge (`09e8c2a`).

### Phase 6 — Pluggable agent runtime + model/effort config (operationalizes ADR 0002)

[ADR 0002](adr/0002-runtime-agnostic-via-mcp.md) decided Babbla should be runtime-agnostic by
routing all capability through MCP; this phase turns that decision into actual support for more
than one runtime — Claude today, **Copilot or Codex** next — plus the config surface to tune
whichever runtime is active. The capability layer is already portable (each MCP server is just a
subprocess spec another runtime launches identically); the only real coupling left is the runtime
call itself.

- [ ] **Pluggable runtime — Claude / Copilot / (possibly) Codex.** The runtime is injected today
  as `query_fn` (defaults to `claude_agent_sdk.query`) at three call-sites — `AgentRunner`
  (`src/babbla/agent_runner.py`), the lobby classifier (`src/babbla/lobby.py:68`), and the
  personal-intent classifier (`src/babbla/personal.py:86`). The injection seam exists, but
  options-building (`ClaudeAgentOptions`, three places) and message-draining
  (`_extract_text`/`_drain`, `agent_runner.py:66`) are Claude-SDK-shaped. Define one thin runtime
  adapter (build-options → run → extract text/session-id) so swapping runtime is a config flip,
  not a re-architecture. The hard parts to port are exactly the ones ADR 0002 flagged as "once the
  headless/read-only story matures": `permission_mode="dontAsk"`, the `PreToolUse` scratch-guard
  hook (`src/babbla/read_only.py`), skill loading (`setting_sources`/`skills`), and session resume
  (the Claude CLI scopes sessions by cwd). A plain-Q&A-only runtime is far simpler than the
  full-feature skilled path — stage it (Q&A first, skilled path once a runtime supports the hook
  surface). Read-only-ness itself survives the swap: it's enforced server-side
  (`GITHUB_READ_ONLY=1`), not by the runtime.
- [ ] **Configurable model + effort.** Model is already one global env var (`BABBLA_MODEL`,
  default `claude-opus-4-8`); **effort/thinking is not configurable** — Babbla sets none of the
  SDK's `effort` / `thinking` / `max_thinking_tokens` knobs, so it runs at the runtime's default.
  Expose effort (and consider `fallback_model`, `max_turns`, and a budget cap) through env/config,
  centralizing the options build so the three call-sites don't drift. Optional follow-on:
  per-project or per-surface model/effort — e.g. a cheap, low-effort model for the lobby/intent
  classifiers and a stronger one for Asks — natural since the classifiers are already separate
  call-sites.

## Open questions

1. ~~**Runtime auth on a server (Phase 3).** Path B (Claude CLI subscription login) is
   user/laptop-bound. A headless server likely reintroduces an `ANTHROPIC_API_KEY` or service
   account — which the pilot deliberately dropped from required env. Decide the headless-auth
   story before hosting.~~ **Resolved by [ADR 0011](adr/0011-always-on-container-hosting.md):**
   a single shared `ANTHROPIC_API_KEY` service key is injected as an OSC secret; Path B
   continues locally. Digests are realized in-process by the scheduler (no launchd required on
   the server).
2. **Public-repo "why" is public.** Repo-resident "why" on a public project (e.g. MyTV) is
   public by construction. Fine for OSS; it removes the option of internal-only rationale on a
   public repo. Acceptable under the no-pollution stance, but worth a conscious confirmation.
3. **When does Minimal stop being enough?** If a project's prose "why" (PRs/ADRs/docs) proves
   too thin for good answers at scale, revisit whether a richer (still repo-resident) structure
   is worth the added convention. Not now (YAGNI).

## Relationship to the project spine

The proposal's spine — MyTV (done) → an internal service (Deploy/Release MCP) → a private
client project (fallback + private + redaction) → Digests — is the **feature** track. It
depends on this foundation track: Phase 0–1 should land before that internal-service feature
work, and Phase 2 (`audit-repo.sh`) is what makes onboarding it clean. Always-on (Phase 3) and the
Lobby (Phase 4) can follow once a second project is answering.
