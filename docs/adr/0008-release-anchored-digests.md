# ADR 0008: Release-anchored Digests on a launchd heartbeat

- **Status:** Accepted. Anchor *sourcing* for the minimal digest is specified by
  [0012](0012-digest-anchor-sourcing.md) (per-project `branch`/`deploy` over the
  read-only GitHub path); the launchd heartbeat is realized as an in-process
  scheduler.
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

The push path (Digest) summarizes "what changed" on a cadence. We need to decide
what the summary is *anchored to* and how it is scheduled. "What changed" could be
anchored to commits, merges, or releases — but for the Audiences Babbla serves
(managers, testers, new joiners), what matters is what actually reached an
Environment and became visible, not every merge to main.

## Decision

- **Release-anchored.** The spine of "what changed" is the **Release timeline**
  (changes reaching an Environment), not the commit/merge timeline. A merge that
  isn't live yet is not the headline.
- **Headless + scheduled.** Digests run as **`claude -p` headless** invocations
  driven by **launchd**, which acts as a **heartbeat**: each tick fires any **due**
  Digests, computed from **per-channel cadence + timezone + a watermark**.
- **Catch-up-on-wake.** A laptop that was asleep still emits the Digests it owed
  rather than silently skipping them.

## Consequences

- Digests track behaviour-that-reached-clients, which is the right altitude for
  non-developer Audiences and depends on the canonical Release record from
  [0004](0004-dual-path-deploy-release-mcp.md).
- The watermark + catch-up-on-wake design tolerates the local-first reality
  ([0005](0005-local-first-deployment.md)) of a laptop that sleeps.
- When Babbla moves to an always-on server ([0010](0010-migration-is-ordinary-work.md)),
  the launchd heartbeat becomes a server-side timer/cron; the
  cadence/timezone/watermark logic carries over unchanged.
- Per-channel cadence/timezone is per-channel state that must scale with Project
  count (open risk #5).

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Digests: Release-anchored, headless, scheduled"
- Related: [0004](0004-dual-path-deploy-release-mcp.md), [0005](0005-local-first-deployment.md), [0010](0010-migration-is-ordinary-work.md)
