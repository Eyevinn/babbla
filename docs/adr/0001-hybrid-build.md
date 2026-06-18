# ADR 0001: Hybrid build — build only the Release→"why" join

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

Babbla's value proposition is to let colleagues — often non-developers — trace a
project from Slack: "what changed", "why is this code here", and "what is live in
prod right now", without a terminal or tribal knowledge. The hardest and most
valuable part is the **join** between a **Release** (a behaviour reaching an
Environment) and the **commit** and **"why"** behind it.

A background survey found no off-the-shelf tool that performs that
Release→commit→"why" join. Everything *else* Babbla needs already exists as
mature, reusable components: Git/GitHub access, agent orchestration, a memory
store, and Slack transport.

## Decision

Adopt a **hybrid build-vs-buy** posture. **Buy/reuse** everything that already
exists (GitHub read access, the Claude Agent SDK orchestrator, agentmemory, Slack
Bolt) and **build only the one thing nobody else builds**: a small Deploy/Release
MCP that reconstructs the canonical Release record
`{project, environment, version, commit, timestamp, trigger}`. Everything else is
composition over existing tools.

## Consequences

- The net-new surface area is deliberately tiny — one MCP server — which keeps
  the system reviewable and the maintenance burden low.
- The architecture becomes a *composition* of MCP-exposed capabilities; the value
  is in the wiring and the one missing join, not in re-implementing solved
  problems.
- We accept a dependency on the quality and stability of the reused components
  (GitHub MCP server, agentmemory, the agent runtime).
- The Deploy/Release MCP is the project's center of gravity for original work and
  gets the most design attention (see [0004](0004-dual-path-deploy-release-mcp.md)).

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "Build-vs-buy: hybrid"
- Related: [0004](0004-dual-path-deploy-release-mcp.md), [0002](0002-runtime-agnostic-via-mcp.md)
