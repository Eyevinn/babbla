# Per-Project Read-Only Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project opt into a set of vetted, read-only Claude Code Skills the answering agent can invoke, producing artifacts (diagrams/docs) that Babbla uploads back to Slack — without ever loosening the read-only guarantee on the subject repo or agentmemory.

**Architecture:** A binding gains an optional `skills:` list. When present *and* the call carries a `scratch_key` (the `thread_ts`, passed only by the interactive Ask paths — so digests never go skilled), `AgentRunner.run_ask` takes a *skilled branch*: it uses a **stable per-thread scratch dir** (a deterministic path derived from `thread_ts`, wiped + recreated each ask), copies only the opted-in skills from the `config/skills/` pool into `<scratch>/.claude/skills/`, and runs the SDK with `cwd=scratch`, `setting_sources=["project"]`, and `skills=[names]` (the SDK's first-class skill switch, which also auto-enables the `Skill` tool). The scratch path is stable across a thread's turns because the CLI scopes session transcripts by cwd — a fresh random cwd per turn crashes resume. `permission_mode` stays `dontAsk` (so MCP writers remain denied) and a **`PreToolUse` hook** allows `Write`/`Edit`/`Read` **only inside the scratch dir** and denies `Bash` — this *enforces* the scratch boundary rather than assuming it. Files the skill writes to scratch are read into memory as artifacts, the scratch is wiped, and the Slack adapter uploads the artifacts threaded under the answer. With no `skills:` configured the path is byte-for-byte today's.

**Validated up front (Task 1, live):** the discovery/isolation lever and the `dontAsk` + `PreToolUse`-hook write path were both confirmed against the real SDK + CLI on `claude-opus-4-8` before this plan was finalized. Note the corrections from the original design: builtins are **NOT** added to `allowed_tools` (under `dontAsk` that doesn't permit them); instead the hook gates them. `bypassPermissions`/`acceptEdits`/`can_use_tool` were each tried and rejected (they either break the read-only guarantee, don't scope writes, or require a streaming-input refactor).

**Tech Stack:** Python 3.14, `claude-agent-sdk`, `slack-bolt`/`slack-sdk` (`files_upload_v2`), `pyyaml`, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), src layout under `src/babbla/`.

## Global Constraints

- **Read-only invariant (ADR 0003) is untouched:** GitHub MCP stays `GITHUB_READ_ONLY=1`; no agentmemory **writer** tool is ever allow-listed (the readers stay allow-listed reader-by-reader). The *only* new write surface is the per-thread scratch dir, which lives outside any repo and is always wiped.
- **`permission_mode` stays `dontAsk`.** Builtins (`Write`/`Edit`/`Read`/`Bash`) are deliberately **not** in `allowed_tools` (under `dontAsk` that would not permit them anyway). The skilled run adds a `PreToolUse` hook that returns `permissionDecision:"allow"` for `Write`/`Edit`/`Read` whose target resolves inside the scratch dir, `"deny"` for those outside and for `Bash`, and `{}` (no opinion) for everything else — so MCP readers/writers stay governed exactly as today. **Never** use `bypassPermissions` (un-gates MCP writers) or `acceptEdits` (writes escape the scratch dir). Both were tried in Task 1 and rejected.
- **Zero behavior change when unconfigured:** a binding with no `skills:` must produce the exact `ClaudeAgentOptions` it does today (no `cwd`, no `setting_sources`, no `skills`, no hooks, readers-only tools). This is regression-guarded by an explicit test.
- **Skilled branch needs a stable per-thread cwd (validated, Task 1).** The CLI scopes session transcripts by cwd, so a fresh `mkdtemp` per request **crashes** resume (`No conversation found with session ID`). The scratch is a deterministic path derived from the `scratch_key` (`thread_ts`); its *contents* are wiped per ask but the *path* is stable across the thread's turns, so resume works (transcripts live in `~/.claude`, keyed by the cwd path, not inside the dir). The skilled branch fires only when `scratch_key` is supplied — the Ask paths pass `thread_ts`; **digest/quiz/adr paths pass none, so a skilled binding's digests never take the skilled branch** (digest-path skills stay out of scope).
- **Skill enablement uses `ClaudeAgentOptions.skills`, never `allowed_tools=["Skill"]`** — passing `"Skill"` to `allowed_tools` is deprecated; setting `skills=[...]` enables the `Skill` tool and skill discovery in one place (the SDK appends a scoped `Skill(<name>)` entry per name).
- **Skills come only from the Babbla-controlled pool** `config/skills/`, never from the subject repo.
- **Isolation:** the skilled run must not load Babbla's own repo context or the user-global `~/.claude` context. Achieved with `cwd=<clean scratch>` + `setting_sources=["project"]` + `skills=[names]`. Verified live: a "list your skills" probe returned only the staged skill.
- **String prompt only:** the skilled path keeps passing the prompt as a `str` (no streaming-input/`can_use_tool` refactor — that path was rejected in Task 1).
- **Follow repo idioms:** `from __future__ import annotations`, frozen dataclasses, `_parse_*` helpers in `config.py`, one-failed-ask-never-crashes-the-process error handling.
- Run the suite with `python -m pytest -q` (deselect live tests with `-m "not integration"`).

## File Structure

**New files**
- `config/skills/README.md` — vetting criteria for the pool (what makes a skill admissible).
- `config/skills/architecture-diagram/SKILL.md` — one real seed skill (read-only; writes an HTML artifact into cwd).
- `tests/manual/skill_pool/echo-skill/SKILL.md` — throwaway skill used only by the V1 smoke test.
- `tests/manual/skill_loading_smoke.py` — live V1/V2 smoke test (not a unit test; not run in CI).
- `tests/test_artifacts.py` — unit tests for artifact capture + Slack upload.
- `docs/adr/0015-skilled-answer-path.md` — ADR for the bounded loosening + the chosen SDK lever.

**Modified files**
- `src/babbla/config.py` — `ProjectBinding.skills`, parse + filesystem validation against the pool.
- `src/babbla/read_only.py` — `AgentConfig.skills`, `build_agent_config(skills=...)` (allowed_tools **unchanged** — builtins are hook-gated, not allow-listed), `_within()`, `make_scratch_guard()` (PreToolUse hook factory), `skill_loading_kwargs()` (returns `cwd` + `setting_sources` + `skills` + `hooks`).
- `src/babbla/agent_runner.py` — `Artifact`, `CitedAnswer.artifacts`, `Secrets.skills_pool`, skilled `run_ask` branch (scratch lifecycle, skill staging, artifact capture).
- `src/babbla/digest/poster.py` — `SlackPoster.upload_file(...)`.
- `src/babbla/slack_adapter.py` — upload artifacts after the answer in `process_ask` / `process_lobby_ask`.
- `src/babbla/orchestrator.py` — preserve `artifacts` through `handle_lobby_ask` (it reconstructs `CitedAnswer`).
- `src/babbla/app.py` — `Secrets.skills_pool` from `BABBLA_SKILLS_POOL` (default `config/skills`).
- `tests/test_config.py`, `tests/test_read_only_guard.py`, `tests/test_agent_runner.py`, `tests/test_orchestrator.py` — extend.
- `config/channels.yaml`, `DEPLOY.md` — document the `skills:` field and the `files:write` scope.

---

### Task 1: V1/V2 spike — confirm headless skill loading + scratch artifacts (LIVE) — ✅ DONE

