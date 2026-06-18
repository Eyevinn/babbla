# MyTV Q&A Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Slack bot that answers natural-language questions about the MyTV project, citing commits/PRs/files, by driving the Claude Agent SDK over two read-only MCP servers (GitHub + agentmemory) against the GitHub remote — never a working tree.

**Architecture:** A long-running async Python process. `slack_bolt` (Socket Mode) receives `app_mention`/`message.im` events, acks fast, posts a placeholder, and hands `(text, thread_ts, channel, is_dm)` to an **orchestrator**. The orchestrator resolves the channel→project binding and the `thread_ts → session_id` map (SQLite), then calls an **agent runner** that builds a frozen read-only `ClaudeAgentOptions` and runs/resumes a `claude_agent_sdk` query. The runner's config is produced by a single guarded builder (`read_only.build_agent_config`) whose output a load-bearing regression test pins. Every component is injected at its seam so unit tests need no live Slack/GitHub/Claude.

**Tech Stack:** Python 3.14, `claude-agent-sdk`, `slack-bolt` (async + Socket Mode), `pyyaml`, stdlib `sqlite3` (async via `asyncio.to_thread`), `pytest` + `pytest-asyncio`. External: Docker (`ghcr.io/github/github-mcp-server`), `npx @agentmemory/mcp` bridge to the local agentmemory launchd service.

## Global Constraints

- **Read the canonical git remote, never a working tree.** Data source is GitHub (pushed commits/PRs/branches) + agentmemory, both over MCP. No local clone, no built-in filesystem/Bash/web tools granted to the agent.
- **`permission_mode` MUST be `"dontAsk"`. NEVER `"bypassPermissions"`** (confirmed to skip gating). `"dontAsk"` hard-denies any tool not in `allowed_tools` with no interactive prompt — correct for a headless server.
- **agentmemory allowlist is the load-bearing read-only layer.** Allowlist ONLY the four readers (`mcp__agentmemory__memory_recall`, `mcp__agentmemory__memory_smart_search`, `mcp__agentmemory__memory_facet_query`, `mcp__agentmemory__memory_relations`). NEVER any writer (`memory_save`, `memory_action_*`, `memory_governance_delete`, …).
- **github-mcp-server runs stdio (the `stdio` subcommand), never http**, with `GITHUB_READ_ONLY=1`.
- **Model default `claude-opus-4-8`** (one-line swap to `claude-sonnet-4-6`).
- **No secrets in code or `channels.yaml`.** Secrets come only from env: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, optional `AGENTMEMORY_URL` (default `http://localhost:3111`), `AGENTMEMORY_SECRET`.
- **MCP tool names are namespaced `mcp__<server>__<tool>`.** Server names used here: `github`, `agentmemory`.
- **Do NOT commit `PROPOSAL*.md`** (user: leave untracked). Scope every `git add` to explicit paths.

---

### Task 1: Project scaffold & toolchain

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/babbla/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable package `babbla` with `__version__: str`; a working `pytest` + `pytest-asyncio` toolchain (`asyncio_mode = "auto"`).

- [ ] **Step 1: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
.pytest_cache/

# Secrets & local state
.env
*.db
*.sqlite3

# Proposals (kept local, not tracked)
PROPOSAL.md
PROPOSAL-design.md
PROPOSAL-pitch.md
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "babbla"
version = "0.1.0"
description = "Read-only Slack assistant — MyTV Q&A pilot"
requires-python = ">=3.12"
dependencies = [
    "claude-agent-sdk",
    "slack-bolt>=1.18",
    "aiohttp",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: live test needing real Slack/GitHub/Claude tokens (deselect with -m 'not integration')",
]
```

- [ ] **Step 3: Write `.env.example`** (documents required env; never filled in with real values)

```bash
# Slack (Socket Mode): bot token + app-level token
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# GitHub fine-grained PAT, read-only, scoped to Wkkkkk/MyTV
GITHUB_TOKEN=github_pat_...

# Claude Agent SDK auth
ANTHROPIC_API_KEY=sk-ant-...

# agentmemory MCP bridge → local launchd backend (defaults shown)
AGENTMEMORY_URL=http://localhost:3111
AGENTMEMORY_SECRET=
```

- [ ] **Step 4: Write the package marker `src/babbla/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 5: Write `tests/__init__.py`** (empty file)

```python
```

- [ ] **Step 6: Write the failing toolchain test `tests/test_scaffold.py`**

```python
import babbla


def test_package_version():
    assert babbla.__version__ == "0.1.0"
```

- [ ] **Step 7: Create venv, install, run the test (expect FAIL → then PASS after install)**

```bash
cd babbla
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/test_scaffold.py -v
```
Expected: PASS (`test_package_version`). If `claude-agent-sdk` fails to resolve on PyPI, install the SDK by its published name and pin it here, then re-run.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore .env.example src/babbla/__init__.py tests/__init__.py tests/test_scaffold.py
git commit -m "chore: scaffold babbla package and test toolchain"
```

---

### Task 2: Config loader (`channels.yaml` → bindings)

**Files:**
- Create: `config/channels.yaml`
- Create: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) ProjectBinding(name: str, owner: str, repo: str, visibility: str, channel_id: str | None, dm: bool)`
  - `@dataclass(frozen=True) Config(bindings: tuple[ProjectBinding, ...])` with methods `for_channel(channel_id: str) -> ProjectBinding | None` and `for_dm() -> ProjectBinding | None`.
  - `load_config(path: str | os.PathLike) -> Config`.

- [ ] **Step 1: Write the config file `config/channels.yaml`**

