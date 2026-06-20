# Architecture Decision Records

This directory records the architecture decisions behind Babbla. Each ADR is an
immutable record of a single decision: its context, the choice made, and the
consequences. When a decision changes, we don't edit the old record — we add a
new ADR that supersedes it, and mark the old one accordingly. That preserves the
*history* of why things are the way they are, which is exactly the kind of "why"
Babbla itself exists to surface.

Format is lightweight [Nygard-style](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):
**Status**, **Context**, **Decision**, **Consequences**, and where useful
**Alternatives considered** and **Links**.

> **Scope note.** These are Babbla's *own* ADRs and live in Babbla's repo. They
> are not "pollution" of the projects Babbla reads. Recommending that
> subject-project teams keep ADRs is advisory only — see the advisory
> recommendations guide.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-hybrid-build.md) | Hybrid build — build only the Release→"why" join | Accepted |
| [0002](0002-runtime-agnostic-via-mcp.md) | Runtime-agnostic via MCP | Accepted |
| [0003](0003-read-only-by-construction.md) | Read-only by construction | Accepted |
| [0004](0004-dual-path-deploy-release-mcp.md) | Dual-path Deploy/Release MCP | Accepted |
| [0005](0005-local-first-deployment.md) | Local-first deployment | Accepted (migration-blocker rationale superseded by [0010](0010-migration-is-ordinary-work.md)) |
| [0006](0006-stateful-config.md) | Version-controlled shared config; only personal Digest config is written | Accepted |
| [0007](0007-access-visibility-redaction.md) | Access = Slack membership; Visibility tiers; Incident PII redaction | Accepted |
| [0008](0008-release-anchored-digests.md) | Release-anchored Digests on a launchd heartbeat | Accepted |
| [0009](0009-repo-is-source-of-truth-for-why.md) | The project repo is the source of truth for "why"; agentmemory is optional local enrichment | Accepted (agentmemory clause superseded by [0016](0016-remove-agentmemory.md)) |
| [0010](0010-migration-is-ordinary-work.md) | Always-on migration is ordinary work, not blocked on agentmemory centralization | Accepted |
| [0011](0011-always-on-container-hosting.md) | Always-on container hosting | Accepted |
| [0012](0012-digest-anchor-sourcing.md) | Digest anchor sourcing via per-project branch/deploy config | Accepted |
| [0013](0013-thread-scoped-conversation-sessions.md) | Thread-scoped conversation sessions via Slack `thread_ts` | Accepted |
| [0014](0014-private-repo-token-access.md) | GitHub token may read specific private/internal repos for onboarding | Accepted |
| [0015](0015-skilled-answer-path.md) | Skilled answer path — bounded read-only loosening for artifacts | Accepted |
| [0016](0016-remove-agentmemory.md) | Remove agentmemory entirely — the repo is the only source of "why" | Accepted |

ADRs 0001–0008 were decided on 2026-06-18 and written from the design proposal
([`../PROPOSAL-design.md`](../PROPOSAL-design.md)). ADRs 0009–0010 capture the
organizing principle adopted the same day (see [`../ROADMAP.md`](../ROADMAP.md)).
ADRs 0011–0012 close out Phase 3 (always-on container + scheduled digest anchors).
ADR 0013 documents the thread-scoped conversation model, confirmed during Phase 4
live smoke testing (2026-06-19). ADR 0014 establishes that the fine-grained GitHub
token may be extended to specific private/internal repos for onboarding. ADR 0015
defines the skilled answer path — a bounded, read-only loosening that lets project
bindings opt into per-thread scratch-based artifact production. ADR 0016 removes
agentmemory entirely (2026-06-20): the optional local-enrichment path of 0009 was off
in production and added read-only surface area, so the repo is now the *only* source of
"why" Babbla reads.
