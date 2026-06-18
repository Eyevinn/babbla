# Babbla — Design Proposal

**Author:** Kun Wu (kun.wu@eyevinn.se)
**Status:** Design for technical review
**Date:** 2026-06-18

## Abstract

Babbla is a **read-only** Slack assistant that lets colleagues — often non-developers — understand and trace projects without touching a terminal: they **Ask** ad-hoc questions and receive scheduled **Digests**, and Babbla answers from each project's code, history, decisions, and per-commit "why" memory. It never modifies the projects it reads. The architecture is a deliberate hybrid build-vs-buy: a Claude Agent SDK orchestrator that is read-only *by construction*, talking to the world over MCP, with a single small net-new component — a Deploy/Release MCP that reconstructs the canonical Release record. This document lays out the problem, the domain model, the architecture and its guarantees, the surfaces and access model, the build spine, the decided-but-unwritten ADRs, and the open risks I want reviewers to attack.

## Problem & motivation

We run many small projects, each a single GitHub repository deployed to a cloud platform to serve clients. Knowing "what changed", "why is this code here", and "what is live in prod right now" currently requires a developer, a terminal, and tribal knowledge — and a recurring daily sync to broadcast it. That excludes the people who most need the answers: managers, testers, and new joiners who don't yet know the project landscape.

Babbla is meant to **partly replace the daily sync** and to let non-devs **trace projects from Slack alone**. The pull path (Ask) answers ad-hoc questions; the push path (Digest) summarizes what changed on a cadence. The framing is "ask the project via Claude, not a terminal." Crucially, a background survey found **no off-the-shelf tool** that performs the join we care most about: linking a **Release** (a behaviour reaching an Environment) back to the **commit** and the **"why"** behind it. That gap is the reason to build rather than buy.

## Domain model / ubiquitous language

The terms below are the project's ubiquitous language. They are load-bearing — the architecture is organised around them.

