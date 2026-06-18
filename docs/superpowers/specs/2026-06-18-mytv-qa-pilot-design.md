# MyTV Q&A Pilot — Design

**Status:** Design approved (brainstorm complete) — ready for implementation plan.
**Date:** 2026-06-18
**Owner:** kun.wu@eyevinn.se

## Context

This is the first pilot of **Babbla** — a read-only Slack assistant that lets
colleagues understand and trace projects without a terminal.

The pilot implements the **Ask** pillar (pull, Q&A) against a single Project — **MyTV**
(`github.com/Wkkkkk/MyTV`, public) — and is intentionally **release-agnostic** (no Environment /
Release / Digest / Lobby-routing). Those are later layers.

## Purpose

Let a teammate ask a natural-language question about MyTV in Slack ("why did we move branding
under `static/`?", "what changed in the player live-transport?") and get a cited answer drawn
from the project's **committed, pushed** history and code — never the local working tree.

## Definition of done

A teammate `@`-mentions the bot in a channel, or DMs it, with a question about MyTV. The bot:

1. acks within Slack's 3s window and posts a transient "🔎 looking into it…" placeholder;
2. reads MyTV **read-only** via `github-mcp-server` (code + PRs/commits) and `agentmemory`
   (rationale), all off the **GitHub remote**;
3. replies in-thread with an answer that **cites its sources** (commit SHAs, PR numbers, file
   paths as GitHub links);
4. supports **threaded follow-ups** — replies in the same Slack thread retain conversation
   context (the Agent SDK session is resumed).

One Project (MyTV), two surfaces (channel Shared Ask + DM Private Ask). No release-awareness,
no Lobby routing, no Digest.

## Locked decisions

| Decision | Choice |
|---|---|
| Orchestrator language | **Python** |
| Agent surface | **`claude-agent-sdk`** (local agent loop, permission modes, local stdio MCP) |
| Model | **`claude-opus-4-8`** (default); one-line config swap to `claude-sonnet-4-6` |
| Data source | **MCP-only off the GitHub remote** — no local clone |
| Slack surfaces | **Channel** (Shared Ask, `app_mention`) **+ DM** (Private Ask, `message.im`) |
| Conversation | **Threaded** — per-`thread_ts` session continuity |

### Core principle

> **Read the canonical git remote, never a working tree.** Babbla's data source is
> GitHub (pushed commits / PRs / branches), never the orchestrator host's filesystem. This is
> what makes it safe to share over a team and host-independent, and it structurally excludes
> uncommitted edits, untracked files, and gitignored secrets (MyTV's `.env`, `dev.db`).

## Architecture — why the Claude Agent SDK

The Anthropic stack offers three surfaces; the project's constraints select one:

- **Managed Agents** (Anthropic-hosted loop + cloud container) — **rejected.** Tools execute in
  Anthropic's container, which can't reach the **local stdio `agentmemory`** MCP, and it
  contradicts the local-first decision.
- **Raw `anthropic` Messages API + hand-rolled loop** — **rejected.** Would require
  hand-building the agent loop, MCP wiring, *and* the permission/allowlist layer that makes
  read-only enforceable.
- **`claude-agent-sdk` (Python)** — **chosen.** Provides local stdio MCP servers, a tool
  allowlist, and permission modes natively. Matches the project's design of record.

> Exact `claude-agent-sdk` option names (permission mode, allowlist fields, session
> resume/continue, MCP server config) are pinned against the Agent SDK docs during the
> implementation-plan phase; this design fixes the *intent* and *invariants*, not the binding.

## Components

Each component is small, single-purpose, and independently testable.

1. **Slack adapter** (`slack_bolt`, Socket Mode). Subscribes to `app_mention` (channel = Shared
   Ask) and `message.im` (DM = Private Ask). Acks < 3s; posts the "🔎 looking into it…"
   placeholder and later edits it with the answer; threads all replies. Knows nothing about
   GitHub or the agent — pure Slack I/O.
2. **Ask orchestrator** (the seam). `(text, thread_key, surface) → cited answer`. Resolves the
   `thread_ts → session` mapping and the Project (MyTV) for the surface; invokes the agent
   runner; formats the result. Knows nothing about Slack transport details.
3. **Agent runner** (`claude-agent-sdk`). Configures the read-only agent — the two MCP servers,
   the tool allowlist, the system prompt, the model — and runs/resumes the query.
4. **MCP layer.** `github-mcp-server` (`--read-only`, **stdio**) reading `Wkkkkk/MyTV`;
   `agentmemory` (local launchd service) with **read/search tools only**.
5. **Session store.** Maps `thread_ts → session_id` (+ project), with TTL eviction. The only
   state the pilot writes. **SQLite** (durable across restarts).
6. **Config** (`config/channels.yaml`). Maps channel/DM → Project (MyTV) + repo coordinates +
   visibility (public). Version-controlled.

## Data flow — one Ask

```
Slack event → adapter acks (<3s) + posts "🔎 looking into it…"
   → orchestrator resolves thread→session, project=MyTV
   → agent runner runs read-only query
       → model drives github-mcp-server (tree / read_file / search + PRs/commits)
                  + agentmemory (recall) over the REMOTE
       → cited answer (commit SHAs, PR #s, file paths as GitHub links)
   → adapter edits the placeholder → posts the answer in-thread
Follow-up in same thread → resume session → continue
```

The **orchestrator is the primary testable seam**: drive it with a fake Slack event and a
stubbed agent runner; test the agent runner with stubbed MCP servers — no live Slack/GitHub for
unit tests.

## Read-only enforcement — defense in depth

Six layers, so no single mistake opens a write path:

1. **No write tools are granted.** The agent's entire tool set is the two MCP servers' read
   tools. No built-in filesystem (`Read`/`Write`/`Edit`), no `Bash`, no web. MCP-only + zero
   filesystem tools = no write surface.
2. **`github-mcp-server --read-only`, stdio** (not http — a read-only-flag bug exists in http
   mode). Exposes only read tools (`get_file_contents`, `search_code`, `list_commits`,
   `get_pull_request`…), never create/update/merge.
3. **`agentmemory`: read/search tools only, via explicit allowlist.** agentmemory exposes
   *mutating* tools (`memory_save`, `memory_action_create/update`, `memory_governance_delete`…).
   The allowlist names only readers (`memory_recall`, `memory_smart_search`,
   `memory_facet_query`, `memory_relations`) and excludes every writer. **This is the
   easiest layer to get wrong.**
4. **Permission mode auto-runs only allowlisted tools; never `bypassPermissions`** — confirmed
   to skip the gating that would otherwise block a non-allowlisted tool. Headless server: no
   interactive prompts, but off-allowlist tools are denied, not bypassed.
5. **Read-scoped GitHub token** — fine-grained PAT, read-only (Contents + Metadata + Pull
   requests + Issues), scoped to MyTV; supplied via env/secret, never in code or
   `channels.yaml`.
6. **Reads the remote, never a working tree** — `github-mcp-server` hits the GitHub API, so
   local working-tree state (uncommitted/untracked/gitignored) is structurally invisible.

### Branch scope

An Ask defaults to the **default branch (`main`) = the shared truth**. The agent may inspect
PRs / other *pushed* branches when the question calls for it ("what's in PR #58?"). It never
sees the local working tree.

## Threading & session lifecycle

- `thread_ts` is the key. First message in a thread → **new** session; store
  `thread_ts → session_id`. Reply in the same thread → **resume** the session (prior context
  retained).
- **TTL eviction** (e.g. 24h idle) bounds storage and honors the "Private Ask is ephemeral"
  rule — after eviction, a fresh reply starts a new session. Channel Shared Asks keep their
  visible transcript in Slack; the session is kept for follow-ups within TTL.
- **Concurrency:** fully async — many Asks run concurrent queries. A per-thread lock prevents
  two rapid replies in one thread from double-resuming.

## Error handling

- **3s ack:** ack the Socket Mode event immediately; all slow work runs async behind the
  placeholder.
- **Query / MCP / API failure:** caught per-Ask (one failure never crashes the process); the
  placeholder is **edited** to a graceful in-thread error ("⚠️ Couldn't answer that right now —
  GitHub was unreachable"), with detail logged. No dangling "🔎 looking into it…".
- **No answer found:** the system prompt instructs the agent to say "I don't know / not in
  MyTV's history" rather than guess; since it can only cite what the read tools returned, it
  can't fabricate sources.
- **Safety on every path:** failures cannot write anything (no write tools exist), so all error
  paths are inherently safe.

## Testing strategy (TDD)

Seams make most behavior testable without live Slack/GitHub.

1. **Orchestrator** — fake event + stubbed agent runner → assert thread→session mapping,
   placeholder/edit behavior, answer formatting, error path.
2. **Read-only guard test (load-bearing regression test)** — assert the configured tool set is a
   **subset of the known read-only allowlist** and that `bypassPermissions` is never set. Fails
   loudly if anyone later adds a write tool or an agentmemory writer.
3. **Agent runner** — stubbed MCP → assert read-only config and that answers carry citations.
4. **Config** — `channels.yaml` parses and maps the channel/DM → MyTV.
5. **Smoke / integration** (manual or CI-with-token) — ask the real public MyTV repo a known
   question (e.g. "what changed in the weekly progress site?") and assert the answer cites a
   real PR/commit. The "does the loop actually work" test.

Because there's an LLM in the loop, content is non-deterministic — tests assert **invariants**
(cited a PR, didn't error, used only read tools), not exact wording.

## Configuration & secrets

- `config/channels.yaml` (version-controlled): channel/DM → Project (MyTV) + repo coords +
  visibility. For the pilot, a single MyTV entry.
- Secrets (env/secret store, never committed): Slack bot token + app-level token (Socket Mode),
  GitHub read-scoped PAT, and whatever the Agent SDK uses for Claude auth.

## Out of scope (future layers, not this pilot)

- Release-awareness (Environment/Release, the dual-path Deploy/Release MCP)
- Digests (shared + personal, launchd-scheduled)
- Lobby routing (ask without naming the project) and entitlement-aware visibility tiers
- Incident summaries + client-PII redaction
- Wiring additional repos (stream-starter, simulcast, magni, …)

## Decided defaults (overridable at review)

- **Runtime:** the pilot runs as a **foreground process**; launchd is deferred to Digests.
- **Session store:** **SQLite** for the durable thread→session map.
- **Model:** **`claude-opus-4-8`**; switch to `claude-sonnet-4-6` only if cost/latency on a
  high-volume shared bot warrants it.

## To pin during planning (research, not open questions)

- Exact `claude-agent-sdk` option names — permission mode, tool allowlist fields, session
  resume/continue, MCP server config — verified against the Agent SDK docs.
