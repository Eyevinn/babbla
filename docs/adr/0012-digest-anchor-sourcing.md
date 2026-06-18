# ADR 0012: Digest anchor sourcing via per-project branch/deploy config

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

[0008](0008-release-anchored-digests.md) established the intent: Digests are
**release-anchored** — the spine of "what changed" is changes reaching an
Environment, not every merge to main. The canonical mechanism ADR 0008 depends on
is the **Release record** from [0004](0004-dual-path-deploy-release-mcp.md), which
remains unbuilt.

Two pilot projects have different deployment patterns:

- **MyTV** is a public GitHub repo that deploys *manually, off-GitHub* (no deploy
  workflow triggers a GitHub Environment). The cleanest available GitHub signal is
  commits landing on the default branch.
- **stream-starter** has a named deploy workflow (`cicd_prod.yml`) that, on a
  successful run, reaches a GitHub Environment — making a successful workflow run
  the proxy for "reached an Environment."

Babbla already has a **read-only GitHub path** ([0003](0003-read-only-by-construction.md))
capable of reading both branch commits and workflow run outcomes. No new MCP
capability is required for either signal.

## Decision

**The digest anchor is per-project configurable** over the existing read-only
GitHub path, with two options:

- **`branch`** — commits landed on the default branch since the last digest. Used
  for projects like MyTV that deploy manually off-GitHub. **Honest caveat:** this
  reports "merged to main", not "confirmed deployed." The digest copy reflects this
  ("landed on main" not "released").
- **`deploy`** — a successful run of a named deploy workflow (e.g.
  `cicd_prod.yml`) that reached a GitHub Environment, since the last digest.
  Used for stream-starter. This is the closest read-only proxy for "reached an
  Environment" without the full Deploy/Release MCP of ADR 0004.

The per-project anchor mode is set in the project's `DigestConfig` in
`config/channels.yaml`.

**First-run behaviour:**

- `branch`: posts a windowed bootstrap (a summary of recent commits on the default
  branch) so the channel is not silent on first run.
- `deploy`: initializes the watermark silently (no post) — a first post requires
  at least one deploy event to anchor to.

**This is the read-only stand-in for ADR 0004's canonical Release record.** The
`environment` variant (filter by a specific Environment name) and true
Release/Deploy-MCP anchors plug into the same per-project config slot when that
path is built.

**Deferred:** stream-starter private-repo onboarding and ADR 0007 redaction for
private repos are deferred; they follow their own spec cycle.

## Consequences

- Refines [0008](0008-release-anchored-digests.md): the release-anchored *intent*
  holds; the *sourcing* is now per-project over the read-only GitHub path.
- MyTV digests are honest: "merged, not confirmed-deployed." This is the right
  trade-off for a project with manual, off-GitHub deployment.
- stream-starter digests are anchored to a successful Environment run — the
  closest available proxy for ADR 0008's canonical release signal.
- The `environment` variant (filter by a named GitHub Environment on a `deploy`
  anchor) and true Release/Deploy-MCP integration plug into the same config slot
  later; no structural change required.
- Private-repo onboarding (stream-starter) and ADR 0007 redaction rules for
  private projects are deferred until stream-starter's onboarding spec.

## Links

- Refines: [0008](0008-release-anchored-digests.md)
- Read-only stand-in for: [0004](0004-dual-path-deploy-release-mcp.md) (canonical Release record, unbuilt)
- Uses: [0003](0003-read-only-by-construction.md) (read-only GitHub path)
- Related: [0007](0007-access-visibility-redaction.md) (private-repo redaction, deferred)
- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — Phase 3