**Status: VALIDATED** against the real SDK (`claude-agent-sdk` 0.2.104) + CLI (`claude` 2.1.183) on `claude-opus-4-8`. Reproducible scripts live in the session scratchpad (`scratchpad/skilltest/`: `smoke.py`, `smoke6.py`, `smoke7.py`, `smoke_resume.py`, `smoke_resume2.py` + `pool/echo-skill/SKILL.md`); commit them under `tests/manual/` for posterity.

**Findings (drive Tasks 4-8 and the ADR):**
- **Discovery + isolation — works.** `cwd=<scratch>` + `setting_sources=["project"]` + `skills=["echo-skill"]` discovered and invoked the staged skill; a "list your skills" probe returned **only** `echo-skill` (no user-global / CLAUDE.md leak). The SDK's `_apply_skills_defaults` respects an explicit `setting_sources` (it only defaults to `["user","project"]` when `setting_sources is None`).
- **Writes under `dontAsk` + builtins in `allowed_tools` — DENIED** (the original design's mechanism). The agent reported both `Write` and `Bash` denied in "don't ask mode."
- **`acceptEdits`** — writes succeed but are **unscoped** (a deliberate out-of-scratch probe leaked). Rejected.
- **`can_use_tool`** — requires streaming-input mode *and* is bypassed for any tool in `allowed_tools`; in `default` mode it failed to fire and the write was denied with a stream error. Rejected.
- **`dontAsk` + a `PreToolUse` hook (Option D) — works and is scoped.** The hook returns `permissionDecision:"allow"` for in-scratch `Write`/`Edit`/`Read` and `"deny"` otherwise; `"allow"` overrides `dontAsk`, the out-of-scratch probe was denied, and the prompt stayed a plain `str`. This is the chosen mechanism.
- **Session resume is cwd-scoped (drives the per-thread scratch).** A fresh `mkdtemp` cwd on turn 2 **hard-crashes** resume: `ProcessError: No conversation found with session ID`. A **stable cwd path works** (`recall=True`, same session id) — and crucially, a stable path whose *contents are wiped* between turns **still resumes** (transcripts live in `~/.claude` keyed by the cwd path, not inside the dir). ⇒ skilled scratch must be a **deterministic per-thread path** (Task 6), and the skilled branch must be gated on a `scratch_key` so digests (no key) never take it.

**Validated config (encoded by `skill_loading_kwargs()` in Task 4):**

```python
ClaudeAgentOptions(
    model=cfg.model, system_prompt=..., mcp_servers=cfg.mcp_servers,
    allowed_tools=list(cfg.allowed_tools),   # readers ONLY; builtins NOT allow-listed
    permission_mode="dontAsk",
    cwd=scratch,
    setting_sources=["project"],
    skills=list(cfg.skills),                 # SDK appends Skill(<name>), auto-approved
    hooks={"PreToolUse": [HookMatcher(hooks=[make_scratch_guard(scratch)])]},
)  # prompt is a plain string
```

- [ ] **Step 1: Commit the reproducible smoke artifacts**

Copy the validated files from the scratchpad into the repo:

```bash
mkdir -p tests/manual/skill_pool/echo-skill
cp <scratchpad>/skilltest/pool/echo-skill/SKILL.md tests/manual/skill_pool/echo-skill/SKILL.md
cp <scratchpad>/skilltest/smoke7.py tests/manual/skill_loading_smoke.py    # Option D write/scope confirmation
cp <scratchpad>/skilltest/smoke_resume2.py tests/manual/skill_resume_smoke.py  # stable-path resume confirmation
git add tests/manual/
git commit -m "test: reproducible live smokes for headless skill loading + resume"
```

Expected (re-run any time with real auth):
- `python tests/manual/skill_loading_smoke.py` → `ARTIFACT WRITTEN: True`, `OUTSIDE LEAK: False`.
- `python tests/manual/skill_resume_smoke.py` → `recall=True same_sid=True` (stable path, wiped between turns, still resumes).

---

### Task 2: Skills pool scaffold — vetting README + one seed skill

**Files:**
- Create: `config/skills/README.md`
- Create: `config/skills/architecture-diagram/SKILL.md`
- Test: `tests/test_config.py` (one structural assertion, added in Task 3)

**Interfaces:**
- Produces: the directory `config/skills/` with `architecture-diagram/SKILL.md`, the canonical pool that `config._parse_skills` validates against and `agent_runner._stage_skills` copies from.

- [ ] **Step 1: Write the pool README (vetting criteria)**

`config/skills/README.md`:

```markdown
# Babbla skills pool

Vetted, **read-only** skills a project may opt into via `skills:` in
`config/channels.yaml`. This pool is Babbla-controlled and version-controlled;
the subject repo can never contribute a skill.

A skill is admissible only if **all** hold:

1. It needs no GitHub or agentmemory **writer** — it reads via the existing
   read-only MCP surface only.
2. It never mutates the subject repo (Babbla holds no local clone; the repo is
   reachable only over the read-only GitHub MCP).
3. Any file it writes goes to the **current working directory** (the per-request
   scratch dir). It must not write outside cwd.

Each skill is a folder `<name>/SKILL.md` (+ optional bundled resources), in the
standard Claude Code skill format. The folder name is the value used in
`skills:`.
```

- [ ] **Step 2: Write the seed skill**

`config/skills/architecture-diagram/SKILL.md`:

```markdown
---
name: architecture-diagram
description: Draw a project's architecture as a self-contained HTML+SVG file from its repository, using only read-only GitHub access. Use when asked to diagram, draw, or visualize a service's components and data flow.
---

# Architecture diagram (read-only)

Produce a polished dark-themed architecture diagram as a single self-contained
HTML file written into the current working directory.

## Steps

1. Explore the repository over the GitHub tools only (README, files under
   `src/`, `docs/`, ADRs). Do not attempt any write, and do not assume a local
   checkout exists.
2. Identify the major components and the data/control flow between them.
3. Write ONE file `architecture.html` into the current working directory: an
   inline `<style>` + inline `<svg>` (no external assets, no network) showing
   the components as boxes and the flows as labelled arrows.
4. Reply with a 2-3 sentence summary of the architecture. Do not paste the HTML
   into the reply.

Keep the file self-contained: all CSS inline, all geometry inline SVG.
```

- [ ] **Step 3: Verify the pool is well-formed**

Run: `test -f config/skills/architecture-diagram/SKILL.md && head -1 config/skills/README.md`
Expected: prints `# Babbla skills pool` (file exists, README readable).

- [ ] **Step 4: Commit**

```bash
git add config/skills/README.md config/skills/architecture-diagram/SKILL.md
git commit -m "feat: seed config/skills pool with vetting README + architecture-diagram"
```

---

### Task 3: Config — `ProjectBinding.skills` parse + validate

**Files:**
- Modify: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ProjectBinding.skills: tuple[str, ...]` (default `()`); `load_config` validates each name against `<config-dir>/skills/<name>/SKILL.md` and raises `ValueError` on an unknown name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def _make_pool(tmp_path: Path, *names: str) -> None:
    for n in names:
        d = tmp_path / "skills" / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n" % n)


def test_skills_parse_to_tuple(tmp_path):
    _make_pool(tmp_path, "architecture-diagram")
    text = FIXTURE + "    skills:\n      - architecture-diagram\n"
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].skills == ("architecture-diagram",)


def test_skills_absent_is_empty_tuple(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].skills == ()


def test_unknown_skill_raises(tmp_path):
    text = FIXTURE + "    skills:\n      - nope\n"
    with pytest.raises(ValueError, match="unknown skill 'nope'"):
        load_config(_write(tmp_path, text))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k skill -v`
Expected: FAIL — `ProjectBinding.__init__() got an unexpected keyword argument 'skills'` (and the unknown-skill test errors, not raises `ValueError`).

- [ ] **Step 3: Add the field and parser**

In `src/babbla/config.py`, add `from pathlib import Path` near the top imports. Add `skills` to the dataclass:

```python
@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool
    digest: DigestConfig | None = None
    quiz: QuizConfig | None = None
    stale_prs: "StalePRConfig | None" = None
    adr: "AdrConfig | None" = None
    skills: tuple[str, ...] = ()
```

Add the parser (next to the other `_parse_*` helpers):

```python
def _parse_skills(name: str, raw: object, pool: Path) -> tuple[str, ...]:
    skills = _parse_str_list(name, "skills", raw)
    for s in skills:
        if not (pool / s / "SKILL.md").is_file():
            raise ValueError(f"{name}: unknown skill {s!r} (no {pool}/{s}/SKILL.md)")
    return skills
```

In `load_config`, derive the pool from the config file's directory and pass `skills` into each binding:

```python
def load_config(path: str | os.PathLike) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    pool = Path(path).parent / "skills"
    bindings = tuple(
        ProjectBinding(
            name=p["name"],
            owner=p["owner"],
            repo=p["repo"],
            visibility=p["visibility"],
            channel_id=p.get("channel_id"),
            dm=bool(p.get("dm", False)),
            digest=_parse_digest(p["name"], p.get("digest")),
            quiz=_parse_quiz(p["name"], p.get("quiz")),
            stale_prs=_parse_stale_prs(p["name"], p.get("stale_prs")),
            adr=_parse_adr(p["name"], p.get("adr")),
            skills=_parse_skills(p["name"], p.get("skills"), pool),
        )
        for p in raw.get("projects", [])
    )
    # ... (rest of load_config unchanged: dm warnings, lobby_channel_id, personal_digest, return)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (new skill tests + all existing config tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/config.py tests/test_config.py
git commit -m "feat: ProjectBinding.skills parsed + validated against the pool"
```

---

### Task 4: read_only — scratch-guard hook + skill-loading kwargs + AgentConfig.skills

Per Task 1's validated Option D, builtins are **not** allow-listed; a `PreToolUse`
hook scopes file writes to scratch. So `allowed_tools` is unchanged on the skilled
path — the new surface is the hook factory + the options kwargs.

**Files:**
- Modify: `src/babbla/read_only.py`
- Test: `tests/test_read_only_guard.py`

**Interfaces:**
- Produces:
  - `_within(path: str, root: str) -> bool` — `path` inside `root`; relative paths resolve against `root` (the agent cwd), not the host process cwd.
  - `make_scratch_guard(scratch: str) -> HookCallback` — async `PreToolUse` hook: `allow` in-scratch `Write`/`Edit`/`Read`, `deny` out-of-scratch and `Bash`, `{}` (no opinion) for anything else.
  - `skill_loading_kwargs(*, scratch_dir: str, skills: tuple[str, ...]) -> dict` → `{cwd, setting_sources:["project"], skills:list(skills), hooks:{PreToolUse:[HookMatcher(hooks=[make_scratch_guard(scratch_dir)])]}}`.
  - `AgentConfig.skills: tuple[str, ...] = ()`.
  - `build_agent_config(..., skills: tuple[str, ...] = ())` — carries `skills` onto `AgentConfig`; **`allowed_tools` is unchanged** (readers only).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_read_only_guard.py`:

```python
from babbla.read_only import _within, make_scratch_guard, skill_loading_kwargs


def _decision(out):
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def test_within_absolute_and_relative(tmp_path):
    root = str(tmp_path)
    assert _within(str(tmp_path / "architecture.html"), root)
    assert _within("architecture.html", root)        # relative -> resolved against root
    assert _within("sub/x.md", root)
    assert not _within("/etc/passwd", root)
    assert not _within("../escape.txt", root)
    assert not _within("", root)


async def test_guard_allows_in_scratch_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "a.html")}}, None, {}
    )
    assert _decision(out) == "allow"


