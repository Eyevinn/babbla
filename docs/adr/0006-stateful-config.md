# ADR 0006: Version-controlled shared config; only personal Digest config is written

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

Babbla needs some state: which Channels map to which Projects, the Subscriptions
they carry, Visibility overrides, and per-person Digest preferences. The read-only
stance ([0003](0003-read-only-by-construction.md)) is about the *Projects* Babbla
reads — but Babbla still needs a place for its own configuration. We want that
state minimal, auditable, and not a database to operate.

## Decision

Keep shared configuration in a **version-controlled `config/channels.yaml`**
(Channels, Subscriptions, Visibility overrides). The **only state Babbla writes at
runtime is the personal Digest config**; everything else is declared in
version-controlled config and changed by editing the file.

## Consequences

- Shared config is reviewable and diffable — changes go through normal
  version-control workflow, consistent with the no-surprise-state ethos.
- Runtime writes are confined to one narrow thing (personal Digest preferences),
  which keeps the read-only-by-construction stance toward Projects intact: Babbla
  never writes to a Project, only to its own small store.
- Visibility defaults from GitHub repo visibility and is **overridable** in this
  config (see [0007](0007-access-visibility-redaction.md)).
- As Project count grows, this file and the per-channel Digest schedule are what
  must scale (open risk #5 in the design proposal); the onboarding routine
  (`audit-repo.sh`) is meant to populate it cleanly.

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Configuration & state"
- Related: [0003](0003-read-only-by-construction.md), [0007](0007-access-visibility-redaction.md)
