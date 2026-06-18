# Visibility Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `visibility` tier actually enforce access — a `private` project is answerable only on its own channel; DM (and later the Lobby) deny-and-point.

**Architecture:** A new pure-function policy module (`src/babbla/access.py`) exposes `authorize_ask(binding, surface) -> AccessDecision`. The orchestrator calls it as a pre-flight gate after resolving the binding and before locking/invoking the runner, so a denied Ask spends no model call and writes no session. Denials return a `CitedAnswer` whose text points the asker to the project's channel.

**Tech Stack:** Python 3, `dataclasses`, `enum`, `pytest` (async tests already configured), stdlib `logging`.

## Global Constraints

- Read-only by construction: the gate must run **before** `runner.run_ask` and **before** any session-store write (ADR 0003).
- Surface-based points-don't-reveal only — **no Slack API / membership call** is permitted in this slice.
- `public` and `internal` must produce **identical** decisions on every surface (single-workspace equivalence); preserve the explanatory comment so it is not "fixed" away.
- No behavior change for the current pilot config (MyTV = `public` + `dm: true` stays answerable everywhere).
- `ProjectBinding` positional order is `(name, owner, repo, visibility, channel_id, dm, digest=None)`.
- `CitedAnswer` shape is `CitedAnswer(text: str, session_id: str | None)`.
- Module loggers use `logger = logging.getLogger(__name__)`.

---

## File Structure

- **Create** `src/babbla/access.py` — the visibility policy: `Surface` enum, `AccessDecision` dataclass, `authorize_ask`. One responsibility: decide whether an Ask is permitted and, if not, produce the pointer text.
- **Create** `tests/test_access.py` — exhaustive policy-matrix unit tests (pure, no I/O).
- **Modify** `src/babbla/orchestrator.py` — derive `Surface`, insert the pre-flight gate, build the denial `CitedAnswer`.
- **Modify** `tests/test_orchestrator.py` — integration tests for deny/allow short-circuit.
- **Modify** `src/babbla/config.py` — one `logger.warning` for `private` + `dm: true`.
- **Modify** `tests/test_config.py` — assert the warning fires (and does not crash load).

---

### Task 1: Access policy module

**Files:**
- Create: `src/babbla/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `ProjectBinding` from `babbla.config` (fields `name`, `visibility`, `channel_id`).
- Produces:
  - `class Surface(Enum)` with members `CHANNEL = "channel"`, `DM = "dm"`.
  - `@dataclass(frozen=True) class AccessDecision` with `allowed: bool`, `reason: str | None = None`, `pointer: str | None = None`.
  - `def authorize_ask(binding: ProjectBinding, surface: Surface) -> AccessDecision`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_access.py
import pytest

from babbla.access import AccessDecision, Surface, authorize_ask
from babbla.config import ProjectBinding


def _binding(visibility="public", channel_id="C123"):
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", visibility, channel_id, True)


@pytest.mark.parametrize("visibility", ["public", "internal", "private"])
def test_channel_surface_always_allows(visibility):
    d = authorize_ask(_binding(visibility), Surface.CHANNEL)
    assert d.allowed is True
    assert d.pointer is None


@pytest.mark.parametrize("visibility", ["public", "internal"])
def test_dm_allows_public_and_internal(visibility):
    assert authorize_ask(_binding(visibility), Surface.DM).allowed is True


def test_dm_denies_private_and_points_to_channel():
    d = authorize_ask(_binding("private", "C123"), Surface.DM)
    assert d.allowed is False
    assert d.reason is not None
    assert "<#C123>" in d.pointer
    assert "MyTV" in d.pointer


def test_dm_denies_private_without_channel_gracefully():
    d = authorize_ask(_binding("private", None), Surface.DM)
    assert d.allowed is False
    assert "<#" not in d.pointer  # no broken channel link
    assert "MyTV" in d.pointer


def test_public_and_internal_decisions_are_identical():
    # Single-workspace: every DM-er is a workspace member, so the tiers must
    # not diverge. Guards the intentional-redundancy comment in access.py.
    pub = authorize_ask(_binding("public"), Surface.DM)
    intern = authorize_ask(_binding("internal"), Surface.DM)
    assert pub == intern


def test_surface_value_roundtrip():
    assert Surface("dm") is Surface.DM
    assert Surface.CHANNEL.value == "channel"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_access.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.access'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/babbla/access.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from babbla.config import ProjectBinding

_OPEN_TIERS = {"public", "internal"}


class Surface(Enum):
    CHANNEL = "channel"  # a project's bound Slack channel
    DM = "dm"            # Private Ask (1:1)
    # LOBBY = "lobby"    # added by the Lobby slice


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None      # why denied (for logs)
    pointer: str | None = None     # user-facing denial text, if denied


def _pointer(binding: ProjectBinding) -> str:
    if binding.channel_id:
        return f"🔒 *{binding.name}* is private — ask about it in <#{binding.channel_id}>."
    return (
        f"🔒 *{binding.name}* is private and has no channel yet — "
        "ask once its channel is set up."
    )


def authorize_ask(binding: ProjectBinding, surface: Surface) -> AccessDecision:
    # On a project's own channel, membership in the channel IS the access.
    if surface is Surface.CHANNEL:
        return AccessDecision(allowed=True)
    # Non-channel surfaces (DM, later Lobby). `public` and `internal` are
    # handled identically ON PURPOSE: in a single Slack workspace every DM-er
    # is a workspace member, so the tiers only diverge at a future external /
    # Lobby edge. Do not "simplify" by dropping one tier.
    if binding.visibility in _OPEN_TIERS:
        return AccessDecision(allowed=True)
    return AccessDecision(
        allowed=False,
        reason=f"{binding.name} is private; {surface.value} is a non-channel surface",
        pointer=_pointer(binding),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_access.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/access.py tests/test_access.py
git commit -m "feat: visibility access policy (authorize_ask)"
```

