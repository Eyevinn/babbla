# ADR 0016: Remove agentmemory entirely — the repo is the only source of "why"

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Kun Wu
- **Supersedes:** the "agentmemory is optional local enrichment" clause of
  [0009](0009-repo-is-source-of-truth-for-why.md); refines
  [0010](0010-migration-is-ordinary-work.md)

## Context

[ADR 0009](0009-repo-is-source-of-truth-for-why.md) established that **the project
repo is the source of truth for "why"** and demoted
[agentmemory](https://github.com/rohitg00/agentmemory) to *optional local
enrichment* — read via a four-tool reader allowlist (`memory_recall`,
`memory_smart_search`, `memory_facet_query`, `memory_relations`) only when
`AGENTMEMORY_URL` was set, and otherwise absent.

In practice that enrichment carried no weight:

- The repo path already covers the "why" surfaces (commits, PR bodies, ADRs,
  `README`/`CLAUDE.md`/`docs/`, issues). agentmemory only ever added a developer's
  *local, machine-bound* notes — never present on the always-on container and never
  uploaded anywhere, so the hosted Babbla never had it at all.
- It widened the read-only attack surface: agentmemory exposes writer tools, so the
  allowlist had to enumerate readers tool-by-tool and a guard test had to assert no
  writer leaked in — carrying weight, an npx bridge, and config (`AGENTMEMORY_URL`,
  `AGENTMEMORY_SECRET`) for a path that was off by default.
- It pulled against runtime-agnosticism ([0002](0002-runtime-agnostic-via-mcp.md)):
  a repo file needs no special MCP, but agentmemory is Claude-specific plumbing.

A knob that is off in production, adds surface area, and duplicates what the repo
already provides is not worth keeping.

## Decision

**Remove agentmemory from Babbla entirely.** The read-only GitHub MCP server is the
agent's only tool source. Specifically:

- Drop the `_agentmemory_server` wiring, the `AGENTMEMORY_READERS` / `AGENTMEMORY_WRITERS`
  allowlists, and the `agentmemory_url` / `agentmemory_secret` fields from `Secrets`
  and `build_agent_config`. `allowed_tools` is now exactly `("mcp__github__*",)`.
- Drop the agentmemory paragraph from the answer system prompt.
- Drop `AGENTMEMORY_URL` / `AGENTMEMORY_SECRET` from `.env.example`, the `Dockerfile`,
  and `DEPLOY.md`.
- The guard test now asserts the *absence* of any `mcp__agentmemory__*` tool and of
  an `agentmemory` MCP server, locking the removal in.

The principle of [0009](0009-repo-is-source-of-truth-for-why.md) is unchanged and
strengthened: the repo is not merely the *primary* source of "why" — it is the
**only** source Babbla reads. A developer may still use any local tool (including
agentmemory) to help *author* good PRs/docs; the durable "why" must land in the repo.

## Consequences

- **Smaller, simpler read-only surface.** No writer-bearing MCP to allowlist around;
  the read-only guarantee is now "one server, `GITHUB_READ_ONLY=1`, stdio" with no
  tool-by-tool exceptions.
- **One less dependency and config dimension** — no `npx @agentmemory/mcp` bridge, no
  agentmemory backend, no `AGENTMEMORY_*` env on any host. The hosted and local builds
  are now configured identically on this axis.
- **No behavioral loss in production** — the always-on container never had agentmemory
  configured, so live answers are unaffected.
- **Trade-off:** a developer who *did* run agentmemory locally no longer gets its notes
  folded into answers. Acceptable: that content was local-only and non-reproducible, and
  the remedy (write the "why" into the repo) is the documented recommendation anyway.

## Links

- Refines: [0009](0009-repo-is-source-of-truth-for-why.md) (source-of-truth principle kept; agentmemory clause dropped), [0010](0010-migration-is-ordinary-work.md)
- Related: [0002](0002-runtime-agnostic-via-mcp.md), [0003](0003-read-only-by-construction.md)
- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — "Organizing principle", "The known wall"
- Guide: [`../RECOMMENDATIONS.md`](../RECOMMENDATIONS.md) — the repo-hygiene recommendations that replace local enrichment
</content>
