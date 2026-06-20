# ADR 0003: Read-only by construction

- **Status:** Accepted — **amended 2026-06-20** (runtime-enforcement correction; see Amendment)
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

Enforce read-only with **independent layers**, so no single failure makes Babbla
writable. The guarantee has two halves — *the repo is never written* and *the
agent is confined to read-only GitHub tools* — and each is held by its own layers:

1. **Server-side read-only GitHub MCP** — the `github` MCP server runs with
   `GITHUB_READ_ONLY=1` over stdio; it cannot even expose a writer, regardless of
   the agent's tool surface.
2. **A read-scoped GitHub token** — the credential itself cannot write.
3. **GitHub-only allowlist + settings isolation** — `allowed_tools=["mcp__github__*"]`,
   plus `setting_sources=[]` and `strict_mcp_config=True` so the agent runs in SDK
   isolation: it does **not** inherit the host's Claude settings (`~/.claude`),
   whose `permissions.allow` rules or MCP servers would otherwise widen its tool
   surface. *(The allowlist **pre-approves** the github tools; on its own it does
   not remove the other built-in tools the CLI exposes — see layer 5.)*
4. **`permission_mode="dontAsk"`** — denies anything not pre-approved by the allow
   rules; no interactive escalation path on a headless server. **`bypassPermissions`
   must NEVER be used** — it would defeat this layer and is the single most
   dangerous misconfiguration in the system.
5. **Deny-by-default `PreToolUse` hook on every Ask path** — independent of the
   permission layer, the hook denies every tool that is not a `github` tool
   (plain path), or every tool outside the throwaway per-thread scratch workspace
   (skilled path; see [0015](0015-skilled-answer-path.md)).

## Amendment (2026-06-20): runtime enforcement corrected

The original decision listed a "`Read`/`Grep`/`Glob` allowlist — write/exec tools
are not even available" and "read-only checkouts on disk." Both were inaccurate
once the design pivoted to all-capability-via-MCP ([0002](0002-runtime-agnostic-via-mcp.md)):
there are no local checkouts, and an `allowed_tools` allowlist **pre-approves**
the listed tools — it does **not** remove the CLI's other built-in tools.

A 2026-06-20 incident proved the gap: on the plain Ask path the agent invoked
`Bash`, `Read`, `Write`, and a subagent. Root cause — the plain path set no
`setting_sources`, so the CLI loaded the operator's `~/.claude/settings.json`,
whose `permissions.allow` rules pre-approved those tools, and `dontAsk`
("deny anything *not pre-approved*") duly allowed them. (`dontAsk` was working as
documented; the allow-set had simply been widened by host settings.) The github
repos were never at risk — `GITHUB_READ_ONLY=1` is server-side — but the
capability was real. Remediation: `setting_sources=[]` + `strict_mcp_config=True`
on every path, and the deny-by-default `PreToolUse` hook now on the plain path too
(it was previously only on the skilled path). Full write-up:
[`../incidents/2026-06-20-plain-path-tool-confinement-not-enforced.md`](../incidents/2026-06-20-plain-path-tool-confinement-not-enforced.md).

## Consequences

- Read-only holds even if one layer is misconfigured; an attacker or bug must
  defeat the layers independently.
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
