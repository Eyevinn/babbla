# ADR 0005: Local-first deployment

- **Status:** Accepted. The deployment decision (laptop + Socket Mode) stands; the
  *"agentmemory centralization is the migration blocker"* rationale is
  **superseded by [0010](0010-migration-is-ordinary-work.md)**. The deployment
  *locality* (laptop) is itself superseded by [0011](0011-always-on-container-hosting.md)
  — Babbla now runs always-on in a container; Socket Mode + SQLite still hold.
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

For the pilot, Babbla needs to run somewhere. A hosted server adds operational
surface (a public URL to expose and secure, deployment, secrets management) before
we have proven the product is worth it. Slack Bolt supports **Socket Mode**, where
the app dials out and there is no inbound endpoint.

## Decision

Run Babbla **local-first**: on the developer's laptop, over Slack **Socket Mode**,
with **no public URL** to expose or secure. Digests run as headless invocations
driven by a local launchd heartbeat (see [0008](0008-release-anchored-digests.md)).

> **Original rationale (since superseded).** The proposal framed the
> cloud-migration blocker as *centralizing agentmemory off the laptop* — until
> memory was shared/hosted, Babbla supposedly couldn't move to a server. That
> framing has been overturned: agentmemory left Babbla's critical path
> ([0009](0009-repo-is-source-of-truth-for-why.md)), so there is no memory service
> to centralize, and moving to always-on became ordinary work
> ([0010](0010-migration-is-ordinary-work.md)).

## Consequences

- **Now:** minimal infrastructure. Socket Mode means no inbound exposure; the
  whole system is a process on a laptop plus a small SQLite session store.
- **Limitation:** single-machine. A sleeping laptop drops Asks and skips Digests
  (the launchd heartbeat mitigates the Digest case with catch-up-on-wake, but the
  laptop must be awake to serve Asks).
- The move to an always-on server is a deliberate later phase, not a blocked one
  — see [0010](0010-migration-is-ordinary-work.md). The remaining open question
  for that move is **runtime auth on a headless server** (Path B, the Claude CLI
  subscription login, is user/laptop-bound).

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Local-first, with a known migration blocker", "Slack transport"
- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — Phase 3, Open question #1
- Superseded-in-part by: [0010](0010-migration-is-ordinary-work.md); depends on [0009](0009-repo-is-source-of-truth-for-why.md)