async def test_guard_allows_relative_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": "a.html"}}, None, {}
    )
    assert _decision(out) == "allow"


async def test_guard_denies_out_of_scratch_write(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/evil.txt"}}, None, {}
    )
    assert _decision(out) == "deny"


async def test_guard_denies_bash(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}, None, {})
    assert _decision(out) == "deny"


async def test_guard_ignores_mcp_tools(tmp_path):
    guard = make_scratch_guard(str(tmp_path))
    out = await guard({"tool_name": "mcp__github__search_code", "tool_input": {}}, None, {})
    assert out == {}  # no opinion -> governed by allowed_tools + dontAsk


def test_skilled_build_keeps_readers_only_allowed_tools():
    cfg = _cfg(skills=("architecture-diagram",))
    assert cfg.skills == ("architecture-diagram",)
    assert cfg.allowed_tools == ALLOWED_TOOLS         # builtins NOT allow-listed
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools
    for writer in AGENTMEMORY_WRITERS:
        assert writer not in cfg.allowed_tools


def test_skill_loading_kwargs_shape(tmp_path):
    kw = skill_loading_kwargs(scratch_dir=str(tmp_path), skills=("a", "b"))
    assert kw["cwd"] == str(tmp_path)
    assert kw["setting_sources"] == ["project"]
    assert kw["skills"] == ["a", "b"]
    assert "PreToolUse" in kw["hooks"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_read_only_guard.py -k "guard or within or skill" -v`
Expected: FAIL — `ImportError: cannot import name '_within'`.

- [ ] **Step 3: Implement the hook factory + kwargs**

In `src/babbla/read_only.py`, add the `HookMatcher` import and `Path`:

```python
from pathlib import Path

from claude_agent_sdk import HookMatcher
```

After `ALLOWED_TOOLS`, add:

```python
def _within(path: str, root: str) -> bool:
    """True iff `path` is inside `root`. Relative paths resolve against `root`
    (the agent's cwd = the scratch dir), not the host process cwd."""
    if not path:
        return False
    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        p.resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _pre_tool(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}


def make_scratch_guard(scratch: str):
    """A PreToolUse hook confining a skill's file writes to `scratch`.

    Validated by Task 1 (Option D): under permission_mode="dontAsk", returning
    permissionDecision="allow" lets an in-scratch Write/Edit/Read through, while
    "deny" blocks out-of-scratch writes and Bash. Returning {} (no opinion)
    leaves MCP tools governed by allowed_tools + dontAsk exactly as today, so
    MCP writers stay denied.
    """
    async def guard(input, tool_use_id, context):
        tool = input.get("tool_name", "")
        if tool in ("Write", "Edit", "Read"):
            ti = input.get("tool_input", {})
            path = ti.get("file_path") or ti.get("path") or ""
            ok = _within(path, scratch)
            return _pre_tool("allow" if ok else "deny",
                             "scratch-scoped" if ok else "outside scratch workspace")
        if tool == "Bash":
            return _pre_tool("deny", "bash is not permitted on the skilled path")
        return {}
    return guard


def skill_loading_kwargs(*, scratch_dir: str, skills: tuple[str, ...]) -> dict:
    """`ClaudeAgentOptions` kwargs that load ONLY `skills` from a clean scratch
    workspace headlessly, confine writes to scratch, and leak no Babbla-repo /
    user-global context. Validated by Task 1.

    - cwd=<scratch> — discovery + writes rooted at the clean temp dir.
    - setting_sources=["project"] — discover <scratch>/.claude/skills only.
    - skills=[names] — enable ONLY these (SDK appends Skill(<name>)).
    - hooks — PreToolUse scratch guard (see make_scratch_guard).

    Caller must stage the skills into <scratch>/.claude/skills/<name>.
    """
    return {
        "cwd": scratch_dir,
        "setting_sources": ["project"],
        "skills": list(skills),
        "hooks": {"PreToolUse": [HookMatcher(hooks=[make_scratch_guard(scratch_dir)])]},
    }
```

Add `skills` to `AgentConfig` and carry it through `build_agent_config` (**allowed_tools unchanged**):

```python
@dataclass(frozen=True)
class AgentConfig:
    model: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    permission_mode: str
    mcp_servers: dict
    skills: tuple[str, ...] = ()
```

```python
def build_agent_config(
    *,
    owner: str,
    repo: str,
    github_token: str,
    agentmemory_url: str,
    agentmemory_secret: str,
    model: str = DEFAULT_MODEL,
    github_launcher: str = "docker",
    skills: tuple[str, ...] = (),
) -> AgentConfig:
    mcp_servers = {"github": _github_server(github_token, github_launcher)}
    allowed_tools: tuple[str, ...] = (GITHUB_WILDCARD,)
    if agentmemory_url:  # agentmemory is OPTIONAL local enrichment (ADR 0009)
        mcp_servers["agentmemory"] = _agentmemory_server(agentmemory_url, agentmemory_secret)
        allowed_tools = ALLOWED_TOOLS
    return AgentConfig(
        model=model,
        system_prompt=build_system_prompt(owner, repo),
        allowed_tools=allowed_tools,      # builtins are hook-gated, NOT allow-listed
        permission_mode="dontAsk",
        mcp_servers=mcp_servers,
        skills=skills,
    )
```

The `_cfg` helper in `tests/test_read_only_guard.py` already forwards `skills` via `**over`; no change needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_read_only_guard.py -v`
Expected: PASS — new hook/kwargs tests pass AND all existing guard tests still pass (the unskilled `_cfg()` is unchanged, and a skilled `_cfg()` still has readers-only `allowed_tools`).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/read_only.py tests/test_read_only_guard.py
git commit -m "feat: scratch-guard PreToolUse hook + skill-loading kwargs (guard-tested)"
```

---

### Task 5: agent_runner — Artifact, CitedAnswer.artifacts, capture helper

**Files:**
- Modify: `src/babbla/agent_runner.py`
- Test: `tests/test_artifacts.py` (new)

**Interfaces:**
- Produces:
  - `Artifact` (frozen): `filename: str`, `data: bytes`
  - `CitedAnswer.artifacts: tuple[Artifact, ...] = ()`
  - `_collect_artifacts(scratch: str) -> tuple[Artifact, ...]` — every regular file under `scratch` whose path has no hidden (`.`-prefixed) part, read as bytes, keyed by basename, sorted by path.
  - `_stage_skills(pool: str, names: tuple[str, ...], scratch: str) -> None` — copies `<pool>/<name>` → `<scratch>/.claude/skills/<name>`.
  - `Secrets.skills_pool: str = "config/skills"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_artifacts.py`:

```python
from pathlib import Path

from babbla.agent_runner import Artifact, CitedAnswer, _collect_artifacts, _stage_skills


def test_cited_answer_artifacts_default_empty():
    assert CitedAnswer(text="x", session_id=None).artifacts == ()


def test_collect_artifacts_reads_files_and_skips_hidden(tmp_path):
    (tmp_path / "architecture.html").write_text("<svg/>")
    (tmp_path / "notes.md").write_bytes(b"hi")
    hidden = tmp_path / ".claude" / "skills" / "x"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text("staged skill, not an artifact")
    arts = _collect_artifacts(str(tmp_path))
    names = {a.filename for a in arts}
    assert names == {"architecture.html", "notes.md"}
    assert Artifact("notes.md", b"hi") in arts


def test_stage_skills_copies_into_dot_claude(tmp_path):
    pool = tmp_path / "pool" / "echo-skill"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_text("---\nname: echo-skill\ndescription: x\n---\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _stage_skills(str(tmp_path / "pool"), ("echo-skill",), str(scratch))
    assert (scratch / ".claude" / "skills" / "echo-skill" / "SKILL.md").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_artifacts.py -v`
Expected: FAIL — `ImportError: cannot import name 'Artifact'`.

- [ ] **Step 3: Implement the types and helpers**

In `src/babbla/agent_runner.py`, extend the imports and add the types/helpers:

```python
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from babbla.config import ProjectBinding
from babbla.read_only import (
    DEFAULT_MODEL,
    build_agent_config,
    skill_loading_kwargs,
)


@dataclass(frozen=True)
class Artifact:
    filename: str
    data: bytes


@dataclass(frozen=True)
class CitedAnswer:
    text: str
    session_id: str | None
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class Secrets:
    github_token: str
    agentmemory_url: str
    agentmemory_secret: str
    model: str = DEFAULT_MODEL
    github_launcher: str = "docker"
    skills_pool: str = "config/skills"


def _stage_skills(pool: str, names: tuple[str, ...], scratch: str) -> None:
    dest_root = Path(scratch) / ".claude" / "skills"
    dest_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(Path(pool) / name, dest_root / name)


def _collect_artifacts(scratch: str) -> tuple[Artifact, ...]:
    root = Path(scratch)
    out: list[Artifact] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue  # skips <scratch>/.claude/skills/* (staged skills) and dotfiles
        out.append(Artifact(filename=p.name, data=p.read_bytes()))
    return tuple(out)
```

Keep `_extract_text` as-is. (The `run_ask` branch is Task 6.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_artifacts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/agent_runner.py tests/test_artifacts.py
git commit -m "feat: Artifact type + scratch staging/capture helpers"
```

---

### Task 6: agent_runner — skilled `run_ask` branch + scratch lifecycle

**Files:**
- Modify: `src/babbla/agent_runner.py`
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `Secrets.skills_pool`, `_stage_skills`, `_collect_artifacts`, `skill_loading_kwargs`, `build_agent_config(skills=...)`, `AgentConfig.skills`.
- Produces:
  - `AgentRunner.run_ask(..., scratch_key: str | None = None)` — the only signature change is the new keyword. Takes the skilled branch only when `binding.skills` is non-empty **and** `scratch_key` is provided (the Ask paths pass `thread_ts`; digest/quiz/adr pass nothing → never skilled). Unskilled/no-key calls get byte-for-byte today's options.
  - `_scratch_path(scratch_key) -> str` — a deterministic per-thread scratch path (so resume works); wiped + recreated each ask, always removed in `finally`.
  - Artifacts captured (in memory) before the wipe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_runner.py`:

```python
from pathlib import Path

from babbla.agent_runner import Artifact


def _pool_with(tmp_path, name="architecture-diagram"):
    d = tmp_path / "pool" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: %s\ndescription: x\n---\n" % name)
    return str(tmp_path / "pool")


SKILLED_BINDING = ProjectBinding(
    "MyTV", "Wkkkkk", "MyTV", "public", "C123", True, skills=("architecture-diagram",)
)


async def test_unskilled_options_have_no_scratch_or_skills():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    opts = captured["options"]
    assert getattr(opts, "cwd", None) is None
    assert not getattr(opts, "skills", None)
    assert getattr(opts, "setting_sources", None) in (None, [])
    assert "Write" not in opts.allowed_tools


async def test_skilled_options_carry_scratch_skills_and_hook(tmp_path):
    captured = {}
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    opts = captured["options"]
    assert opts.skills == ["architecture-diagram"]
    assert opts.setting_sources == ["project"]
    assert opts.cwd                                    # the per-thread scratch dir
    assert "PreToolUse" in (opts.hooks or {})          # scratch guard wired
    # Builtins are hook-gated, NOT allow-listed (read-only posture preserved).
    assert "Write" not in opts.allowed_tools and "Bash" not in opts.allowed_tools


async def test_skilled_cwd_is_stable_per_scratch_key(tmp_path):
    # Resume needs the same cwd across a thread's turns. Same key -> same path;
    # different key -> different path.
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))
    a, b, c = {}, {}, {}
    await AgentRunner(secrets, query_fn=make_query_fn(a)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-A")
    await AgentRunner(secrets, query_fn=make_query_fn(b)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-A")
    await AgentRunner(secrets, query_fn=make_query_fn(c)).run_ask(
        "q", SKILLED_BINDING, None, scratch_key="thread-B")
    assert a["options"].cwd == b["options"].cwd        # stable across turns
    assert a["options"].cwd != c["options"].cwd        # distinct per thread


async def test_skilled_branch_skipped_without_scratch_key(tmp_path):
    # A skilled binding with NO scratch_key (e.g. the digest path) must take the
    # plain branch — never load skills or a scratch.
    captured = {}
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("digest", SKILLED_BINDING, resume_session_id=None)  # no scratch_key
    opts = captured["options"]
    assert getattr(opts, "cwd", None) is None
    assert not getattr(opts, "skills", None)


async def test_skilled_scratch_is_removed_after_run(tmp_path):
    captured = {}
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))
    runner = AgentRunner(secrets, query_fn=make_query_fn(captured))
    await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert not Path(captured["options"].cwd).exists()  # wiped in finally