| Term | Definition |
| --- | --- |
| **Project** | A single GitHub repository, deployed to a cloud platform to serve clients (a web service or a Fastly Compute function). The unit Babbla reads and reports on. (Avoid "repo"; a "platform" may span several Projects and is not yet modelled.) |
| **Visibility** | A property of a Project: **public** (open-source, answerable to anyone), **internal** (the default for Projects we care about, answerable to any workspace member including via the Lobby), or **private** (client/restricted, answerable only to members of the Project's Channel). Defaults from the GitHub repo visibility; overridable in config. |
| **Audience** | A person or group that consumes Babbla — manager, tester, developer, new joiner. Different Audiences care about different Projects at different detail levels. (Avoid "user", "colleague".) |
| **Subscription** | The set of Projects (and optionally Topics) an Audience cares about; bounds what they can Ask about and which Digests they receive. **Shared Subscription** is tied to a Channel; **Personal Subscription** is an individual's own interests and persists even though private Asks do not. |
| **Topic** | A thematic slice of interest within or across Projects (e.g. security changes, a feature area, incidents). Lets a Subscription be narrower than a whole Project. |
| **Channel** | A Slack channel that makes a *shared* Subscription concrete: shared Asks happen here, the shared Digest is posted here, and Releases/incidents/discussion for the subscribed Projects live here. The shared, persistent counterpart to a private Ask. |
| **Lobby** | A single open entry surface where anyone can Ask without knowing which Project the question concerns. Babbla locates the relevant Project(s), answers, and points the asker to that Project's Channel — discovery doubles as onboarding. |
| **Ask** | A *pull* interaction targeting a single Project (never the platform). **Shared Ask** happens in a Channel, scoped by its Subscription, and is persisted by Slack so the whole Audience learns. **Private Ask** is a 1:1 DM, personal and ephemeral — nothing is saved beyond the session. (Avoid "query", "prompt".) |
| **Digest** | A *push* interaction: a scheduled summary of changes, Releases, architecture decisions, and incidents across a Subscription. **Shared Digest** posts to a Channel on a fixed cadence; **Personal Digest** is delivered privately, scoped to a Personal Subscription. (Avoid "report", "notification", "summary".) |
| **Environment** | A deployment target a Project runs in. The set is **per-Project**: some have none (no CD), some a single **prod**, some a **stage + prod** pair. An Ask defaults to prod (what clients/testers see); stage, where present, previews the next Release. |
| **Release** | A change reaching an Environment to serve clients — the moment a behaviour becomes visible to non-developers. Distinct from a merge to main that isn't live yet. Canonical record: `{project, environment, version, commit, timestamp, trigger}`, sourced primarily from the GitHub Actions deploy run, enriched by GitHub Releases / Fastly versions. (Avoid "deploy", "rollout".) |
| **Incident** | A record of a production problem and its resolution. Engineering details are internal-safe to summarize, but client-sensitive data (names, PII) must never be surfaced — every Incident summary passes a redaction check before posting. |

## Architecture

### Build-vs-buy: hybrid

The decision is **hybrid**. A background survey found no off-the-shelf tool that does the Release→commit "why" join — the heart of the value proposition. So we buy/reuse everything that already exists (Git/GitHub access, agent orchestration, memory, Slack transport) and **build only the one thing nobody else builds**: a small Deploy/Release MCP that reconstructs the canonical Release record. Everything else is composition.

### Orchestrator: Claude Agent SDK, read-only by construction

The orchestrator is the **Claude Agent SDK**. The dominant non-functional requirement is that Babbla **never modifies the projects it reads**, so read-only is enforced *by construction*, with multiple independent layers so no single failure makes it writable:

1. **`permissionMode: dontAsk`** — the agent does not get an interactive escalation path.
2. **A `Read` / `Grep` / `Glob` tool allowlist** — no write/exec tools are even available.
3. **A read-scoped token** for GitHub access.
4. **Read-only checkouts** on disk.

**Explicit risk call-out:** we must **NEVER** use `bypassPermissions`. `bypassPermissions` would defeat the gating layer and is the single most dangerous misconfiguration — a gating bug here turns a read-only assistant into one that can mutate repositories. This is a primary review target (see Open Risks).

### MCP layer

The orchestrator reaches the outside world over **MCP**, which keeps it model-agnostic (see runtime seam below):

- **`github/github-mcp-server`** in **`--read-only`** mode over **stdio** — git history, PRs, releases, code, and `docs/adr/`. This is the read path into a Project.
- **`agentmemory`** — the per-commit **"why"** store. Important caveat: the **commit↔session linkage is forward-only and currently near-empty**. So on day one the "why" is **not** primarily agentmemory; it is **commit messages + PR bodies + `docs/adr/`**. agentmemory's contribution grows as new commits accrue linked sessions. Reviewers should assume the day-one experience is "commit/PR/ADR archaeology", with memory as an enrichment that compounds over time.

### The single net-new build: Deploy/Release MCP

The only component we build from scratch is a small **Deploy/Release MCP** whose job is to produce the canonical Release event record:

```
Release = { project, environment, version, commit, timestamp, trigger }
```

It is **dual-path** by design, because Projects vary in how cleanly they expose deployment state:

- **Clean path** — GitHub **Deployments / Statuses**. When a Project uses GitHub Deployments, the Release record is read directly.
- **Fallback path** — **deploy-workflow `head_sha`**. When there are no Deployments, we reconstruct the Release from the deploy workflow run's `head_sha` (the commit that the deploy job ran against).
- **Enrichment** — **Fastly active-version** lookup, for Projects that are Fastly Compute functions, to confirm/annotate what is actually live.

This dual-path shape is what lets the same Release abstraction span a GitHub Pages site, a GitHub-Environments service, and a Fastly function.

### Slack transport

Slack integration uses **Bolt + Socket Mode**, so there is **no public URL** to expose or secure — Babbla dials out, which fits the local-first deployment.

### Digests: Release-anchored, headless, scheduled

Digests are **Release-anchored** (the spine of "what changed" is the Release timeline). They run as **`claude -p` headless** invocations driven by **launchd**. launchd acts as a **heartbeat**: on each tick it fires any **due** Digests, computed from **per-channel cadence + timezone + a watermark**, with **catch-up-on-wake** so a laptop that was asleep still emits the Digests it owed rather than skipping them.

### Local-first, with a known migration blocker

The whole system is **local-first** (runs on the laptop; Socket Mode means no inbound exposure). The explicit **cloud-migration blocker** is **centralising agentmemory off the laptop** — until memory is shared/hosted, Babbla can't simply move to a server.

### Runtime seam

The runtime is **swappable behind a thin seam**. Because all capability flows through MCP, the orchestrator is **model-agnostic**; the Claude Agent SDK is the current runtime, and (for example) **Copilot is a possible future runtime** once its headless/read-only story matures. MCP is what keeps that door open.

## Surfaces & access model

Three surfaces, mapped to the domain:

- **Lobby** — open discovery + onboarding. Anyone can Ask without naming a Project; Babbla finds the Project(s), answers, and points to the Channel.
- **Channel** (per service) — shared Asks, Releases, incidents, and the shared Digest for a Project's Subscription.
- **Private DM** — ephemeral private Asks + the personal Digest.

**Access is Slack channel membership.** We do not invent a parallel authorization system; if you're in the Channel, you can see that Project's shared surface. Layered on top are the Project **Visibility** tiers:

- The **Lobby answers public + internal** Projects.
- For **private** Projects the Lobby uses **"points-don't-reveal"**: it can direct an asker to the Project's Channel but does not surface private content to non-members.

**Incident PII redaction.** Every Incident summary passes a **client-PII redaction check** before it is posted; engineering detail is internal-safe to summarize, client-sensitive data (names, PII) must never be surfaced.

**Configuration & state.** Shared configuration lives in a **version-controlled `config/channels.yaml`** (Channels, Subscriptions, Visibility overrides). The **personal Digest config is the only state Babbla writes** — consistent with the read-only-by-construction stance toward the Projects themselves.

## Build spine & roadmap

Three projects form the build spine, chosen so each step forces exactly one new capability:

| Project | Role | What it exercises |
| --- | --- | --- |
| **MyTV** (public) | Q&A MVP | **public** Project, GitHub **Pages** clean path, rich agentmemory "why" |
| An internal service | Release-aware | **internal** Project, GitHub **Environments** (stage/prod) + **Fastly** enrichment |
| A private client project | Hardening | fallback **`head_sha`** path, **private** Project, Incident **redaction** |

**Plan (in order):** audit ✓ (done 2026-06-18) → Q&A bot on MyTV → dual-path Deploy MCP → Digests.

**Deferred:** other internal projects — one that exercises an AWS fallback adapter, and a couple that are Q&A-only (no Release path needed yet).

## Decisions (ADRs)

Eight decisions are **decided but not yet written to `docs/adr/`** (deferred 2026-06-18). They are listed here so reviewers can challenge them now:

| ADR | Decision | One-line rationale |
| --- | --- | --- |
| Hybrid build | Build only the Release→"why" join; reuse the rest | A survey found no off-the-shelf tool does this join |
| Runtime-agnostic via MCP | All capability via MCP; runtime swappable | Keeps it model-agnostic; Copilot a possible future runtime |
| Read-only by construction | `dontAsk` + Read/Grep/Glob allowlist + read-scoped token + read-only checkouts | Never modify the Projects we read; never `bypassPermissions` |
| Dual-path Deploy MCP | GitHub Deployments clean path + `head_sha` fallback + Fastly enrichment | One Release abstraction across heterogeneous deploy styles |
| Local-first | Runs on the laptop; Socket Mode, no public URL | Simplicity now; agentmemory centralisation is the migration blocker |
| Stateful config | Shared config in `config/channels.yaml`; only personal Digest config is written | Keep state minimal and version-controlled |
| Access / Visibility / redaction | Access = Slack membership; Lobby answers public+internal, points-don't-reveal for private; Incident PII redaction | Reuse Slack's model; protect client data |
| Release-anchored Digests | Digests built around the Release timeline, fired by launchd heartbeat | "What changed" is best anchored to what reached an Environment |

Also deferred: **`scripts/audit-repo.sh`** — the per-repo onboarding routine, of which the by-hand audit is the prototype.

## Open risks & questions for reviewers

1. **The "why" gap on day one.** The commit↔session linkage in agentmemory is forward-only and near-empty, so early answers lean on commit messages, PR bodies, and `docs/adr/`. Is that good enough for the MVP Audiences, or does it under-deliver on the headline "why" promise until memory accrues?
2. **Local-first vs centralisation.** Local-first is simple but single-machine; centralising agentmemory off the laptop is the explicit blocker. What's the right trigger and design for that migration — and what breaks (Socket Mode, launchd heartbeat, checkouts) when we move?
3. **Read-only guarantees.** Are four independent layers (`dontAsk`, tool allowlist, read-scoped token, read-only checkouts) sufficient, and how do we *test* that `bypassPermissions` can never sneak in? This is the highest-severity gating risk.
4. **Redaction reliability.** The Incident redaction check is a hard requirement — client PII must never surface. How do we make it reliable and auditable rather than best-effort, and how do we fail safe (suppress vs. post)?
5. **Scaling across many repos.** The spine is three projects with deferred others; in practice Babbla would watch many more. How does the audit/onboarding routine (`audit-repo.sh`), config, and per-channel Digest scheduling scale as Project count grows?
