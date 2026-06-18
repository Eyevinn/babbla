# ADR 0007: Access = Slack membership; Visibility tiers; Incident PII redaction

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

Babbla answers across Projects with different sensitivity: open-source ones anyone
may ask about, internal ones for any workspace member, and private client projects
that must stay restricted. We need an access model that protects client data
without inventing a second authorization system to operate alongside Slack's.

## Decision

- **Access is Slack channel membership.** We do not invent a parallel
  authorization system — if you are in a Project's Channel, you can see that
  Project's shared surface.
- **Visibility tiers** layer on top: **public** (answerable to anyone),
  **internal** (any workspace member, including via the Lobby), **private**
  (client/restricted, answerable only to Channel members). Visibility defaults
  from GitHub repo visibility and is overridable in config
  ([0006](0006-stateful-config.md)).
- The **Lobby answers public + internal** Projects. For **private** Projects it
  uses **"points-don't-reveal"**: it can direct an asker to the Project's Channel
  but never surfaces private content to non-members.
- **Incident PII redaction** is a hard requirement: every Incident summary passes
  a **client-PII redaction check** before posting. Engineering detail is
  internal-safe to summarize; client-sensitive data (names, PII) must never be
  surfaced.

## Consequences

- We inherit Slack's membership model for free — no second auth system to build or
  keep in sync.
- "Points-don't-reveal" makes the Lobby safe to open broadly: discovery doubles as
  onboarding without leaking private projects.
- The redaction check is a **fail-safe gate**, not best-effort. How to make it
  reliable and auditable — and whether it suppresses vs. posts on uncertainty — is
  an open risk (#4) to resolve before the private-project phase.
- Repo-resident "why" inherits repo access control automatically: a private repo's
  "why" is private by construction ([0009](0009-repo-is-source-of-truth-for-why.md)),
  so memory visibility collapses into repo access rather than needing its own
  tier.

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Surfaces & access model", Open risk #4
- Related: [0006](0006-stateful-config.md), [0009](0009-repo-is-source-of-truth-for-why.md)