```yaml
# Maps Slack surfaces -> Project + repo coordinates. Version-controlled (no secrets).
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    # Slack channel id for the Shared Ask surface. Fill in once the channel exists;
    # null means "no channel binding yet" (DM still works via dm: true).
    channel_id: null
    # Answer DMs (Private Ask) about this project. Exactly one project may set dm: true in the pilot.
    dm: true
```

- [ ] **Step 2: Write the failing test `tests/test_config.py`**

```python
from pathlib import Path

import pytest

from babbla.config import Config, ProjectBinding, load_config

FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "channels.yaml"
    p.write_text(text)
    return p


def test_loads_binding(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings == (
        ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True),
    )


def test_for_channel_matches(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.for_channel("C123").name == "MyTV"
    assert cfg.for_channel("CNOPE") is None


def test_for_dm_returns_dm_project(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.for_dm().name == "MyTV"


def test_null_channel_id_is_none(tmp_path):
    text = FIXTURE.replace("channel_id: C123", "channel_id: null")
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].channel_id is None
    assert cfg.for_channel("C123") is None
    assert cfg.for_dm().name == "MyTV"


def test_rejects_multiple_dm_projects(tmp_path):
    text = FIXTURE + """  - name: Other
    owner: o
    repo: r
    visibility: public
    channel_id: C999
    dm: true
"""
    with pytest.raises(ValueError, match="exactly one"):
        load_config(_write(tmp_path, text))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.config'`.

- [ ] **Step 4: Write `src/babbla/config.py`**

```python
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool


@dataclass(frozen=True)
class Config:
    bindings: tuple[ProjectBinding, ...]

    def for_channel(self, channel_id: str) -> ProjectBinding | None:
        for b in self.bindings:
            if b.channel_id is not None and b.channel_id == channel_id:
                return b
        return None

    def for_dm(self) -> ProjectBinding | None:
        for b in self.bindings:
            if b.dm:
                return b
        return None


def load_config(path: str | os.PathLike) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    bindings = tuple(
        ProjectBinding(
            name=p["name"],
            owner=p["owner"],
            repo=p["repo"],
            visibility=p["visibility"],
            channel_id=p.get("channel_id"),
            dm=bool(p.get("dm", False)),
        )
        for p in raw.get("projects", [])
    )
    if sum(1 for b in bindings if b.dm) > 1:
        raise ValueError("channels.yaml: exactly one project may set dm: true in the pilot")
    return Config(bindings=bindings)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add config/channels.yaml src/babbla/config.py tests/test_config.py
git commit -m "feat: channels.yaml config loader with surface->project resolution"
```

---

### Task 3: Read-only agent config builder + load-bearing guard test

**Files:**
- Create: `src/babbla/read_only.py`
- Test: `tests/test_read_only_guard.py`

**Interfaces:**
- Consumes: nothing (pure builder; takes plain args).
- Produces:
  - `@dataclass(frozen=True) AgentConfig(model: str, system_prompt: str, allowed_tools: tuple[str, ...], permission_mode: str, mcp_servers: dict)`
  - module constants `AGENTMEMORY_READERS: tuple[str, ...]`, `AGENTMEMORY_WRITERS: tuple[str, ...]`, `GITHUB_TOOL_PREFIX = "mcp__github__"`, `ALLOWED_TOOLS: tuple[str, ...]`, `DEFAULT_MODEL = "claude-opus-4-8"`.
  - `build_system_prompt(owner: str, repo: str) -> str`
  - `build_agent_config(*, owner: str, repo: str, github_token: str, agentmemory_url: str, agentmemory_secret: str, model: str = DEFAULT_MODEL) -> AgentConfig`

- [ ] **Step 1: Write the failing guard test `tests/test_read_only_guard.py`**

```python
import pytest

from babbla.read_only import (
    AGENTMEMORY_READERS,
    AGENTMEMORY_WRITERS,
    ALLOWED_TOOLS,
    DEFAULT_MODEL,
    build_agent_config,
)

# Built-in tool names that must NEVER be granted to a read-only agent.
FORBIDDEN_BUILTINS = ("Bash", "Write", "Edit", "Read", "NotebookEdit", "WebFetch", "WebSearch")


@pytest.fixture
def cfg():
    return build_agent_config(
        owner="Wkkkkk",
        repo="MyTV",
        github_token="ghp_dummy",
        agentmemory_url="http://localhost:3111",
        agentmemory_secret="",
    )


def test_permission_mode_is_dontask(cfg):
    assert cfg.permission_mode == "dontAsk"


def test_permission_mode_never_bypass(cfg):
    assert cfg.permission_mode != "bypassPermissions"


def test_only_mcp_tools_allowed(cfg):
    # Every allowlisted tool is an MCP tool — no built-in filesystem/bash/web tools.
    for tool in cfg.allowed_tools:
        assert tool.startswith("mcp__"), f"non-MCP tool allowlisted: {tool}"
    for builtin in FORBIDDEN_BUILTINS:
        assert builtin not in cfg.allowed_tools


def test_agentmemory_only_readers(cfg):
    am_tools = [t for t in cfg.allowed_tools if t.startswith("mcp__agentmemory__")]
    assert set(am_tools) == set(AGENTMEMORY_READERS)


def test_no_agentmemory_writer_allowlisted(cfg):
    for writer in AGENTMEMORY_WRITERS:
        assert writer not in cfg.allowed_tools


def test_github_server_is_readonly_stdio(cfg):
    gh = cfg.mcp_servers["github"]
    assert gh["command"] == "docker"
    assert "stdio" in gh["args"]
    assert "http" not in gh["args"]
    assert gh["env"]["GITHUB_READ_ONLY"] == "1"
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_dummy"


def test_agentmemory_server_configured(cfg):
    am = cfg.mcp_servers["agentmemory"]
    assert am["command"] == "npx"
    assert am["env"]["AGENTMEMORY_URL"] == "http://localhost:3111"


def test_allowed_tools_matches_frozen_set(cfg):
    assert cfg.allowed_tools == ALLOWED_TOOLS


def test_default_model(cfg):
    assert cfg.model == DEFAULT_MODEL


def test_system_prompt_names_repo(cfg):
    assert "Wkkkkk/MyTV" in cfg.system_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_read_only_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.read_only'`.

