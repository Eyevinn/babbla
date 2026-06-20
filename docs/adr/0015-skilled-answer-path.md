# ADR 0015: Skilled answer path — a bounded, read-only loosening for artifacts

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Kun Wu

## Context

Babbla answers over a locked read-only tool surface (ADR 0003): GitHub MCP with
`GITHUB_READ_ONLY=1` + allow-listed agentmemory readers, `permission_mode=dontAsk`,
no builtins. Some valuable per-project work is skill-shaped (draw an architecture
diagram, write a document) and must *produce a file* — which the read-only floor
forbids.

## Decision

Define read-only precisely and bound one loosening:

- **Read-only means** no mutation of the subject GitHub repo (server stays
  `GITHUB_READ_ONLY=1`) and no agentmemory writes (writers never allow-listed).
  Unchanged.
- **The loosening:** when a binding declares `skills:` *and* the call is on the
  interactive Ask path, the answering run gets a **per-thread scratch dir** — a
  deterministic path derived from `thread_ts`, contents wiped + recreated each
  ask, always cleaned in a `finally`, living outside any repo. `permission_mode`
  stays `dontAsk`; the scratch is made writable not by allow-listing builtins
  (which `dontAsk` does not honor) but by a `PreToolUse` hook that returns
  `permissionDecision:"allow"` for `Write`/`Edit`/`Read` resolving **inside** the
  scratch dir, `"deny"` for those outside and for `Bash`, and no opinion for
  everything else. So the write surface is *enforced* to be exactly the scratch dir.

The scratch path is **stable per thread, not per request**, because the CLI
scopes session transcripts by cwd: a fresh random cwd each turn crashes
conversation resume (`No conversation found with session ID`). A stable path
whose contents are wiped between turns still resumes (transcripts live in
`~/.claude`, keyed by the cwd path, not inside the dir). The skilled branch fires
only when a `scratch_key` (the `thread_ts`) is supplied — so **digest/quiz/adr
runs, which pass none, never take the skilled branch** (digest-path skills stay
out of scope).

Skills come only from the Babbla-controlled `config/skills/` pool, never the
subject repo. They load via the SDK's `ClaudeAgentOptions.skills=[names]` switch
(which also enables a scoped `Skill(<name>)` tool — allow-listing `"Skill"` is
deprecated), with `cwd=<scratch>` and `setting_sources=["project"]` so only the
staged pool skills in `<scratch>/.claude/skills/` are discovered and no
Babbla-repo or user-global context leaks. The prompt stays a plain string.

These mechanics were validated by live smoke tests on `claude-opus-4-8` before
build. The original design's `dontAsk` + allow-listed `Write/Edit/Bash` was found
**not** to permit writes; `bypassPermissions` (un-gates MCP writers),
`acceptEdits` (writes escape scratch), and `can_use_tool` (needs streaming input;
bypassed by `allowed_tools`) were each tried and rejected in favor of the hook.
The per-request `mkdtemp` from the original design was found to crash resume and
replaced with the per-thread path.

## Consequences

- The subject repo and agentmemory are never written. MCP writers stay denied
  (not allow-listed, and the hook gives no opinion on MCP tools so `dontAsk`
  governs them). The only write surface is the scratch dir, enforced by the hook.
- `skills=[...]` is a context filter, not a sandbox: unlisted skills are hidden
  and rejected, but files on disk remain readable — hence we stage only the
  opted-in skills into the scratch and the pool holds only vetted read-only
  skills.
- `Bash` is denied on the skilled path; pool skills must produce artifacts via
  `Write`/`Edit`. A future sandboxed-Bash option can revisit this.
- Conversation resume in a skilled thread works (stable per-thread cwd). Resume
  relies on the CLI's session transcripts under `CLAUDE_CONFIG_DIR` (`~/.claude`)
  — the same dependency as the non-skilled path (ADR 0013).
- **Docker-portable.** Skills add no new runtime; they ride the same SDK→CLI path
  as all Q&A. A containerized Babbla needs the `claude` CLI in the image, auth
  (mounted Path-B creds or `ANTHROPIC_API_KEY`), `BABBLA_GITHUB_MCP=binary` (avoid
  Docker-in-Docker), a writable `$TMPDIR` for the ephemeral scratch, a persisted
  `CLAUDE_CONFIG_DIR` for resume, and `config/skills/` baked into the image —
  most of which any Babbla deploy already needs. A clean container *improves*
  isolation. See DEPLOY.md "Running Babbla in Docker".
- Bindings with no `skills:` are byte-for-byte unchanged (regression-guarded).
- Artifact upload needs the Slack `files:write` scope; missing it degrades to a
  logged no-op, never a failed ask.
