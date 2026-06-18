# ADR 0011: Always-on container hosting

- **Status:** Accepted
- **Date:** 2026-06-18
- **Deciders:** Kun Wu

## Context

[0010](0010-migration-is-ordinary-work.md) established that moving Babbla to an
always-on server is ordinary engineering work, not blocked on agentmemory
centralization. The remaining open question before hosting was **runtime auth on a
headless server** (Roadmap open question #1): Path B (Claude CLI subscription
login) is user/laptop-bound and cannot be used on a headless server without a
human interactive session.

The deployment target is **Eyevinn OSC** (Open Source Cloud), which supports
always-on instances with injected secrets and persistent volumes — a natural fit
for a thin connector that owns only a small SQLite session store and config.

The GitHub MCP server runs as a bundled binary over `stdio` with
`GITHUB_READ_ONLY=1`. There is no docker-in-docker scenario: the binary is bundled
into the container image and launched as a subprocess. The read-only guarantee is
provided by the transport (`stdio`) and the flags passed to the binary, not by
Docker isolation.

## Decision

**Babbla runs always-on as a portable container.**

- **Build:** the `Dockerfile` is built locally and the image is shipped to
  Eyevinn OSC.
- **Runtime:** Eyevinn OSC provides an always-on instance, injected secrets, and a
  persistent volume for the SQLite session store.
- **GitHub MCP:** a bundled binary, launched as a `stdio` subprocess with
  `GITHUB_READ_ONLY=1`. No docker-in-docker; the read-only guarantee is the
  transport + flags.
- **Headless auth:** a single shared `ANTHROPIC_API_KEY` service key is injected
  as an OSC secret. This resolves Roadmap open question #1. Locally, Path B
  (Claude CLI subscription login) still requires no key and is unchanged.
- **No per-user/per-role identity.** Access control stays Slack-membership-based
  as decided in [0007](0007-access-visibility-redaction.md). There is no per-user
  or per-role API key.
- **agentmemory** wiring remains optional/off on the server, consistent with
  [0009](0009-repo-is-source-of-truth-for-why.md). No shared memory service is
  hosted.

## Consequences

- Asks and Digests survive a sleeping laptop — the original Phase 3 goal.
- The deployment-locality note in [0005](0005-local-first-deployment.md) is
  superseded: Babbla no longer runs only on the developer's laptop. Socket Mode
  and SQLite still hold (see 0005 status note).
- Path B (subscription login) continues to work locally for development with no
  `ANTHROPIC_API_KEY` required; the server uses the injected service key.
- The single shared service key is the simplest headless-auth story. Per-user or
  per-role keys are explicitly deferred (YAGNI; out of scope).
- OSC secrets + persistent volume are operational prerequisites before deploying.
  The runbook lives in `docs/superpowers/specs/2026-06-18-always-on-babbla-design.md`.

## Links

- Roadmap: [`../ROADMAP.md`](../ROADMAP.md) — Phase 3, Open question #1 (resolved here)
- Supersedes the deployment-locality note in [0005](0005-local-first-deployment.md)
- Related: [0003](0003-read-only-by-construction.md), [0009](0009-repo-is-source-of-truth-for-why.md), [0010](0010-migration-is-ordinary-work.md)
