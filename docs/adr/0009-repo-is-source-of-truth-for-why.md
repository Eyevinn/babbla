# ADR 0009: The project repo is the source of truth for "why"; agentmemory is optional local enrichment

- **Status:** Accepted — the source-of-truth principle stands; the "agentmemory is
  optional local enrichment" clause is **superseded by [0016](0016-remove-agentmemory.md)**
  (agentmemory removed entirely).
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

The original design leaned on **agentmemory** as the per-commit "why" store, while
acknowledging a problem: the commit↔session linkage is **forward-only and
near-empty**, so on day one the "why" is really **commit messages + PR bodies +
`docs/adr/`**, with memory as enrichment that compounds over time. That left
agentmemory in Babbla's critical path and framed *centralizing it off the laptop*
as the cloud-migration blocker (see [0005](0005-local-first-deployment.md)).

Three commitments sharpen this into a cleaner principle:

1. The project repo already holds the "why" — `README`, `CLAUDE.md`, `docs/`,
   architecture notes, ADRs, commit messages, PR bodies, issues.
2. Babbla must require **no changes** to the projects it reads (no pollution).
3. A repo file needs no special MCP, so any runtime
   ([0002](0002-runtime-agnostic-via-mcp.md)) can read it.

## Decision

**The project repo is the source of truth for "why."** Babbla reads a project's
existing repo-resident surfaces over the read-only GitHub path it already has
([0003](0003-read-only-by-construction.md)).

**agentmemory is optional local enrichment, never required.** It is removed from
Babbla's critical path. At most it is a *local drafting aid* a developer may use to
help author good PRs/docs; its contents are never uploaded anywhere and never a
dependency for answering.

**No pollution.** Babbla requires no new artifacts or mandated files in the
projects it reads. We *recommend* documentation hygiene (advisory only). Sparse
docs produce thinner answers, never failure — **graceful degradation**.

## Consequences

- "Why" inherits the repo's access control for free: a private repo's "why" is
  private by construction; no second auth system
  ([0007](0007-access-visibility-redaction.md)).
- Runtime-agnostic: repo-resident "why" is just a file, so a future runtime reads
  it as easily as Claude does ([0002](0002-runtime-agnostic-via-mcp.md)).
- The agentmemory-centralization "wall" dissolves — there is no shared memory
  service left to host (the consequence formalized in
  [0010](0010-migration-is-ordinary-work.md)).
- **Trade-off:** on a public repo (e.g. MyTV) the "why" is public by construction;
  this removes the option of internal-only rationale on a public repo. Acceptable
  under the no-pollution stance (open question #2).
- **Trade-off:** if a project's prose "why" proves too thin for good answers at
  scale, we may revisit whether a richer (still repo-resident) structure is worth
  the convention — not now (YAGNI, open question #3).

## Links

- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — "Organizing principle", "The known wall", Open questions #2–#3
- Supersedes the "why"-via-agentmemory framing in [`../PROPOSAL-design.md`](../PROPOSAL-design.md) ("MCP layer")
- Related: [0002](0002-runtime-agnostic-via-mcp.md), [0007](0007-access-visibility-redaction.md), [0010](0010-migration-is-ordinary-work.md)