- [ ] **Step 3: Write `src/babbla/read_only.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MODEL = "claude-opus-4-8"

GITHUB_TOOL_PREFIX = "mcp__github__"

# The ONLY agentmemory tools the agent may call. Adding one here also requires
# updating the guard test's expectation. Never add a writer.
AGENTMEMORY_READERS: tuple[str, ...] = (
    "mcp__agentmemory__memory_recall",
    "mcp__agentmemory__memory_smart_search",
    "mcp__agentmemory__memory_facet_query",
    "mcp__agentmemory__memory_relations",
)

# agentmemory mutating tools — listed so the guard test can assert none leak in.
AGENTMEMORY_WRITERS: tuple[str, ...] = (
    "mcp__agentmemory__memory_save",
    "mcp__agentmemory__memory_action_create",
    "mcp__agentmemory__memory_action_update",
    "mcp__agentmemory__memory_governance_delete",
)

# GitHub read-only-ness is enforced server-side (GITHUB_READ_ONLY=1 + the `stdio`
# subcommand), so a wildcard over that server is safe: the server cannot expose a
# writer. agentmemory exposes writers, so it is allowlisted tool-by-tool above.
GITHUB_WILDCARD = "mcp__github__*"

ALLOWED_TOOLS: tuple[str, ...] = (GITHUB_WILDCARD, *AGENTMEMORY_READERS)


@dataclass(frozen=True)
class AgentConfig:
    model: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    permission_mode: str
    mcp_servers: dict


def build_system_prompt(owner: str, repo: str) -> str:
    slug = f"{owner}/{repo}"
    return (
        f"You are Babbla, a read-only assistant answering questions about the "
        f"{slug} project on GitHub. Answer ONLY from {slug}'s pushed history and code "
        f"(commits, pull requests, branches, files) reachable via the github tools, plus "
        f"rationale from the agentmemory tools. You have no write access and no local files.\n\n"
        f"Rules:\n"
        f"- Default to the repository's default branch (main) as the shared truth; inspect a "
        f"specific PR or pushed branch only when the question calls for it.\n"
        f"- ALWAYS cite your sources as GitHub links: commit SHAs, pull request numbers, and "
        f"file paths (e.g. https://github.com/{slug}/commit/<sha>, "
        f"https://github.com/{slug}/pull/<n>, https://github.com/{slug}/blob/main/<path>).\n"
        f"- If the answer is not in {slug}'s history, say so plainly "
        f"(\"I don't know — that's not in {slug}'s history\"). Never guess or invent sources.\n"
        f"- Keep answers concise and Slack-friendly."
    )


def build_agent_config(
    *,
    owner: str,
    repo: str,
    github_token: str,
    agentmemory_url: str,
    agentmemory_secret: str,
    model: str = DEFAULT_MODEL,
) -> AgentConfig:
    mcp_servers = {
        "github": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "-e",
                "GITHUB_READ_ONLY",
                "-e",
                "GITHUB_TOOLSETS",
                "ghcr.io/github/github-mcp-server",
                "stdio",
            ],
            "env": {
                "GITHUB_PERSONAL_ACCESS_TOKEN": github_token,
                "GITHUB_READ_ONLY": "1",
                "GITHUB_TOOLSETS": "context,repos,pull_requests,issues",
            },
        },
        "agentmemory": {
            "command": "npx",
            "args": ["-y", "@agentmemory/mcp"],
            "env": {
                "AGENTMEMORY_URL": agentmemory_url,
                "AGENTMEMORY_SECRET": agentmemory_secret,
            },
        },
    }
    return AgentConfig(
        model=model,
        system_prompt=build_system_prompt(owner, repo),
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="dontAsk",
        mcp_servers=mcp_servers,
    )
```

> Note: Docker passes `-e GITHUB_PERSONAL_ACCESS_TOKEN` (name only) to inherit the value, but the SDK spawns the process with the server's `env` dict, so the value is present in the child environment and forwarded. The explicit `env` dict is the source of truth and is what the guard test asserts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_read_only_guard.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/read_only.py tests/test_read_only_guard.py
git commit -m "feat: read-only agent config builder + load-bearing guard test"
```

---

### Task 4: Session store (SQLite `thread_ts → session_id` with TTL)

**Files:**
- Create: `src/babbla/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class SessionStore` with `__init__(self, db_path: str, ttl_seconds: int = 86400, time_fn: Callable[[], float] = time.time)`.
  - `async def get_session(self, thread_ts: str) -> str | None` — returns the stored `session_id`, or `None` if absent or older than TTL (and deletes the expired row).
  - `async def put_session(self, thread_ts: str, session_id: str) -> None` — upserts, stamping the current time.
  - `def close(self) -> None`.

- [ ] **Step 1: Write the failing test `tests/test_session_store.py`**

```python
from babbla.session_store import SessionStore


