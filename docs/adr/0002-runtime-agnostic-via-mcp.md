# ADR 0002: Runtime-agnostic via MCP

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

The orchestrator that drives Babbla is, today, the Claude Agent SDK. But the
agent-runtime landscape moves quickly, and we do not want the design wedded to a
single vendor. We want the ability to swap the runtime (for example, to GitHub
Copilot once its headless/read-only story matures) without re-architecting.

## Decision

Route **all capability through MCP**. The orchestrator reaches the outside world
— GitHub read access, the "why" store, the Deploy/Release MCP — exclusively over
the Model Context Protocol. The runtime sits behind a **thin seam**: because every
capability is an MCP server rather than a runtime-specific integration, the
orchestrator is **model-agnostic** and the runtime is swappable.

## Consequences

- The runtime (Claude Agent SDK now, possibly Copilot later) can change without
  touching the capability layer. MCP is what keeps that door open.
- Capabilities are uniform and inspectable: each is a server with a declared tool
  surface, which also reinforces the read-only allowlist
  (see [0003](0003-read-only-by-construction.md)).
- This composes with the repo-as-source-of-truth principle
  ([0009](0009-repo-is-source-of-truth-for-why.md)): repo-resident "why" is just a
  file behind the GitHub MCP path, so any runtime reads it equally well — no
  runtime-specific memory integration required.
- We pay a small indirection cost (everything is an MCP round-trip) in exchange
  for the portability.

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "MCP layer", "Runtime seam"
- Related: [0001](0001-hybrid-build.md), [0003](0003-read-only-by-construction.md), [0009](0009-repo-is-source-of-truth-for-why.md)
