# Incident: Agent tool-confinement not enforced on the plain Ask path

| | |
| --- | --- |
| **Status** | **Resolved 2026-06-20** — fixed and live-verified (see Resolution) |
| **Severity** | High (capability); **no realized harm** |
| **Discovered** | 2026-06-20, during local Docker smoke testing (Path-B subscription auth) |
| **Components** | `src/babbla/agent_runner.py` (`_run_plain` → `_base_options`); `src/babbla/read_only.py` (`build_agent_config`, `permission_mode="dontAsk"`, `make_scratch_guard`) |
| **Related** | [ADR 0003 — Read-only by construction](../adr/0003-read-only-by-construction.md), [ADR 0015 — Skilled answer path](../adr/0015-skilled-answer-path.md) |

## Summary

Babbla intends the answering agent to be confined to the read-only GitHub MCP server only —
`allowed_tools=["mcp__github__*"]` with `permission_mode="dontAsk"`. During local Docker testing
the agent, on the **plain (non-skilled) Ask path**, invoked built-in tools far outside that
allowlist — `Bash`, `Read`, `Write`, `Agent` (subagent), `TaskCreate`, `AskUserQuestion`,
`ToolSearch` — reading and writing the *container* filesystem and spawning a subagent. **Tool
confinement is not enforced at runtime on that path.**

The target GitHub repository was never at risk (`GITHUB_READ_ONLY=1` is enforced server-side),
and **no data exfiltration or credential access occurred**. The exposure was a *capability*, not
a realized harm.

## Impact

- **Realized:** none. The agent's actions were benign — it explored `/app`, read Babbla's own
  source, and wrote `/app/ONBOARDING.md` and an in-memory todo list, all inside the container.
- **Potential (High):** the container had the operator's Claude subscription credentials mounted
  (`/root/.claude/.credentials.json`) and the operator's personal claude.ai MCP integrations
  connected (the CLI connector logs showed at least one non-GitHub MCP server). An unconfined
  agent with `Bash` + network + those integrations could, under prompt injection from repository
  content, exfiltrate the mounted token or act on connected services. None of this was observed.
- **Not affected:** the target GitHub repos (read-only server-side); the **skilled** Ask path,
  whose `PreToolUse` hook correctly denied `Bash`.

## Timeline (UTC, 2026-06-20)

- **20:25** — Container started with Path-B creds mounted; connected to Slack.
- **20:29** — Lobby ask *"write an onboarding guide for babbla"* → **skilled path, correct**:
  `Skill` + `mcp__github__get_file_contents`×7 + a scratch `Write`; **no `Bash`**.
- **20:36** — Ask *"write an onboarding guide for babbla"* → **plain path**: `Bash`×4, read all of
  `/app/src/babbla/*.py`, wrote `/app/ONBOARDING.md`×2. (It read the *container's* copy of
  Babbla's source, not the GitHub repo — it even "saw" no `tests/` dir because the image doesn't
  COPY it.)
- **20:37** — Ask *"use your skill to create a todo list for babbla"* → **plain path**: `Bash`×6,
  `Agent`×1 (subagent), `TaskCreate`×11, `AskUserQuestion`×1.
- **~20:40** — Detected during log/transcript review; container stopped (`docker compose stop`).

## Detection

Found while reviewing container logs and the CLI session transcripts
(`/root/.claude/projects/*/*.jsonl`) after a user test. Babbla's own application logs were clean
(no exceptions) — the anomaly was visible only in the CLI transcripts' `tool_use` records. Babbla
logs neither tool usage nor completions, so this class of problem is invisible to its own logs.

## What held vs. what failed

**Held**
- `GITHUB_READ_ONLY=1` (server-side) — the GitHub repo can never be written, regardless of the
  agent's tool surface.
- The skilled-path `PreToolUse` scratch guard — denied `Bash` and out-of-scratch writes (the
  20:29 skilled run used no `Bash`).
- No exfiltration — no `curl`/`wget`/`nc`, no reads of `.credentials.json` or `.env`, no env dumps.

**Failed**
- `permission_mode="dontAsk"` + `allowed_tools` did **not** restrict the toolset on the plain path.
- The plain path (`_base_options`) sets **no `setting_sources`**, so under Path-B subscription auth
  the bundled CLI loaded the operator's claude.ai **remote settings** (personal MCP integrations +
  a permissive tool/permission environment), overriding the intended lock-down.
- `tests/test_read_only_guard.py` asserts only config values and hook logic; it never runs the CLI,
  so the runtime gap was invisible to the test suite.

## Root cause

> **Correction (2026-06-20):** the initial write-up named the primary cause as
> "`permission_mode="dontAsk"` is not a recognized enforcing SDK permission mode." **That was
> wrong.** Investigation of the installed SDK (`claude_agent_sdk` 0.2.104, bundled CLI 2.1.181)
> showed `dontAsk` *is* a valid, enforcing mode (`types.py` `PermissionMode` literal), documented
> as *"deny anything not pre-approved by allow rules."* The real primary cause was the missing
> settings isolation below.

