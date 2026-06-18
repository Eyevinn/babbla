# ADR 0003: Read-only by construction

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

The dominant non-functional requirement for Babbla is that it **never modifies the
projects it reads**. It is an assistant that traces and explains projects; it must
not be able to mutate a repository, even by accident or through a single
misconfiguration. "Read-only by policy" is not enough — a single gating bug would
turn a read-only assistant into one that can write to repos. The guarantee must
hold *by construction*, with no single point of failure.

## Decision

Enforce read-only with **four independent layers**, so no single failure makes
Babbla writable:

1. **`permissionMode: dontAsk`** — the agent has no interactive escalation path.
2. **A `Read` / `Grep` / `Glob` tool allowlist** — write/exec tools are not even
   available to the agent.
3. **A read-scoped GitHub token** — the credential itself cannot write.
4. **Read-only checkouts** on disk.

**`bypassPermissions` must NEVER be used.** It would defeat the gating layer and
is the single most dangerous misconfiguration in the system.

## Consequences

- Read-only holds even if one layer is misconfigured; an attacker or bug must
  defeat all four independently.
- This is the **highest-severity gating risk** in the system and a primary review
  target. We need a test that proves `bypassPermissions` can never sneak in (open
  risk #3 in the design proposal).
- The allowlist constrains what the agent can do, which keeps the capability
  surface small and auditable — reinforced by the all-capability-via-MCP stance
  ([0002](0002-runtime-agnostic-via-mcp.md)).
- The only state Babbla itself writes is personal Digest config — and that is
  *Babbla's* state, not a Project's (see [0006](0006-stateful-config.md)).

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Orchestrator: read-only by construction", Open risk #3
- Related: [0002](0002-runtime-agnostic-via-mcp.md), [0006](0006-stateful-config.md)
