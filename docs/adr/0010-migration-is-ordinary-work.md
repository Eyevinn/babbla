# ADR 0010: Always-on migration is ordinary work, not blocked on agentmemory centralization

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

[0005](0005-local-first-deployment.md) recorded the local-first deployment and,
following the original proposal, framed the cloud-migration blocker as
**centralizing agentmemory off the laptop**: until memory was shared/hosted,
Babbla supposedly could not move to a server. That framing assumed agentmemory was
in Babbla's critical path.

[0009](0009-repo-is-source-of-truth-for-why.md) removed that assumption:
agentmemory is now optional local enrichment, and the source of truth for "why" is
the project repo, read over the existing GitHub path. With memory out of the
critical path, **there is no shared memory service left to centralize** — so the
"blocker" no longer exists.

## Decision

**Overturn the migration-blocker framing.** Moving Babbla to an always-on server
is **ordinary engineering work**, not a blocked migration. Babbla is a **thin
connector** — Slack ↔ an agent runtime — plus a read-only GitHub path and a small
SQLite session store. Hosting it is small *because there is no agentmemory to
host*.

The "wall" decomposes into ordinary pieces:

- **Read** ("why" reaches Babbla): read repo-resident surfaces over the existing
  GitHub path; inherits repo access control.
- **Capture** ("why" reaches the repo): minimal + advisory documentation hygiene;
  no artifact, no per-developer upload; agentmemory an optional *local* aid.
- **Infra remnant** (always-on): host the thin connector so Asks/Digests survive a
  sleeping laptop, and move the launchd heartbeat
  ([0008](0008-release-anchored-digests.md)) to a server timer/cron.

## Consequences

- The deployment decision in [0005](0005-local-first-deployment.md) still holds for
  the pilot (laptop + Socket Mode); this ADR supersedes only its *blocker
  rationale*. Always-on is a planned later phase (Roadmap Phase 3), not a blocked
  one.
- **One real open question remains for the move: runtime auth on a headless
  server.** Path B (the Claude CLI subscription login) is user/laptop-bound; a
  headless server likely reintroduces an `ANTHROPIC_API_KEY` or service account,
  which the pilot deliberately dropped from required env. This must be decided
  before hosting (open question #1).
- Migrating no longer risks breaking a memory-centralization story, because there
  isn't one; the surfaces that move are Socket Mode → server, launchd → cron, and
  local checkouts → server checkouts.

## Links

- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — "The known wall — resolved by dissolving it", Phase 3, Open question #1
- Supersedes the migration-blocker rationale in [0005](0005-local-first-deployment.md)
- Depends on: [0009](0009-repo-is-source-of-truth-for-why.md)