---

### Task 2: Orchestrator pre-flight gate

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Surface`, `authorize_ask` from `babbla.access`; `CitedAnswer` (already imported).
- Produces: no signature change to `handle_ask(*, text, thread_ts, channel_id, is_dm)`; a denied Ask returns `CitedAnswer(text=<pointer>, session_id=None)` without calling the runner or store.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
PRIVATE_BINDING = ProjectBinding("Secret", "Wkkkkk", "Secret", "private", "C777", True)
PRIVATE_CONFIG = Config(bindings=(PRIVATE_BINDING,))


async def test_dm_about_private_denies_without_runner_or_store(store):
    runner = FakeRunner()
    orch = Orchestrator(PRIVATE_CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tp", channel_id="D999", is_dm=True)
    assert "<#C777>" in ans.text          # points to the channel
    assert ans.session_id is None
    assert runner.calls == []             # runner never invoked
    assert await store.get_session("tp") is None  # nothing written


async def test_channel_about_private_calls_runner(store):
    runner = FakeRunner()
    orch = Orchestrator(PRIVATE_CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tc", channel_id="C777", is_dm=False)
    assert ans.text == "answer to q"      # channel = access; answered normally
    assert runner.calls[0][1].name == "Secret"


async def test_dm_about_public_still_calls_runner(store):
    # MyTV regression guard: public DM behavior unchanged.
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store)
    ans = await orch.handle_ask(text="q", thread_ts="tx", channel_id="D999", is_dm=True)
    assert ans.text == "answer to q"
    assert runner.calls[0][1].name == "MyTV"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -k "private or public_still" -v`
Expected: FAIL — `test_dm_about_private_denies_without_runner_or_store` fails because the runner is currently called and a session is written.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/orchestrator.py`, add the import near the top:

```python
from babbla.access import Surface, authorize_ask
```

Replace the body of `handle_ask` (currently after `binding = self._resolve(...)`) so the gate runs first:

```python
    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool
    ) -> CitedAnswer:
        binding = self._resolve(channel_id, is_dm)
        surface = Surface.DM if is_dm else Surface.CHANNEL
        decision = authorize_ask(binding, surface)
        if not decision.allowed:
            # Pre-flight deny: no model call, no session write.
            return CitedAnswer(text=decision.pointer, session_id=None)
        try:
            async with self._lock_for(thread_ts):
                resume_session_id = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, binding, resume_session_id)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
            return answer
        finally:
            self._release_lock(thread_ts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS — new tests pass and all pre-existing orchestrator tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: enforce visibility as pre-flight gate in orchestrator"
```

---

### Task 3: Config warning for `private` + `dm: true`

**Files:**
- Modify: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `load_config(path)` and parsed `bindings`.
- Produces: a `logging.WARNING` on `babbla.config` when any binding is `visibility == "private"` and `dm is True`; load still succeeds (no exception).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` (write the YAML to a tmp file and load it):

```python
import logging


def test_private_dm_logs_warning_but_loads(tmp_path, caplog):
    from babbla.config import load_config

    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n"
        "  - name: Secret\n"
        "    owner: Wkkkkk\n"
        "    repo: Secret\n"
        "    visibility: private\n"
        "    channel_id: C777\n"
        "    dm: true\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="babbla.config"):
        config = load_config(cfg)
    assert config.bindings[0].name == "Secret"     # load succeeded
    assert any("private" in r.message and "dm" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_private_dm_logs_warning_but_loads -v`
Expected: FAIL — no warning recorded.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/config.py`, add at the top (after `import os`):

```python
import logging

logger = logging.getLogger(__name__)
```

In `load_config`, after the bindings tuple is built and before the `dm` count check, add:

```python
    for b in bindings:
        if b.visibility == "private" and b.dm:
            logger.warning(
                "channels.yaml: project %r is private with dm: true — its DM surface "
                "will always deny and point to the channel (a dead DM surface).",
                b.name,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py::test_private_dm_logs_warning_but_loads -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/config.py tests/test_config.py
git commit -m "feat: warn on private + dm:true misconfiguration at load"
```

---

### Task 4: Full suite regression check

**Files:** none (verification only).

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS — all pre-existing tests plus the new `test_access.py` cases and the added orchestrator/config tests. No behavior change for the MyTV pilot config.

- [ ] **Step 2: Mark the roadmap (optional, if approved)**

Leave Phase 4's checkbox open — only the Visibility slice is done. Note progress in the PR description rather than checking the box, since Lobby + Subscriptions remain.

---

## Self-Review

**1. Spec coverage**
- Access rule (channel-allow / non-channel private-deny) → Task 1 `authorize_ask`.
- Pre-flight gate, no model call / no store write → Task 2 + its assertions.
- Denial as `CitedAnswer` with pointer, adapter untouched → Task 2.
- Null-channel graceful pointer → Task 1 test `test_dm_denies_private_without_channel_gracefully`.
- `public`==`internal` identical + comment guard → Task 1 test + comment.
- `private`+`dm:true` warns, loads → Task 3.
- Pilot behavior unchanged → Task 2 `test_dm_about_public_still_calls_runner`, Task 4 full suite.
- `Surface.LOBBY` left as a commented stub → Task 1 (out of scope, documented).

**2. Placeholder scan:** none — every code/test step shows complete content.

**3. Type consistency:** `Surface`, `AccessDecision(allowed/reason/pointer)`, `authorize_ask(binding, surface)`, `CitedAnswer(text, session_id)`, and `ProjectBinding` positional order are consistent across all tasks.
