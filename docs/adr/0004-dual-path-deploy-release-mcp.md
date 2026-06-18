# ADR 0004: Dual-path Deploy/Release MCP

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

The single net-new component Babbla builds (see [0001](0001-hybrid-build.md)) is a
Deploy/Release MCP that produces the canonical Release record:

```
Release = { project, environment, version, commit, timestamp, trigger }
```

But projects vary in how cleanly they expose deployment state. Some use GitHub
Deployments/Statuses; some only have a deploy workflow run; some are Fastly
Compute functions where the live version lives in Fastly. We need **one** Release
abstraction that spans all of these — a GitHub Pages site, a GitHub-Environments
service, and a Fastly function alike.

## Decision

Make the Deploy/Release MCP **dual-path**, with an enrichment step:

- **Clean path** — read the Release directly from GitHub **Deployments /
  Statuses** when a project uses them.
- **Fallback path** — when there are no Deployments, reconstruct the Release from
  the deploy workflow run's **`head_sha`** (the commit the deploy job ran against).
- **Enrichment** — for Fastly Compute functions, look up the **active version** to
  confirm/annotate what is actually live.

## Consequences

- The same Release abstraction works across heterogeneous deploy styles without
  per-project special-casing in the orchestrator.
- The fallback path is less precise than the clean path (a workflow run's
  `head_sha` is a proxy for "what deployed"), so the record should carry its
  `trigger`/source so consumers know the provenance.
- This adapter shape is extensible: the build spine defers an AWS fallback
  adapter as a future path. New deploy styles are new paths into the same record.
- The dual-path design is exercised deliberately across the build spine — clean
  path on MyTV (Pages), Environments+Fastly on the internal service, `head_sha`
  fallback on the private client project.

## Links

- Design proposal: [`../PROPOSAL-design.md`](../PROPOSAL-design.md) — "The single net-new build: Deploy/Release MCP"
- Related: [0001](0001-hybrid-build.md), [0008](0008-release-anchored-digests.md)