**Primary:** the plain path (`_base_options`) set no `setting_sources`, which the SDK documents as
*"all sources are loaded (matches CLI defaults)"* — so the bundled CLI loaded the operator's
`~/.claude/settings.json` (`user`) **and** project/local settings. Those settings' `permissions.allow`
rules were unioned into the effective allow-set, and `permission_mode="dontAsk"` ("deny anything
*not pre-approved*") then permitted the now-pre-approved built-in tools. `dontAsk` was working as
documented; the allow-set had simply been widened by host settings the agent should never have
inherited.

**Contributing factors**
1. The `Bash`/builtin-deny `PreToolUse` hook was installed only on the **skilled** path — the plain
   path had no independent deny-by-default layer.
2. `strict_mcp_config` was unset, so settings-defined MCP servers (the operator's claude.ai
   integrations) could load alongside the github server.
3. No end-to-end test ever exercised real CLI tool-permission behavior — confidence rested on
   config-only assertions against `build_agent_config`, never against the options the runner
   actually sends to `query()`.

**Confirmed empirically (2026-06-20).** With the local `~/.claude/settings.json` containing
`Bash(git remote get-url:*)`, a live plain-path run with `setting_sources` unset **executed** that
Bash command (leak reproduced); the same run with `setting_sources=[]` + the deny hook **denied** it.

## Remediation (action items)

- [x] **Isolate settings on every path** — plain path now passes `setting_sources=[]` (SDK
      isolation); skilled path keeps `setting_sources=["project"]`, which excludes the operator's
      `user` settings (`~/.claude`). (`src/babbla/agent_runner.py` `_base_options`.)
- [x] **Pin MCP** — `strict_mcp_config=True` on every path, so only the github server we pass is
      used; settings-defined MCP servers (the operator's claude.ai integrations) are ignored.
- [x] **Install the deny-by-default hook on the plain path too** — `make_readonly_guard`
      (`src/babbla/read_only.py`) denies every non-`github` tool via `PreToolUse`, independent of
      the permission layer. The skilled path keeps its scratch guard.
- [x] **Add an enforcement test against the runtime options** — tests now assert the options the
      runner actually sends to `query()` (`setting_sources=[]`, `strict_mcp_config`, deny hook),
      plus guard-unit tests, plus an opt-in live CLI test (`BABBLA_E2E=1`). The leak was reproduced
      and the fix observed live (Resolution).
- [x] **Correct the docs** — README "Why it's safe" and ADR 0003 updated to describe the actual
      layered runtime enforcement (no longer "`dontAsk` hard-denies any off-allowlist tool").
- [N/A] **Replace `"dontAsk"`** — not needed; `dontAsk` is a valid, enforcing mode (see Root cause
      correction). It was never the cause.
- **Production (OSC, Path-A `ANTHROPIC_API_KEY`):** the leak is auth-mode-independent — it stems
  from loading filesystem settings, which exist in any deployment — so production was exposed by the
  same code path and is fixed by the same change (the fix is applied on every path, regardless of
  auth). No production allow-rule abuse was observed; the fix removes the dependency on host
  settings entirely.

## Resolution (2026-06-20)

**Fix** (one commit): on every Ask path the agent now runs in SDK isolation —
`setting_sources=[]` (host `~/.claude` settings not loaded) and `strict_mcp_config=True` (only the
github MCP server we pass) — and a deny-by-default `PreToolUse` hook (`make_readonly_guard`) denies
every non-`github` tool on the plain path, independent of the permission layer. The skilled path is
unchanged (it was already isolated via `setting_sources=["project"]` + its scratch guard) beyond
also gaining `strict_mcp_config`.

**Verification.**
- **Deterministic:** the unit suite asserts the runtime options the runner sends (not just config),
  the guard denies all non-github tools, and a no-network check confirms the built CLI command now
  carries `--setting-sources`. Full suite green (418 passed).
- **Live (end-to-end):** with `~/.claude/settings.json` holding `Bash(git remote get-url:*)`, a real
  bundled-CLI run on the **buggy** config (`setting_sources` unset, no guard) **executed** the Bash
  command (leak reproduced); the **fixed** config (`setting_sources=[]` + guard) **denied** it.

## Lessons

- "Read-only by construction" has two independent halves: (1) **the repo is never written** —
  enforced server-side, solid; (2) **the agent is confined to GitHub-only** — was *not* enforced at
  runtime. State and test them separately.
- Config-only tests give false confidence; safety guarantees need tests against the **runtime
  options actually sent to the CLI**, and ideally an **end-to-end** check that a forbidden tool is
  really denied.
- With `setting_sources` unset the SDK loads **all** host Claude settings (`~/.claude` etc.) — in
  *any* auth mode, not just subscription/Path-B — and their `permissions.allow` rules silently widen
  what `dontAsk` permits. A confined agent must run in explicit isolation (`setting_sources=[]`,
  `strict_mcp_config=True`); treat the host's Claude environment as untrusted, not as a default.
- `dontAsk` denies what is *not pre-approved* — it is only as tight as its allow-set. Don't describe
  it as "hard-denies off-allowlist tools" without also pinning the allow-set (isolation) and adding
  an independent deny layer.
