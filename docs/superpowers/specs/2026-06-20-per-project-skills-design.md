# Per-Project Read-Only Skills — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-20
**Builds on:** [Always-on Babbla](2026-06-18-always-on-babbla-design.md) (the Agent-SDK runtime + tool surface),
[MyTV Q&A pilot](2026-06-18-mytv-qa-pilot-design.md) (the read-only answering path)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0009 — repo is source of truth; agentmemory optional](../../adr/), the deferred *skill-based-summary spike* in
[ROADMAP](../../ROADMAP.md)

## Why this slice exists

Babbla answers questions about a project over a **locked, read-only tool surface** (GitHub MCP wildcard with
`GITHUB_READ_ONLY=1` + a handful of agentmemory readers). That is the right floor for "ask about a repo," but it
caps what Babbla can *produce*. Some valuable per-project work is skill-shaped: **draw the architecture** of a
service, **write a document** from the repo's history, generate an editorial card, etc. The Claude Code CLI that
the Agent SDK drives already has native **Skills** — task-specific instructions + bundled resources loaded on
demand. This slice lets a project opt into a set of **vetted, read-only skills** that the answering agent can
invoke, producing artifacts Babbla posts back to Slack.

### The core tension and how it's resolved

Babbla is *strictly read-only* and the answering system prompt says "you have no write access and no local
files." But "draw a diagram" / "write a document" skills **must produce an artifact**. We reconcile this by
**defining read-only precisely** and **bounding the one loosening**:

- **Read-only means:** no mutation of the subject GitHub repo (the GitHub MCP stays `GITHUB_READ_ONLY=1`) and
  no agentmemory writes. That invariant is untouched.
- **The one loosening:** when a project has skills, the agent gets an **ephemeral per-request scratch
  workspace** (a temp dir, created per ask, wiped after) and file/bash tools **scoped to that scratch dir** — so
  a skill can write an SVG/HTML/markdown artifact. The scratch dir is outside any repo; nothing the agent does
  there touches the subject project or persists.

So the subject repo is never written, agentmemory is never written, and the only new write surface is a
throwaway scratch directory whose sole purpose is to hand an artifact to Slack.

### Decisions made during brainstorming

- **Any read-only skill, from a vetted pool.** Not just Q&A- or digest-shaping — architecture diagrams,
  document generation, and other read-only skills. Skills come from a **Babbla-controlled, version-controlled
  pool** (`config/skills/`), not arbitrary repo-resident skills, so the set is reviewed and the read-only
  guarantee is enforceable.
- **Opt-in per project, default off.** A binding with no `skills:` behaves exactly as today (locked tool
  surface, no scratch, no skill loading) — zero behavior change for the current pilot.