async def test_skilled_scratch_removed_even_on_exception(tmp_path):
    from babbla.agent_runner import _scratch_path
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))

    async def boom(prompt, options=None):
        raise RuntimeError("agent died")
        yield  # pragma: no cover

    runner = AgentRunner(secrets, query_fn=boom)
    with pytest.raises(RuntimeError):
        await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert not Path(_scratch_path("t1")).exists()


async def test_skilled_captures_artifacts(tmp_path):
    secrets = Secrets(github_token="g", agentmemory_url="http://x", agentmemory_secret="",
                      skills_pool=_pool_with(tmp_path))

    async def writing_query(prompt, options=None):
        # Simulate a skill writing an artifact into the scratch cwd.
        (Path(options.cwd) / "architecture.html").write_text("<svg/>")
        yield FakeResultMessage(result="drew it", session_id="s1")

    runner = AgentRunner(secrets, query_fn=writing_query)
    ans = await runner.run_ask("draw it", SKILLED_BINDING, resume_session_id=None, scratch_key="t1")
    assert ans.text == "drew it"
    assert Artifact("architecture.html", b"<svg/>") in ans.artifacts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_runner.py -k skilled -v`
Expected: FAIL — `run_ask()` has no `scratch_key` parameter yet; skilled bindings currently run the plain path.

- [ ] **Step 3: Refactor run_ask into plain/skilled branches**

Replace `AgentRunner.run_ask` in `src/babbla/agent_runner.py` with a dispatcher plus shared drain:

```python
class AgentRunner:
    def __init__(self, secrets: Secrets, query_fn=_sdk_query) -> None:
        self._secrets = secrets
        self._query = query_fn

    async def run_ask(
        self, text: str, binding: ProjectBinding, resume_session_id: str | None,
        *, system_prompt: str | None = None, scratch_key: str | None = None,
    ) -> CitedAnswer:
        cfg = build_agent_config(
            owner=binding.owner,
            repo=binding.repo,
            github_token=self._secrets.github_token,
            agentmemory_url=self._secrets.agentmemory_url,
            agentmemory_secret=self._secrets.agentmemory_secret,
            model=self._secrets.model,
            github_launcher=self._secrets.github_launcher,
            skills=binding.skills,
        )
        # The skilled branch needs a STABLE per-thread scratch path so session
        # resume works (the CLI scopes sessions by cwd — a fresh random cwd each
        # turn crashes resume with "No conversation found"). It fires only when a
        # scratch_key is supplied, which the interactive Ask paths pass as the
        # thread_ts; digest/quiz/adr callers pass none, so they NEVER go skilled
        # (digest-path skills stay out of scope).
        if cfg.skills and scratch_key is not None:
            return await self._run_skilled(
                cfg, text, binding, resume_session_id, system_prompt, scratch_key
            )
        return await self._run_plain(cfg, text, binding, resume_session_id, system_prompt)

    def _base_options(self, cfg, system_prompt, resume_session_id, **extra) -> ClaudeAgentOptions:
        options = ClaudeAgentOptions(
            model=cfg.model,
            system_prompt=system_prompt or cfg.system_prompt,
            allowed_tools=list(cfg.allowed_tools),
            permission_mode=cfg.permission_mode,
            mcp_servers=cfg.mcp_servers,
            **extra,
        )
        if resume_session_id:
            options.resume = resume_session_id
        return options

    async def _drain(self, options, text, resume_session_id):
        last_text: str | None = None
        session_id: str | None = resume_session_id
        async for message in self._query(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                last_text = captured
            sid = getattr(message, "session_id", None)
            if sid:
                session_id = sid
        return last_text, session_id

    def _fallback(self, binding) -> str:
        return f"I don't know — I couldn't find anything in {binding.name}'s history."

    async def _run_plain(self, cfg, text, binding, resume_session_id, system_prompt) -> CitedAnswer:
        options = self._base_options(cfg, system_prompt, resume_session_id)
        last_text, session_id = await self._drain(options, text, resume_session_id)
        return CitedAnswer(text=last_text or self._fallback(binding), session_id=session_id)

    async def _run_skilled(
        self, cfg, text, binding, resume_session_id, system_prompt, scratch_key
    ) -> CitedAnswer:
        # Deterministic per-thread path (NOT mkdtemp): turn N+1 must reuse the
        # same cwd as turn N or resume crashes. Wipe + recreate so the dir starts
        # empty each turn (simple artifact capture); resume still works because
        # the session transcript lives in ~/.claude keyed by the cwd *path*, not
        # inside the dir (validated by smoke_resume2). The orchestrator serializes
        # asks per thread, so the shared path is never used concurrently.
        scratch = _scratch_path(scratch_key)
        shutil.rmtree(scratch, ignore_errors=True)   # clear any prior-turn / crashed-run leftovers
        os.makedirs(scratch, exist_ok=True)
        try:
            _stage_skills(self._secrets.skills_pool, cfg.skills, scratch)
            options = self._base_options(
                cfg, system_prompt, resume_session_id,
                **skill_loading_kwargs(scratch_dir=scratch, skills=cfg.skills),
            )
            last_text, session_id = await self._drain(options, text, resume_session_id)
            artifacts = _collect_artifacts(scratch)
            return CitedAnswer(
                text=last_text or self._fallback(binding),
                session_id=session_id,
                artifacts=artifacts,
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
```

And add the deterministic-path helper near the other module helpers (Task 5's
`_stage_skills`/`_collect_artifacts`):

```python
def _scratch_path(scratch_key: str) -> str:
    """A STABLE scratch dir path for a conversation thread. Keyed by scratch_key
    (the thread_ts) so a thread's turns share one cwd — required for session
    resume, which the CLI scopes by cwd path. Lives under $TMPDIR (honor a
    writable tmpfs in containers)."""
    digest = hashlib.sha1(scratch_key.encode()).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"babbla-skill-{digest}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_runner.py -v`
Expected: PASS — skilled tests pass AND the five pre-existing `run_ask` tests still pass (plain path unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/agent_runner.py tests/test_agent_runner.py
git commit -m "feat: skilled run_ask branch with stable per-thread scratch + artifact capture"
```

---

### Task 7: SlackPoster.upload_file + adapter upload wiring

**Files:**
- Modify: `src/babbla/digest/poster.py`, `src/babbla/slack_adapter.py`
- Test: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: `CitedAnswer.artifacts`, `Artifact`.
- Produces:
  - `SlackPoster.upload_file(self, channel_id, *, filename, content, title=None, thread_ts=None) -> bool` — forwards to `files_upload_v2`; returns `False` and logs on failure (never raises).
  - `slack_adapter._upload_artifacts(client, *, channel, thread_ts, artifacts) -> None` — uploads each artifact threaded under the answer; called after `chat_update` in both `process_ask` and `process_lobby_ask`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_artifacts.py`:

```python
import pytest

from babbla.agent_runner import Artifact, CitedAnswer
from babbla.digest.poster import SlackPoster


class FakeUploadClient:
    def __init__(self, fail=False):
        self.uploads = []
        self._fail = fail

    async def files_upload_v2(self, **kwargs):
        if self._fail:
            raise RuntimeError("missing files:write scope")
        self.uploads.append(kwargs)
        return {"ok": True}


async def test_upload_file_forwards_fields():
    client = FakeUploadClient()
    ok = await SlackPoster(client).upload_file(
        "C1", filename="architecture.html", content=b"<svg/>", thread_ts="t1"
    )
    assert ok is True
    up = client.uploads[0]
    assert up["channel"] == "C1"
    assert up["filename"] == "architecture.html"
    assert up["content"] == b"<svg/>"
    assert up["thread_ts"] == "t1"
    assert up["title"] == "architecture.html"  # defaults to filename


async def test_upload_file_degrades_on_failure():
    client = FakeUploadClient(fail=True)
    ok = await SlackPoster(client).upload_file("C1", filename="x.md", content=b"y")
    assert ok is False  # logged, not raised


async def test_adapter_uploads_artifacts_threaded():
    from babbla import slack_adapter

    class FakeOrch:
        async def handle_ask(self, **kwargs):
            return CitedAnswer(text="drew it", session_id="s",
                               artifacts=(Artifact("architecture.html", b"<svg/>"),))

    class FakeClient:
        def __init__(self):
            self.uploads = []
            self.updated = []
        async def chat_postMessage(self, **kwargs):
            return {"ts": "ph1"}
        async def chat_update(self, **kwargs):
            self.updated.append(kwargs)
        async def files_upload_v2(self, **kwargs):
            self.uploads.append(kwargs)
            return {"ok": True}

    client = FakeClient()
    await slack_adapter.process_ask(
        text="draw", channel="C1", thread_ts="t1", is_dm=False,
        client=client, orchestrator=FakeOrch(), user_id="U1",
    )
    assert client.uploads and client.uploads[0]["filename"] == "architecture.html"
    assert client.uploads[0]["thread_ts"] == "t1"


async def test_adapter_artifact_upload_failure_does_not_crash():
    from babbla import slack_adapter

    class FakeOrch:
        async def handle_ask(self, **kwargs):
            return CitedAnswer(text="ok", session_id="s",
                               artifacts=(Artifact("x.md", b"y"),))

    class FlakyClient:
        async def chat_postMessage(self, **kwargs):
            return {"ts": "ph1"}
        async def chat_update(self, **kwargs):
            pass
        async def files_upload_v2(self, **kwargs):
            raise RuntimeError("no scope")

    # Must not raise.
    await slack_adapter.process_ask(
        text="q", channel="C1", thread_ts="t1", is_dm=False,
        client=FlakyClient(), orchestrator=FakeOrch(), user_id="U1",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_artifacts.py -k "upload or adapter" -v`
Expected: FAIL — `AttributeError: 'SlackPoster' object has no attribute 'upload_file'`.

- [ ] **Step 3: Implement upload_file**

Replace `src/babbla/digest/poster.py` with:

```python
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SlackPoster:
    def __init__(self, client) -> None:
        self._client = client

    async def post(
        self, channel_id: str, text: str, thread_ts: str | None = None, blocks=None
    ) -> str:
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        if blocks is not None:
            kwargs["blocks"] = blocks
        resp = await self._client.chat_postMessage(**kwargs)
        return resp["ts"]

    async def open_dm(self, user_id: str) -> str:
        resp = await self._client.conversations_open(users=user_id)
        return resp["channel"]["id"]

    async def upload_file(
        self, channel_id: str, *, filename: str, content,
        title: str | None = None, thread_ts: str | None = None,
    ) -> bool:
        """Upload one artifact. Returns False (logged) on failure — a missing
        files:write scope or upload error must never crash the ask."""
        kwargs = {
            "channel": channel_id,
            "filename": filename,
            "content": content,
            "title": title or filename,
        }
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        try:
            await self._client.files_upload_v2(**kwargs)
            return True
        except Exception:
            logger.exception("artifact upload failed: %s -> %s", filename, channel_id)
            return False
```

- [ ] **Step 4: Wire the adapter**

In `src/babbla/slack_adapter.py`, add the import and helper, and call it after each `chat_update`:

```python
from babbla.digest.poster import SlackPoster
```

```python
async def _upload_artifacts(client, *, channel: str, thread_ts: str, artifacts) -> None:
    if not artifacts:
        return
    poster = SlackPoster(client)
    for art in artifacts:
        await poster.upload_file(
            channel, filename=art.filename, content=art.data, thread_ts=thread_ts
        )
```

In `process_ask`, after the success-path `chat_update(...)`:

```python
        await client.chat_update(
            channel=channel, ts=ts, text=answer.text,
            blocks=delete_button_blocks(answer.text, owner_id=user_id or ""),
        )
        await _upload_artifacts(
            client, channel=channel, thread_ts=thread_ts,
            artifacts=getattr(answer, "artifacts", ()),
        )
```

Make the identical addition in `process_lobby_ask` after its `chat_update(...)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_artifacts.py -v`
Expected: PASS (upload_file + adapter tests).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/digest/poster.py src/babbla/slack_adapter.py tests/test_artifacts.py
git commit -m "feat: upload skill artifacts to Slack, threaded under the answer (degrades safely)"
```

---

### Task 8: Orchestrator — pass `scratch_key=thread_ts` + preserve artifacts through the lobby path

Two orchestrator changes: (1) every Ask path passes `scratch_key=thread_ts` to
`run_ask` (this is what enables the skilled branch and gives it the stable
per-thread scratch resume needs); (2) `handle_lobby_ask` stops dropping artifacts
when it appends the pointer suffix.

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `AgentRunner.run_ask(..., scratch_key=...)`, `CitedAnswer.artifacts`.
- Produces: `handle_ask`, `_handle_personal_ask`, `handle_lobby_ask` all call
  `run_ask(..., scratch_key=thread_ts)`; `handle_lobby_ask` preserves
  `answer.artifacts`.

> **Update the existing fake runners first.** `tests/test_orchestrator.py` has
> several fakes (`FakeRunner`, `SlowRunner`, plus the new `ArtifactRunner`) whose
> `run_ask` signature is `(self, text, binding, resume_session_id)`. Since the
> orchestrator now passes `scratch_key=...` as a keyword, add `*, scratch_key=None`
> to each fake's `run_ask` (they can ignore it). Without this, every orchestrator
> test fails with an unexpected-keyword error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
from babbla.agent_runner import Artifact
from babbla.lobby import CatalogEntry          # CatalogEntry(binding, description)
from babbla.session_store import LobbyThreadStore


class ArtifactRunner:
    def __init__(self):
        self.scratch_keys = []
    async def run_ask(self, text, binding, resume_session_id, *, scratch_key=None):
        self.scratch_keys.append(scratch_key)
        return CitedAnswer(text="drew it", session_id="s1",
                           artifacts=(Artifact("architecture.html", b"<svg/>"),))


async def test_handle_ask_passes_thread_ts_as_scratch_key(store):
    runner = ArtifactRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="draw", thread_ts="t1", channel_id="C123", is_dm=False)
    assert ans.artifacts and ans.artifacts[0].filename == "architecture.html"
    assert runner.scratch_keys == ["t1"]        # thread_ts threaded through as scratch_key


async def test_lobby_ask_preserves_artifacts_and_scratch_key(store, tmp_path):
    runner = ArtifactRunner()
    entry = CatalogEntry(BINDING, None)         # (binding, description) — matches build_catalog
    lobby_store = LobbyThreadStore(str(tmp_path / "lobby.db"))
    await lobby_store.put("t1", BINDING.name)   # sticky → deterministic route, no classifier call
    orch = Orchestrator(CONFIG, runner, store, catalog=(entry,), lobby_store=lobby_store)
    ans = await orch.handle_lobby_ask(text="draw", thread_ts="t1")
    assert ans.artifacts and ans.artifacts[0].filename == "architecture.html"
    assert runner.scratch_keys == ["t1"]
    lobby_store.close()
```

(`CatalogEntry` and `LobbyThreadStore` are already imported near the existing
lobby tests in `tests/test_orchestrator.py`; the re-imports above are harmless if
you place this block separately. The `(binding, description)` shape and the
`put`/`close` store API are confirmed against `src/babbla/lobby.py` and
`src/babbla/session_store.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -k "scratch_key or artifact" -v`
Expected: FAIL — the orchestrator doesn't pass `scratch_key` yet (so `scratch_keys == [None]`), and the lobby path drops artifacts.

- [ ] **Step 3: Pass `scratch_key` and preserve lobby artifacts**

In `src/babbla/orchestrator.py`, add `scratch_key=thread_ts` to each `run_ask`
call. There are three:

`handle_ask`:

```python
                answer = await self._runner.run_ask(
                    text, binding, resume_session_id, scratch_key=thread_ts
                )
```

`_handle_personal_ask`:

```python
                answer = await self._runner.run_ask(
                    text, entry.binding, resume, scratch_key=thread_ts
                )
```

`handle_lobby_ask` — pass `scratch_key` **and** preserve artifacts in the return:

```python
                answer = await self._runner.run_ask(
                    text, entry.binding, resume, scratch_key=thread_ts
                )
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return CitedAnswer(
                    text=answer.text + lobby.pointer_suffix(entry),
                    session_id=answer.session_id,
                    artifacts=answer.artifacts,
                )
```

(A non-skilled binding ignores `scratch_key` — `run_ask` only uses it when
`cfg.skills` is non-empty — so this is a no-op for today's projects.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (new tests + all existing orchestrator tests, with the fakes updated to accept `scratch_key`).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: thread thread_ts as scratch_key into run_ask; preserve lobby artifacts"
```

---

### Task 9: Wiring + docs — Secrets.skills_pool, channels.yaml, DEPLOY, ADR 0015

**Files:**
- Modify: `src/babbla/app.py`, `config/channels.yaml`, `DEPLOY.md`
- Create: `docs/adr/0015-skilled-answer-path.md`
- Test: `tests/test_app.py` (extend)

**Interfaces:**
- Consumes: `Secrets.skills_pool`.
- Produces: `load_secrets` reads `BABBLA_SKILLS_POOL` (default `config/skills`) into `Secrets.skills_pool`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_load_secrets_default_skills_pool():
    from babbla.app import load_secrets
    env = {"SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "z"}
    assert load_secrets(env).skills_pool == "config/skills"


def test_load_secrets_skills_pool_override():
    from babbla.app import load_secrets
    env = {"SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "z",
           "BABBLA_SKILLS_POOL": "/srv/pool"}
    assert load_secrets(env).skills_pool == "/srv/pool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -k skills_pool -v`
Expected: FAIL — `Secrets(...)` has no `skills_pool` from `load_secrets` (returns default `"config/skills"` only if wired; the override test fails).

- [ ] **Step 3: Wire load_secrets**

In `src/babbla/app.py`, add to the `Secrets(...)` construction in `load_secrets`:

```python
    return Secrets(
        github_token=env["GITHUB_TOKEN"],
        agentmemory_url=env.get("AGENTMEMORY_URL", "http://localhost:3111"),
        agentmemory_secret=env.get("AGENTMEMORY_SECRET", ""),
        model=env.get("BABBLA_MODEL", DEFAULT_MODEL),
        github_launcher=env.get("BABBLA_GITHUB_MCP", "docker"),
        skills_pool=env.get("BABBLA_SKILLS_POOL", "config/skills"),
    )
```

- [ ] **Step 4: Document the `skills:` field in channels.yaml**

Add a commented example under a project in `config/channels.yaml` (kept as a NULL/template binding — do not bind real IDs):

```yaml
    # Opt into vetted read-only skills from config/skills/ (default: none).
    # Each name must be a folder config/skills/<name>/SKILL.md. When set, the
    # answering agent may invoke these skills and Babbla uploads any artifact
    # they produce back to the asking surface. Requires the bot's files:write
    # scope (see DEPLOY.md).
    # skills:
    #   - architecture-diagram
```

- [ ] **Step 5: Document the Slack scope + Docker deployment in DEPLOY.md**

Add a note to `DEPLOY.md` (Slack scopes section): the bot token needs **`files:write`** for projects that use `skills:` (artifact upload via `files_upload_v2`). Without it, the ask still answers; the artifact upload degrades to a no-op with a logged warning.

Then add this subsection to `DEPLOY.md` (covers the question "does per-project skills fit when we run Babbla in Docker, not from the local Claude CLI?"):

````markdown
## Running Babbla in Docker (and per-project skills)

The Agent SDK always drives the `claude` CLI subprocess — true for plain Q&A and
for skills alike. So per-project skills add **no new runtime**; they reuse the
same SDK→CLI path. Most of the container checklist is required for *any* Babbla
deploy, not just skills:

**Required for all of Babbla (not skills-specific):**
- **Bundle the `claude` CLI** in the image (the SDK shells out to it; it is not
  pure-Python). Pin a version compatible with the installed `claude-agent-sdk`.
- **Auth, one of:** mount the Path-B subscription credentials into the
  container's `$HOME/.claude` (read-only is fine), **or** set `ANTHROPIC_API_KEY`
  (Path A — already supported; `ANTHROPIC_API_KEY` is intentionally optional in
  `app.py`).
- **GitHub MCP launcher:** set `BABBLA_GITHUB_MCP=binary` and install the
  `github-mcp-server` binary in the image, so Babbla does **not** try to
  `docker run` the MCP server from inside its own container (which would need
  Docker-in-Docker). The skilled path reuses the same `mcp_servers`, so this one
  setting covers both.
- **Persist `CLAUDE_CONFIG_DIR`** (default `~/.claude`) on a writable volume that
  survives restarts: thread-scoped conversation **resume** (ADR 0013) reads the
  CLI's session transcripts from there. This matters for skilled *and*
  non-skilled threads.

**Skills-specific (small):**
- **Writable scratch:** skills write to a per-thread scratch dir under `$TMPDIR`.
  On a `--read-only` container, mount a `tmpfs` and point `TMPDIR` at it. The
  scratch is wiped per ask, so it can be fully ephemeral.
- **Bake `config/skills/` into the image** (like `config/channels.yaml`), or set
  `BABBLA_SKILLS_POOL` to a mounted path. An unknown skill name fails fast at
  config load, so a missing pool is loud, not silent.
- **Slack `files:write`** scope (above) for artifact upload.

**Isolation gets *better* in a container:** a clean image has no operator
`~/.claude/CLAUDE.md` or user-global skills, so the skilled path's
`setting_sources=["project"]` from a fresh scratch is airtight by construction.
````

- [ ] **Step 6: Write ADR 0015**

`docs/adr/0015-skilled-answer-path.md`:

```markdown
# 15. Skilled answer path: a bounded, read-only loosening for artifacts

Date: 2026-06-20

## Status

Accepted

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
```

- [ ] **Step 7: Run tests + config load**

Run: `python -m pytest tests/test_app.py -v && python -c "from babbla.config import load_config; load_config('config/channels.yaml'); print('config OK')"`
Expected: PASS, then `config OK` (the commented `skills:` doesn't break the load).

- [ ] **Step 8: Commit**

```bash
git add src/babbla/app.py config/channels.yaml DEPLOY.md docs/adr/0015-skilled-answer-path.md tests/test_app.py
git commit -m "feat: wire skills_pool + docs (skills: field, files:write, Docker), ADR 0015"
```

---

### Task 10: Full suite + live verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole unit suite**

Run: `python -m pytest -q -m "not integration"`
Expected: all green; the count is the prior baseline + the new tests.

- [ ] **Step 2: Live-verify end to end (manual)**

Temporarily add `skills: [architecture-diagram]` to a real local binding in
`config/channels.yaml` (local-only, not committed), set the bot's `files:write`
scope, run Babbla, and ask in that project's channel/DM:
*"draw the architecture of this project."*
Expected: a text reply summarizing the architecture, followed by an
`architecture.html` file uploaded in the same thread. Revert the local binding
edit afterward.

- [ ] **Step 3: Verify multi-turn resume in a skilled thread**

In the same skilled project, ask a question, then ask a **follow-up in the same
thread** ("and who reviewed that?"). Confirm the follow-up answers *with* prior
context and does **not** error — this exercises the per-thread stable scratch
(the per-request `mkdtemp` would crash here). Optionally run
`python tests/manual/skill_resume_smoke.py` (expects `recall=True same_sid=True`).

- [ ] **Step 4: Confirm zero-change for unconfigured bindings**

With `skills:` removed, ask a normal question in another project — confirm the
answer behaves exactly as before (no scratch, no upload). Confirm a skilled
project's **digest** still runs (it must take the plain branch — no scratch_key).

- [ ] **Step 5: Finalize**

Use `superpowers:finishing-a-development-branch` to merge/PR. Do not commit any
local-only `channels.yaml` bindings.

---

## Appendix — alternatives considered (Task 1) and what to do if SDK behavior changes

The lever is **validated**, so this is for posterity / future SDK upgrades. If a
later SDK/CLI version regresses (smoke test shows artifact missing or foreign
skills leaking), the only file to change is `read_only.skill_loading_kwargs()`
(and possibly `make_scratch_guard`); the rest of the plan is unaffected. Tried in
Task 1:

- **`dontAsk` + allow-listed `Write/Edit/Bash`** (original design) — builtins were
  **denied**; allow-listing does not permit builtins under `dontAsk`. ✗
- **`bypassPermissions`** — would write, but un-gates the agentmemory MCP
  **writers** (server exposes them; only `allowed_tools` keeps them out). Breaks
  read-only. ✗
- **`acceptEdits`** — writes succeed but are **unscoped**; an out-of-scratch probe
  leaked. ✗
- **`can_use_tool` (default mode)** — requires streaming-input prompts and is
  bypassed for any tool already in `allowed_tools`; in testing it did not fire and
  the write was denied with a stream error. ✗
- **`dontAsk` + `PreToolUse` scratch-guard hook** — writes land in scratch,
  out-of-scratch + `Bash` denied, MCP writers still denied, plain string prompt.
  **Chosen.** ✓

Untried but available if needed: wrap the pool as a local plugin
(`plugins=[{"type":"local","path":...}]`) instead of staging into
`<scratch>/.claude/skills`; or a profile-only fallback (inject SKILL.md body as
system text, text-only artifacts).

## Self-Review

- **Spec coverage:** config field + parse/validate (Task 3) ✓; pool + vetting
  README + seed (Task 2) ✓; scratch-guard hook + no-writer guard (Task 4) ✓;
  per-thread scratch + skill loading + artifact capture (Tasks 5-6) ✓;
  `SlackPoster.upload_file` + adapter upload + degrade (Task 7) ✓; orchestrator
  `scratch_key` wiring + artifact pass-through incl. lobby (Task 8) ✓; unchanged
  when unconfigured + digests stay non-skilled (Tasks 4, 6, 8, 10) ✓; V1/V2 **and
  resume** validated up front (Task 1) ✓; ADR + `files:write` + Docker deploy +
  channels.yaml docs (Task 9) ✓; multi-turn resume + digest-non-skilled
  live-checked (Task 10) ✓; private-project visibility unchanged (artifacts ride
  the existing authorized answer path — no new routing).
- **Deviations from the design (grounded in live SDK validation):** (a) skill
  enablement via `ClaudeAgentOptions.skills`, not `allowed_tools=["Skill"]` (SDK
  deprecation); (b) **writes gated by a `PreToolUse` scratch-guard hook under
  `dontAsk`**, not by allow-listing builtins (which the SDK denies) — this also
  *enforces* the scratch boundary the design only assumed; (c) **per-thread stable
  scratch path, not per-request `mkdtemp`** — the latter crashes session resume;
  the skilled branch is gated on a `scratch_key` (`thread_ts`), which also keeps
  digests off the skilled path; (d) artifacts carried as in-memory
  `Artifact(filename, bytes)` rather than scratch paths, because the scratch is
  wiped in `run_ask`'s `finally`; (e) upload happens in the Slack adapter, not the
  orchestrator, because the orchestrator never touches Slack. All are noted where
  they occur.
- **Type consistency:** `Artifact(filename, data)`, `CitedAnswer.artifacts`,
  `make_scratch_guard(scratch)`, `_within(path, root)`,
  `skill_loading_kwargs(scratch_dir=, skills=)`, `_scratch_path(scratch_key)`,
  `run_ask(..., scratch_key=)`, `_stage_skills(pool, names, scratch)`,
  `_collect_artifacts(scratch)`, `Secrets.skills_pool`,
  `SlackPoster.upload_file(channel_id, *, filename, content, title, thread_ts)`
  are used identically across tasks.
- **Lobby shapes confirmed:** Task 8 uses `CatalogEntry(binding, description)`
  and `LobbyThreadStore(path)` with `await put(thread_ts, name)` / `close()` —
  verified against `src/babbla/lobby.py` and `src/babbla/session_store.py` and
  the existing lobby tests in `tests/test_orchestrator.py`.
```