async def test_put_then_get_roundtrip(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    await store.put_session("t1", "sess-abc")
    assert await store.get_session("t1") == "sess-abc"
    store.close()


async def test_missing_thread_returns_none(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    assert await store.get_session("nope") is None
    store.close()


async def test_put_overwrites(tmp_path):
    store = SessionStore(str(tmp_path / "s.db"))
    await store.put_session("t1", "sess-1")
    await store.put_session("t1", "sess-2")
    assert await store.get_session("t1") == "sess-2"
    store.close()


async def test_ttl_eviction(tmp_path):
    clock = {"now": 1000.0}
    store = SessionStore(str(tmp_path / "s.db"), ttl_seconds=100, time_fn=lambda: clock["now"])
    await store.put_session("t1", "sess-old")
    clock["now"] = 1101.0  # 101s later, past the 100s TTL
    assert await store.get_session("t1") is None
    # expired row is gone, so a fresh put starts clean
    await store.put_session("t1", "sess-new")
    assert await store.get_session("t1") == "sess-new"
    store.close()


async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.db")
    store = SessionStore(path)
    await store.put_session("t1", "sess-abc")
    store.close()
    store2 = SessionStore(path)
    assert await store2.get_session("t1") == "sess-abc"
    store2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_session_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.session_store'`.

- [ ] **Step 3: Write `src/babbla/session_store.py`**

```python
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Callable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_ts  TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at REAL NOT NULL
)
"""


class SessionStore:
    def __init__(
        self,
        db_path: str,
        ttl_seconds: int = 86400,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = ttl_seconds
        self._now = time_fn
        # check_same_thread=False: we serialize access through asyncio.to_thread.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    async def get_session(self, thread_ts: str) -> str | None:
        return await asyncio.to_thread(self._get_session_sync, thread_ts)

    def _get_session_sync(self, thread_ts: str) -> str | None:
        row = self._conn.execute(
            "SELECT session_id, updated_at FROM sessions WHERE thread_ts = ?",
            (thread_ts,),
        ).fetchone()
        if row is None:
            return None
        session_id, updated_at = row
        if self._now() - updated_at > self._ttl:
            self._conn.execute("DELETE FROM sessions WHERE thread_ts = ?", (thread_ts,))
            self._conn.commit()
            return None
        return session_id

    async def put_session(self, thread_ts: str, session_id: str) -> None:
        await asyncio.to_thread(self._put_session_sync, thread_ts, session_id)

    def _put_session_sync(self, thread_ts: str, session_id: str) -> None:
        self._conn.execute(
            "INSERT INTO sessions (thread_ts, session_id, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_ts) DO UPDATE SET session_id = excluded.session_id, "
            "updated_at = excluded.updated_at",
            (thread_ts, session_id, self._now()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_session_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_session_store.py
git commit -m "feat: SQLite session store with TTL eviction"
```

---

### Task 5: Agent runner (`claude_agent_sdk` query/resume → CitedAnswer)

**Files:**
- Create: `src/babbla/agent_runner.py`
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `babbla.config.ProjectBinding`; `babbla.read_only.build_agent_config` and `AgentConfig`.
- Produces:
  - `@dataclass(frozen=True) CitedAnswer(text: str, session_id: str | None)`
  - `@dataclass(frozen=True) Secrets(github_token: str, agentmemory_url: str, agentmemory_secret: str, model: str = DEFAULT_MODEL)`
  - `class AgentRunner` with `__init__(self, secrets: Secrets, query_fn=...)` and
    `async def run_ask(self, text: str, binding: ProjectBinding, resume_session_id: str | None) -> CitedAnswer`.
  - `query_fn` signature matches `claude_agent_sdk.query(prompt: str, options) -> AsyncIterator[message]`. The runner builds `ClaudeAgentOptions` from the `AgentConfig` and (when resuming) sets `resume=resume_session_id`. It collects assistant text and reads `session_id` + `result` off the terminal `ResultMessage`.

- [ ] **Step 1: Write the failing test `tests/test_agent_runner.py`**

```python
import pytest

from babbla.agent_runner import AgentRunner, CitedAnswer, Secrets
from babbla.config import ProjectBinding

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
SECRETS = Secrets(github_token="ghp_x", agentmemory_url="http://localhost:3111", agentmemory_secret="")


class FakeResultMessage:
    def __init__(self, result, session_id):
        self.result = result
        self.session_id = session_id


def make_query_fn(captured, *, result="Because of PR #58 https://github.com/Wkkkkk/MyTV/pull/58",
                  session_id="sess-new"):
    async def fake_query(prompt, options=None):
        captured["prompt"] = prompt
        captured["options"] = options
        yield FakeResultMessage(result=result, session_id=session_id)
    return fake_query


async def test_run_ask_returns_cited_answer():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    ans = await runner.run_ask("why pr 58?", BINDING, resume_session_id=None)
    assert isinstance(ans, CitedAnswer)
    assert "PR #58" in ans.text
    assert ans.session_id == "sess-new"


async def test_run_ask_passes_readonly_options():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    opts = captured["options"]
    assert opts.permission_mode == "dontAsk"
    assert opts.permission_mode != "bypassPermissions"
    assert all(t.startswith("mcp__") for t in opts.allowed_tools)
    assert "github" in opts.mcp_servers and "agentmemory" in opts.mcp_servers


async def test_run_ask_new_session_has_no_resume():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured))
    await runner.run_ask("q", BINDING, resume_session_id=None)
    # resume is None/unset for a brand-new session
    assert getattr(captured["options"], "resume", None) in (None, "")


async def test_run_ask_resume_sets_session():
    captured = {}
    runner = AgentRunner(SECRETS, query_fn=make_query_fn(captured, session_id="sess-resumed"))
    ans = await runner.run_ask("follow up", BINDING, resume_session_id="sess-old")
    assert captured["options"].resume == "sess-old"
    assert ans.session_id == "sess-resumed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_agent_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.agent_runner'`.

- [ ] **Step 3: Write `src/babbla/agent_runner.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from babbla.config import ProjectBinding
from babbla.read_only import DEFAULT_MODEL, build_agent_config


@dataclass(frozen=True)
class CitedAnswer:
    text: str
    session_id: str | None


@dataclass(frozen=True)
class Secrets:
    github_token: str
    agentmemory_url: str
    agentmemory_secret: str
    model: str = DEFAULT_MODEL


def _extract_text(message) -> str | None:
    """Return assistant-visible text from a message, or None if it carries none."""
    # Terminal ResultMessage carries the final string in `.result`.
    result = getattr(message, "result", None)
    if isinstance(result, str) and result:
        return result
    # AssistantMessage carries a list of content blocks with `.text` on text blocks.
    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = [getattr(b, "text", "") for b in content if getattr(b, "text", "")]
        if parts:
            return " ".join(parts)
    return None


class AgentRunner:
    def __init__(self, secrets: Secrets, query_fn=_sdk_query) -> None:
        self._secrets = secrets
        self._query = query_fn

    async def run_ask(
        self, text: str, binding: ProjectBinding, resume_session_id: str | None
    ) -> CitedAnswer:
        cfg = build_agent_config(
            owner=binding.owner,
            repo=binding.repo,
            github_token=self._secrets.github_token,
            agentmemory_url=self._secrets.agentmemory_url,
            agentmemory_secret=self._secrets.agentmemory_secret,
            model=self._secrets.model,
        )
        options = ClaudeAgentOptions(
            model=cfg.model,
            system_prompt=cfg.system_prompt,
            allowed_tools=list(cfg.allowed_tools),
            permission_mode=cfg.permission_mode,
            mcp_servers=cfg.mcp_servers,
        )
        if resume_session_id:
            options.resume = resume_session_id

        last_text: str | None = None
        session_id: str | None = resume_session_id
        async for message in self._query(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                last_text = captured
            sid = getattr(message, "session_id", None)
            if sid:
                session_id = sid

        return CitedAnswer(
            text=last_text or "I don't know — I couldn't find anything in MyTV's history.",
            session_id=session_id,
        )
```

> **Pin-at-wiring note:** `ClaudeAgentOptions` field names (`permission_mode`, `allowed_tools`, `mcp_servers`, `resume`, `system_prompt`, `model`) and the `permission_mode="dontAsk"` value were confirmed against the Agent SDK docs during planning. If construction errors on first live run (e.g. `resume` is constructor-only and not settable as an attribute), set it via the constructor instead: build the kwargs dict and pass `resume=` only when resuming. The guard test (Task 3) and `test_run_ask_passes_readonly_options` lock the *invariants*; adjust binding only.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_agent_runner.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/agent_runner.py tests/test_agent_runner.py
git commit -m "feat: agent runner over claude-agent-sdk with session resume"
```

---

### Task 6: Orchestrator (the Ask seam)

**Files:**
- Create: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `babbla.config.Config` + `ProjectBinding`; `babbla.session_store.SessionStore`; `babbla.agent_runner.AgentRunner` + `CitedAnswer`.
- Produces:
  - `class UnknownSurfaceError(Exception)` — raised when no binding matches the surface.
  - `class Orchestrator` with `__init__(self, config: Config, runner: AgentRunner, store: SessionStore)` and
    `async def handle_ask(self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool) -> CitedAnswer`.
  - Behavior: resolve binding (`for_channel` for channels, `for_dm` for DMs); look up prior `session_id` by `thread_ts`; call `runner.run_ask(text, binding, resume_session_id)`; persist the returned `session_id`; return the `CitedAnswer`. A per-`thread_ts` `asyncio.Lock` serializes concurrent replies in one thread.

- [ ] **Step 1: Write the failing test `tests/test_orchestrator.py`**

```python
import asyncio

import pytest

from babbla.agent_runner import CitedAnswer
from babbla.config import Config, ProjectBinding
from babbla.orchestrator import Orchestrator, UnknownSurfaceError
from babbla.session_store import SessionStore

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", True)
CONFIG = Config(bindings=(BINDING,))


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.next_session = "sess-1"

    async def run_ask(self, text, binding, resume_session_id):
        self.calls.append((text, binding, resume_session_id))
        return CitedAnswer(text=f"answer to {text}", session_id=self.next_session)


@pytest.fixture
def store(tmp_path):
    s = SessionStore(str(tmp_path / "s.db"))
    yield s
    s.close()


async def test_new_thread_creates_session(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False)
    assert ans.text == "answer to q1"
    assert runner.calls[0][2] is None  # no resume on first message
    assert await store.get_session("t1") == "sess-1"


async def test_followup_resumes_session(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    await orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False)
    runner.next_session = "sess-1"  # SDK may keep same id on resume
    await orch.handle_ask(text="q2", thread_ts="t1", channel_id="C123", is_dm=False)
    assert runner.calls[1][2] == "sess-1"  # resumed with prior session id


async def test_dm_resolves_via_for_dm(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="t9", channel_id="D999", is_dm=True)
    assert runner.calls[0][1].name == "MyTV"
    assert ans.text == "answer to q"


async def test_unknown_channel_raises(store):
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    with pytest.raises(UnknownSurfaceError):
        await orch.handle_ask(text="q", thread_ts="t1", channel_id="CNOPE", is_dm=False)


async def test_per_thread_lock_serializes(store):
    # Two concurrent asks in the SAME thread must not both run with resume=None.
    order = []
    binding = BINDING

    class SlowRunner:
        async def run_ask(self, text, binding, resume_session_id):
            order.append(("start", text, resume_session_id))
            await asyncio.sleep(0.01)
            order.append(("end", text))
            return CitedAnswer(text=f"a-{text}", session_id="sess-1")

    orch = Orchestrator(CONFIG, SlowRunner(), store)
    await asyncio.gather(
        orch.handle_ask(text="q1", thread_ts="t1", channel_id="C123", is_dm=False),
        orch.handle_ask(text="q2", thread_ts="t1", channel_id="C123", is_dm=False),
    )
    # Serialized: first ask fully completes before the second starts.
    assert order[0][0] == "start" and order[1][0] == "end"
    # The second ask saw the session the first one stored.
    second_start = [o for o in order if o[0] == "start"][1]
    assert second_start[2] == "sess-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.orchestrator'`.

- [ ] **Step 3: Write `src/babbla/orchestrator.py`**

```python
from __future__ import annotations

import asyncio

from babbla.agent_runner import CitedAnswer
from babbla.config import Config, ProjectBinding


class UnknownSurfaceError(Exception):
    """No project binding matches the Slack surface the question came from."""


class Orchestrator:
    def __init__(self, config: Config, runner, store) -> None:
        self._config = config
        self._runner = runner
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, thread_ts: str) -> asyncio.Lock:
        lock = self._locks.get(thread_ts)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_ts] = lock
        return lock

    def _resolve(self, channel_id: str, is_dm: bool) -> ProjectBinding:
        binding = self._config.for_dm() if is_dm else self._config.for_channel(channel_id)
        if binding is None:
            raise UnknownSurfaceError(
                f"No project bound to {'DM' if is_dm else channel_id}"
            )
        return binding

    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool
    ) -> CitedAnswer:
        binding = self._resolve(channel_id, is_dm)
        async with self._lock_for(thread_ts):
            resume_session_id = await self._store.get_session(thread_ts)
            answer = await self._runner.run_ask(text, binding, resume_session_id)
            if answer.session_id:
                await self._store.put_session(thread_ts, answer.session_id)
            return answer
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_orchestrator.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: ask orchestrator with thread->session mapping and per-thread lock"
```

---

### Task 7: Slack adapter (Socket Mode, placeholder + edit, threading)

**Files:**
- Create: `src/babbla/slack_adapter.py`
- Test: `tests/test_slack_adapter.py`

**Interfaces:**
- Consumes: `babbla.orchestrator.Orchestrator` + `UnknownSurfaceError`; `babbla.agent_runner.CitedAnswer`.
- Produces:
  - `PLACEHOLDER = "🔎 looking into it…"` and `ERROR_TEXT = "⚠️ Couldn't answer that right now — please try again shortly."`
  - `def clean_mention_text(text: str) -> str` — strips a leading `<@U…>` bot mention.
  - `async def process_ask(*, text: str, channel: str, thread_ts: str, is_dm: bool, client, orchestrator: Orchestrator) -> None` — posts the placeholder in-thread, calls `orchestrator.handle_ask`, then edits the placeholder to the answer; on any exception, edits it to `ERROR_TEXT`.
  - `def register_handlers(app, orchestrator: Orchestrator) -> None` — wires `app_mention` and `message` (DM) Bolt listeners to `process_ask` via `asyncio.create_task` so the event acks within 3s.

- [ ] **Step 1: Write the failing test `tests/test_slack_adapter.py`**

```python
import pytest

from babbla.agent_runner import CitedAnswer
from babbla.slack_adapter import (
    ERROR_TEXT,
    PLACEHOLDER,
    clean_mention_text,
    process_ask,
)


class FakeClient:
    def __init__(self):
        self.posted = None
        self.updates = []

    async def chat_postMessage(self, *, channel, thread_ts, text):
        self.posted = {"channel": channel, "thread_ts": thread_ts, "text": text}
        return {"ts": "msg-1"}

    async def chat_update(self, *, channel, ts, text):
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ok": True}


class FakeOrch:
    def __init__(self, answer=None, exc=None):
        self.answer = answer
        self.exc = exc
        self.calls = []

    async def handle_ask(self, *, text, thread_ts, channel_id, is_dm):
        self.calls.append({"text": text, "thread_ts": thread_ts, "channel_id": channel_id, "is_dm": is_dm})
        if self.exc:
            raise self.exc
        return self.answer


def test_clean_mention_text_strips_bot():
    assert clean_mention_text("<@U123> why did we move branding?") == "why did we move branding?"
    assert clean_mention_text("no mention here") == "no mention here"


async def test_process_ask_posts_placeholder_then_answer():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="Because PR #58", session_id="s1"))
    await process_ask(
        text="why?", channel="C123", thread_ts="t1", is_dm=False, client=client, orchestrator=orch
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.posted["thread_ts"] == "t1"
    assert client.updates[-1]["text"] == "Because PR #58"
    assert client.updates[-1]["ts"] == "msg-1"
    assert orch.calls[0]["channel_id"] == "C123"
    assert orch.calls[0]["is_dm"] is False


async def test_process_ask_edits_to_error_on_failure():
    client = FakeClient()
    orch = FakeOrch(exc=RuntimeError("github down"))
    await process_ask(
        text="why?", channel="C123", thread_ts="t1", is_dm=False, client=client, orchestrator=orch
    )
    assert client.posted["text"] == PLACEHOLDER
    assert client.updates[-1]["text"] == ERROR_TEXT  # no dangling placeholder


async def test_process_ask_passes_is_dm():
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    await process_ask(
        text="q", channel="D9", thread_ts="t9", is_dm=True, client=client, orchestrator=orch
    )
    assert orch.calls[0]["is_dm"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_slack_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.slack_adapter'`.

- [ ] **Step 3: Write `src/babbla/slack_adapter.py`**

```python
from __future__ import annotations

import asyncio
import logging
import re

from babbla.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

PLACEHOLDER = "🔎 looking into it…"
ERROR_TEXT = "⚠️ Couldn't answer that right now — please try again shortly."

_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*")


def clean_mention_text(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


async def process_ask(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    is_dm: bool,
    client,
    orchestrator: Orchestrator,
) -> None:
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    try:
        answer = await orchestrator.handle_ask(
            text=text, thread_ts=thread_ts, channel_id=channel, is_dm=is_dm
        )
        await client.chat_update(channel=channel, ts=ts, text=answer.text)
    except Exception:  # one failed Ask must never crash the process
        logger.exception("Ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)


def register_handlers(app, orchestrator: Orchestrator) -> None:
    @app.event("app_mention")
    async def _on_mention(event, client):
        thread_ts = event.get("thread_ts") or event["ts"]
        asyncio.create_task(
            process_ask(
                text=clean_mention_text(event.get("text", "")),
                channel=event["channel"],
                thread_ts=thread_ts,
                is_dm=False,
                client=client,
                orchestrator=orchestrator,
            )
        )

    @app.event("message")
    async def _on_message(event, client):
        # DM (Private Ask) only; ignore bot echoes and non-DM channel messages
        # (channel questions arrive via app_mention).
        if event.get("channel_type") != "im" or event.get("bot_id"):
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        asyncio.create_task(
            process_ask(
                text=(event.get("text") or "").strip(),
                channel=event["channel"],
                thread_ts=thread_ts,
                is_dm=True,
                client=client,
                orchestrator=orchestrator,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_slack_adapter.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/slack_adapter.py tests/test_slack_adapter.py
git commit -m "feat: slack adapter with placeholder/edit, threading, error path"
```

---

### Task 8: App wiring & entrypoint

**Files:**
- Create: `src/babbla/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: everything above; `slack_bolt.async_app.AsyncApp` + `slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler`.
- Produces:
  - `def load_secrets(env: Mapping[str, str]) -> Secrets` — reads `GITHUB_TOKEN`, `ANTHROPIC_API_KEY` (presence-checked), `AGENTMEMORY_URL` (default `http://localhost:3111`), `AGENTMEMORY_SECRET` (default `""`). Raises `KeyError`-style `RuntimeError` listing any missing required var.
  - `def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets) -> Orchestrator`.
  - `async def main() -> None` — builds the `AsyncApp`, registers handlers, starts `AsyncSocketModeHandler`.

- [ ] **Step 1: Write the failing test `tests/test_app.py`**

```python
import pytest

from babbla.app import build_orchestrator, load_secrets
from babbla.orchestrator import Orchestrator

ENV = {
    "SLACK_BOT_TOKEN": "xoxb-x",
    "SLACK_APP_TOKEN": "xapp-x",
    "GITHUB_TOKEN": "ghp_x",
    "ANTHROPIC_API_KEY": "sk-x",
}


def test_load_secrets_defaults():
    s = load_secrets(ENV)
    assert s.github_token == "ghp_x"
    assert s.agentmemory_url == "http://localhost:3111"
    assert s.agentmemory_secret == ""


def test_load_secrets_missing_required_raises():
    broken = dict(ENV)
    del broken["GITHUB_TOKEN"]
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        load_secrets(broken)


def test_load_secrets_custom_agentmemory():
    env = dict(ENV, AGENTMEMORY_URL="http://localhost:9999", AGENTMEMORY_SECRET="shh")
    s = load_secrets(env)
    assert s.agentmemory_url == "http://localhost:9999"
    assert s.agentmemory_secret == "shh"


def test_build_orchestrator(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert isinstance(orch, Orchestrator)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.app'`.

- [ ] **Step 3: Write `src/babbla/app.py`**

```python
from __future__ import annotations

import asyncio
import logging
import os
from typing import Mapping

from babbla.agent_runner import AgentRunner, Secrets
from babbla.config import load_config
from babbla.orchestrator import Orchestrator
from babbla.read_only import DEFAULT_MODEL
from babbla.session_store import SessionStore
from babbla.slack_adapter import register_handlers

logger = logging.getLogger(__name__)

_REQUIRED = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY")


def load_secrets(env: Mapping[str, str]) -> Secrets:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return Secrets(
        github_token=env["GITHUB_TOKEN"],
        agentmemory_url=env.get("AGENTMEMORY_URL", "http://localhost:3111"),
        agentmemory_secret=env.get("AGENTMEMORY_SECRET", ""),
        model=env.get("BABBLA_MODEL", DEFAULT_MODEL),
    )


def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets) -> Orchestrator:
    config = load_config(config_path)
    runner = AgentRunner(secrets)
    store = SessionStore(db_path)
    return Orchestrator(config, runner, store)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    secrets = load_secrets(os.environ)
    orchestrator = build_orchestrator(
        config_path=os.environ.get("BABBLA_CONFIG", "config/channels.yaml"),
        db_path=os.environ.get("BABBLA_DB", "babbla.db"),
        secrets=secrets,
    )
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    register_handlers(app, orchestrator)
    handler = AsyncSocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    logger.info("Babbla (MyTV Q&A pilot) starting in Socket Mode…")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: PASS (4 tests). (`main()` is not exercised — no live Slack connection in tests.)

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -m "not integration" -v`
Expected: PASS (all unit tests across Tasks 1–8).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/app.py tests/test_app.py
git commit -m "feat: app wiring, secrets loading, Socket Mode entrypoint"
```

---

### Task 9: Smoke / integration test + run docs

**Files:**
- Create: `tests/test_smoke.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `babbla.agent_runner.AgentRunner` + `Secrets`; `babbla.config.ProjectBinding`. Uses the **real** `claude_agent_sdk.query` (no stub) and live MCP servers.
- Produces: a `@pytest.mark.integration` test that asks the live public MyTV repo a known question and asserts the answer references a GitHub citation; plus operator run docs.

- [ ] **Step 1: Write the integration test `tests/test_smoke.py`**

```python
import os
import re

import pytest

from babbla.agent_runner import AgentRunner, Secrets
from babbla.config import ProjectBinding

BINDING = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", None, True)

CITATION_RE = re.compile(r"github\.com/Wkkkkk/MyTV/(commit|pull|blob)/", re.IGNORECASE)


@pytest.mark.integration
async def test_live_ask_cites_a_source():
    if not (os.environ.get("GITHUB_TOKEN") and os.environ.get("ANTHROPIC_API_KEY")):
        pytest.skip("integration test needs GITHUB_TOKEN and ANTHROPIC_API_KEY")
    secrets = Secrets(
        github_token=os.environ["GITHUB_TOKEN"],
        agentmemory_url=os.environ.get("AGENTMEMORY_URL", "http://localhost:3111"),
        agentmemory_secret=os.environ.get("AGENTMEMORY_SECRET", ""),
    )
    runner = AgentRunner(secrets)
    answer = await runner.run_ask(
        "What does the MyTV repository do? Cite a specific file or commit.",
        BINDING,
        resume_session_id=None,
    )
    assert answer.text  # non-empty
    assert CITATION_RE.search(answer.text), f"answer carried no GitHub citation:\n{answer.text}"
```

- [ ] **Step 2: Run it (requires Docker running + tokens exported)**

Run: `.venv/bin/pytest tests/test_smoke.py -m integration -v -s`
Expected: PASS — the answer text contains a `github.com/Wkkkkk/MyTV/(commit|pull|blob)/` link. If it fails with an MCP launch error, confirm Docker is running (`docker ps`), the image pulls (`docker pull ghcr.io/github/github-mcp-server`), and `npx -y @agentmemory/mcp` resolves.

- [ ] **Step 3: Write `README.md`**

````markdown
# Babbla — MyTV Q&A Pilot

Read-only Slack assistant: ask natural-language questions about the MyTV project and get
answers cited to commits/PRs/files — drawn from the GitHub remote, never a local working tree.

## Prerequisites
- Python 3.12+ (`.venv` created via `python3 -m venv .venv`)
- Docker (for `ghcr.io/github/github-mcp-server`)
- `npx` available (Node) for the `@agentmemory/mcp` bridge
- agentmemory backend running locally (launchd `com.agentmemory.server`)

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in real tokens (never commit .env)
```

## Run
```bash
set -a && source .env && set +a
.venv/bin/python -m babbla.app
```

## Test
```bash
.venv/bin/pytest -m "not integration"      # fast unit suite, no tokens
.venv/bin/pytest -m integration -s         # live smoke test (needs Docker + tokens)
```

## Read-only safety
The agent is granted only read tools from two MCP servers — `github` (`GITHUB_READ_ONLY=1`,
stdio) and `agentmemory` (four reader tools only). `permission_mode="dontAsk"` hard-denies
anything off the allowlist. See `src/babbla/read_only.py` and the guard test
`tests/test_read_only_guard.py`.
````

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke.py README.md
git commit -m "test: live MyTV smoke test asserting cited answer + run docs"
```

---

## Self-Review

**Spec coverage:**
- Definition-of-done (ack<3s, read-only via two MCP servers, cited reply, threaded follow-ups) → Tasks 7 (placeholder/ack), 3+5 (read-only MCP + citations), 4+6 (threaded resume). ✓
- Six components → Slack adapter (T7), orchestrator (T6), agent runner (T5), MCP layer (T3 config), session store (T4), config (T2). ✓
- 6-layer read-only enforcement → T3 builder + guard test encodes layers 1–5; layer 6 (reads remote) is structural in the github MCP server. ✓
- Threading & session lifecycle (new vs resume, TTL, per-thread lock) → T4 (TTL) + T6 (resume + lock). ✓
- Error handling (3s ack, per-Ask failure isolation, no dangling placeholder, no-answer text) → T7 (error edit) + T5 (fallback text) + system prompt (T3). ✓
- Testing strategy items 1–5 → T6, T3, T5, T2, T9 respectively. ✓
- Config & secrets (channels.yaml version-controlled; secrets env-only) → T2 + T8. ✓
- Decided defaults (foreground process, SQLite, opus model) → T8 main(), T4, T3. ✓

**Placeholder scan:** No TBD/"add error handling"/"similar to Task N". All steps carry complete code and exact commands. ✓

**Type consistency:** `ProjectBinding`, `AgentConfig`, `CitedAnswer`, `Secrets`, `Config`, `SessionStore`, `AgentRunner`, `Orchestrator` names/signatures match across T2–T9. `build_agent_config(**kwargs)` signature in T3 matches its caller in T5. `process_ask(**kwargs)` in T7 matches its test. `handle_ask(**kwargs)` keyword signature consistent T6↔T7. ✓

## Risks to watch during execution
- **Agent SDK field binding** (`ClaudeAgentOptions` attrs, `resume` settability, `permission_mode="dontAsk"`): pinned from docs but verify on first construction (note in T5). The guard test locks invariants regardless of binding.
- **`claude-agent-sdk` PyPI package name / Python 3.14 wheels** — if install fails in T1, adjust the dependency line and pin a version.
- **agentmemory reader tool names** — the four in `AGENTMEMORY_READERS` are from the spec; if the live server exposes different reader names, the smoke test still works (github citations), but update the constant + guard test together.