- **Ask path first.** Skills load on the Q&A Ask path (a user asking "draw the architecture of X" in the
  project's channel/DM). Digest-path skills are a follow-on.
- **Assume headless skill-loading works; validate at build time.** Two build-time checks (V1, V2 below) replace
  a separate spike — the design proceeds on the assumption and the plan verifies the SDK levers early.

### Impact when unconfigured

No `skills:` on any binding → the answering path is byte-for-byte today's: the locked
`(github wildcard, agentmemory readers)` tool surface, no scratch dir, no skill loading. **Zero behavior change.**

## Architecture & data flow

```
ProjectBinding.skills = ["architecture-diagram", "document-writer"]
        │
        ▼
Orchestrator.handle_ask → AgentRunner.run_ask(text, binding, …)
        │  binding.skills non-empty?
        │     no  → today's path (locked tools, no scratch)            ── unchanged
        │     yes → build a SKILLED ClaudeAgentOptions:
        │             • cwd = fresh scratch dir (tempfile.mkdtemp)
        │             • load ONLY binding.skills from config/skills/   (V1: setting_sources/add_dirs)
        │             • tool profile = readers + Skill + scratch-scoped Read/Write/Edit/Bash
        ▼
   agent runs; a skill may write artifact(s) into the scratch dir
        ▼
   collect new files in scratch → SlackPoster.upload_file(...) for each
        ▼
   wipe scratch dir (finally)
```

## Components & files

### Config — `src/babbla/config.py`

```python
@dataclass(frozen=True)
class ProjectBinding:
    ...
    skills: tuple[str, ...] = ()   # names of skills (folders in config/skills/) this project may use
```

- `_parse` reads an optional `skills:` list of strings; absent → `()`.
- Validation: each named skill must exist as a folder under the skills pool (`config/skills/<name>/SKILL.md`);
  an unknown skill name → `ValueError(f"{project}: unknown skill {name!r}")` (fail-fast at config load).
- `Config.skilled_bindings()` convenience (optional) → bindings with a non-empty `skills`.

### Skills pool — `config/skills/`

A Babbla-controlled, version-controlled directory of **vetted read-only skills**, each a standard
`SKILL.md` + bundled resources (the Claude Code skill format). Vetting criteria (documented in a
`config/skills/README.md`): the skill must not require GitHub/agentmemory writers, must not mutate the subject
repo, and must confine any file writes to the working directory (the scratch dir). Examples to seed:
`architecture-diagram/`, `document-writer/`.

This pool is **distinct from Babbla's own `.claude/skills`** — only pool skills a project opted into are loaded,
preserving the classifier/answer isolation that `setting_sources=[]` gives today.

### Read-only skills tool profile — `src/babbla/read_only.py`

A second, bounded tool profile used only when a binding has skills:

```python
SCRATCH_TOOLS = ("Read", "Write", "Edit", "Bash")          # scoped to the scratch cwd
SKILL_TOOL = ("Skill",)
def skilled_allowed_tools() -> tuple[str, ...]:
    # readers (GitHub wildcard + agentmemory readers) + Skill + scratch file/bash tools
    return (*ALLOWED_TOOLS, *SKILL_TOOL, *SCRATCH_TOOLS)
```

- The GitHub MCP server stays `GITHUB_READ_ONLY=1` (so the wildcard still cannot expose a repo writer).
- `Write`/`Edit`/`Bash` operate in the agent's `cwd` (the scratch dir). Permission mode stays `dontAsk`; the
  scratch dir is the blast radius.
- **Never** added: GitHub writers (impossible via the read-only server) and agentmemory writers (still
  allowlisted reader-by-reader). A guard test asserts no writer appears even in the skilled profile.

### `build_agent_config` / `AgentRunner` — `read_only.py` + `agent_runner.py`

`build_agent_config(...)` gains an optional `skills: tuple[str,...]` and `scratch_dir: str | None`. When
`skills` is non-empty:

- `allowed_tools = skilled_allowed_tools()`;
- the returned `AgentConfig` carries the skill names + scratch dir so `AgentRunner` can set `cwd` and the
  skill-loading option on `ClaudeAgentOptions`.

`AgentRunner.run_ask(...)` (skilled branch):

```python
scratch = tempfile.mkdtemp(prefix="babbla-skill-")
try:
    options = ClaudeAgentOptions(
        model=cfg.model,
        system_prompt=cfg.system_prompt,
        allowed_tools=list(cfg.allowed_tools),
        permission_mode=cfg.permission_mode,
        mcp_servers=cfg.mcp_servers,
        cwd=scratch,
        # V1 — the lever that loads ONLY the project's skills from config/skills/:
        #   setting_sources / add_dirs / plugins (confirmed at build time)
        ...skill_loading_option(skills, pool=SKILLS_POOL),
    )
    answer = await self._run(options, text, resume_session_id)
    artifacts = _new_files(scratch)          # files the skill produced
    return CitedAnswer(text=answer.text, session_id=answer.session_id, artifacts=artifacts)
finally:
    shutil.rmtree(scratch, ignore_errors=True)
```

`CitedAnswer` gains an optional `artifacts: tuple[str,...] = ()` (paths in scratch). The orchestrator/poster
upload them before the scratch dir is wiped (so capture paths into memory, or upload inside the `try`).

### Artifact delivery — `src/babbla/digest/poster.py` (or a small `slack_files.py`)

`SlackPoster.upload_file(channel_id, path, *, title=None, thread_ts=None)` — uploads a file to Slack via
`files_upload_v2` (needs the `files:write` scope — a deployment note). The answer flow posts the text reply,
then uploads each artifact (threaded under the reply when there's a `thread_ts`). Missing scope / upload failure
→ log + degrade to a text note ("generated an artifact but couldn't upload it"); never crash the ask.

### Orchestrator — `src/babbla/orchestrator.py`

The Ask paths pass `binding.skills` through to `run_ask`, and after a successful answer, upload any
`answer.artifacts` to the surface the ask came from (channel or DM). No routing/visibility change — skills ride
the existing authorized answer path (a private project's skill output still only goes to its own channel).

### Unchanged

`access.py` (visibility unchanged), `lobby.py`, the digest actions (digest-path skills deferred),
`subscriptions.py`, the classifier isolation (`setting_sources=[]` on the classifier stays — skills load only on
the answer path, only for skilled bindings).

## Build-time validation (replaces a separate spike)

The design assumes the headless Agent SDK can load a pool skill; the plan verifies this **first**, before the
rest is built:

- **V1 — skill loading.** Confirm which `ClaudeAgentOptions` lever loads a single named skill from
  `config/skills/` headlessly without also loading Babbla's own repo skills/CLAUDE.md (candidates:
  `setting_sources` pointed at the pool, `add_dirs`, or a `plugins`/`skills` option). Smoke test: a trivial
  `echo-skill` that the agent invokes and whose effect is observable. If no lever loads a project-scoped skill
  cleanly, fall back to the **profile** degradation below and flag it.
- **V2 — scratch tools + artifact capture.** Confirm `Write`/`Bash` scoped to `cwd` work under the SDK and that
  files the agent writes to scratch are present after the run for capture.

**Fallback if V1 fails** (documented, not built unless needed): a per-project **profile** — extra
system-prompt content (`config/skills/<name>/SKILL.md`'s body injected as supplementary system text) — gives a
large fraction of the value (project glossary, answer/diagram-as-text style) without true skill loading or a
scratch workspace. The artifact path (V2) is then text-only (e.g. a Mermaid/ASCII diagram in the reply).

## Error handling & edge cases

- **No `skills:` on the binding** → today's locked path; no scratch, no skill loading, no artifact handling
  (regression-guarded).
- **Unknown skill name in config** → `ValueError` at config load (fail-fast).
- **Skill produces no artifact** → just the text reply, as normal.
- **Artifact upload fails / missing `files:write`** → log + text-only degrade; ask still answered.
- **Scratch dir always wiped** in a `finally` — a crashed/slow run leaks no disk.
- **Read-only preserved** — GitHub MCP is `GITHUB_READ_ONLY=1`; agentmemory writers never allowlisted; the only
  writable surface is the ephemeral scratch dir (outside any repo). Guard-tested.
- **Isolation preserved** — only the project's declared pool skills load; Babbla's own repo context does not
  leak into answers.
- **Private project** — skill output rides the authorized answer path, so it only reaches the project's own
  channel (visibility model unchanged).
- **Tool-surface guard** — a test asserts `skilled_allowed_tools()` contains no GitHub/agentmemory writer and no
  tool outside the readers + `Skill` + scratch set.

## Testing

Deterministic where possible (fake agent runner captures the options; fake poster captures uploads); the V1/V2
checks are live smoke tests in the plan, not unit tests.

- **`tests/test_config.py`** (extend): `skills:` parses to a tuple; unknown skill → `ValueError`; absent → `()`.
- **`tests/test_read_only.py`** (extend): `skilled_allowed_tools()` includes readers + `Skill` + scratch tools
  and **no writer**; the default (unskilled) `allowed_tools` is unchanged.
- **`tests/test_agent_runner.py`** (extend, fake query_fn captures options): skilled binding → options carry a
  scratch `cwd`, the skill-loading lever, and the skilled tool profile; unskilled binding → today's options
  verbatim; scratch dir is removed after the run (even on exception).
- **`tests/test_artifacts.py`** (new): `_new_files` detects files written to scratch; `SlackPoster.upload_file`
  forwards channel/path/thread_ts; upload failure degrades to a text note.
- **`tests/test_orchestrator.py`** (extend): a skilled answer with artifacts triggers uploads to the ask's
  surface; a private skilled project still only answers on its channel.
- **Guard test**: no writer in the skilled profile (mirrors the existing read-only guard test).

## Scope summary

- **New:** `config/skills/` pool (+ `README.md` vetting criteria, seed skills), `skilled_allowed_tools()` +
  scratch handling (`read_only.py`/`agent_runner.py`), `CitedAnswer.artifacts`, `SlackPoster.upload_file`,
  artifact capture/upload wiring in the orchestrator; `tests/test_artifacts.py`.
- **Changed:** `config.py` (`ProjectBinding.skills` + parse/validate), `agent_runner.py` (skilled branch +
  scratch lifecycle), `read_only.py` (skilled tool profile), `orchestrator.py` (pass skills + upload artifacts),
  `config/channels.yaml` (document the `skills:` field), deployment docs (`files:write` Slack scope).
- **Unchanged:** `access.py`, `lobby.py`, digest actions, classifier isolation.
- **Inert when no `skills:` configured** (zero behavior change to the pilot).

## Out of scope (future / deferred)

- **Digest-path skills** — a project's skill shaping its digest (the original "skill-based summary" idea); ride
  the same pool once the Ask path is proven.
- **Arbitrary repo-resident skills** — loading a skill from the *subject* repo rather than the vetted pool
  (re-opens the read-only-vetting question; deferred deliberately).
- **Write-capable skills** — anything mutating the subject repo or opening a PR (out of bounds for read-only
  Babbla).
- **Per-user skills** — skills tied to a personal subscription rather than a project.
