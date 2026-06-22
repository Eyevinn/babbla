# Configurable model + effort (per-surface) Implementation Plan

> **STATUS: ✅ COMPLETE** — all 8 tasks shipped and merged to `main` (`6edf7bc`,
> commits `b48c263`…`c5ec1eb`). Suite green (437 passed / 1 skipped). The unticked
> step checkboxes below are historical; the work is done.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the Claude Agent SDK tuning knobs (`effort`, `fallback_model`, `max_turns`, `max_budget_usd`) split across two surfaces — an Ask tier and a cheaper classifier tier — and centralize the options build so the three call-sites stop drifting.

**Architecture:** A new `src/babbla/runtime.py` holds a `RuntimeProfile` dataclass, a `tuning_kwargs` applier (the four new optional knobs), a shared `classifier_options` builder (de-duplicating the two tools-less classifiers), and a `load_profiles(env)` resolver. `Secrets` gains `ask` and `classifier` profile fields (with defaults, so the feature stays additive). The Ask runner applies the ask profile; the lobby and personal-intent classifiers apply the classifier profile.

**Tech Stack:** Python 3, `claude-agent-sdk` (`ClaudeAgentOptions`), pytest (`-m "not integration"`), frozen dataclasses.

## Global Constraints

- **Strictly additive / inert until configured.** With no new env vars set, behavior is byte-identical to today. `effort`/`fallback_model`/`max_turns`/`max_budget_usd` default to `None` and are omitted from the options dict so the SDK keeps its own default.
- **`BABBLA_MODEL` stays the shared default** for both `BABBLA_ASK_MODEL` and `BABBLA_CLASSIFIER_MODEL`. Existing `.env` deployments must be untouched.
- **`DEFAULT_MODEL = "claude-opus-4-8"`** lives in `src/babbla/read_only.py` — import it, never re-declare it.
- **Allowed effort values:** exactly `low`, `medium`, `high`, `xhigh`, `max`.
- **`model` keeps its existing path** — `tuning_kwargs` must NOT emit `model` (it is keyed separately at each call-site: `cfg.model` on the Ask path, `profile.model` in `classifier_options`). Emitting it would double-key the constructor.
- **Validation fails at boot**, not at first ask: `load_profiles` raises `RuntimeError` on a bad effort literal, a non-positive-int `MAX_TURNS`, or a non-positive-float `MAX_BUDGET_USD`.
- Test command throughout: `python -m pytest -q -p no:cacheprovider -k "not integration"` (activate `.venv` first: `source .venv/bin/activate`).
- Commit messages end with the repo's two trailer lines (Co-Authored-By + Claude-Session), matching prior commits on this branch.

---

### Task 1: `RuntimeProfile` + `tuning_kwargs`

**Files:**
- Create: `src/babbla/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `DEFAULT_MODEL` from `src/babbla/read_only.py`.
- Produces:
  - `RuntimeProfile(model: str = DEFAULT_MODEL, effort: str | None = None, fallback_model: str | None = None, max_turns: int | None = None, max_budget_usd: float | None = None)` — frozen dataclass.
  - `tuning_kwargs(p: RuntimeProfile) -> dict` — returns the four NEW knobs as `ClaudeAgentOptions` kwargs, omitting any left `None`. Never includes `model`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_runtime.py`:

```python
from babbla.runtime import RuntimeProfile, tuning_kwargs


def test_tuning_kwargs_empty_for_default_profile():
    assert tuning_kwargs(RuntimeProfile()) == {}


def test_tuning_kwargs_omits_none_and_never_includes_model():
    p = RuntimeProfile(model="claude-haiku-4-5", effort="low")
    kw = tuning_kwargs(p)
    assert kw == {"effort": "low"}
    assert "model" not in kw  # model is keyed separately at the call-site


def test_tuning_kwargs_includes_all_set_knobs():
    p = RuntimeProfile(
        model="m", effort="xhigh", fallback_model="claude-opus-4-8",
        max_turns=4, max_budget_usd=1.5,
    )
    assert tuning_kwargs(p) == {
        "effort": "xhigh",
        "fallback_model": "claude-opus-4-8",
        "max_turns": 4,
        "max_budget_usd": 1.5,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.runtime'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/babbla/runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from babbla.read_only import DEFAULT_MODEL


@dataclass(frozen=True)
class RuntimeProfile:
    """Per-surface Claude Agent SDK tuning. `model` keeps its existing
    call-site handling; the four optional knobs are applied via tuning_kwargs
    and omitted when None so the SDK keeps its own default (inert-until-set)."""

    model: str = DEFAULT_MODEL
    effort: str | None = None            # 'low'|'medium'|'high'|'xhigh'|'max'
    fallback_model: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None


def tuning_kwargs(p: RuntimeProfile) -> dict:
    """The four NEW optional knobs as ClaudeAgentOptions kwargs. Omits any knob
    left at None. Never emits `model` (keyed separately at each call-site, so
    emitting it here would double-key the options constructor)."""
    out: dict = {}
    if p.effort is not None:
        out["effort"] = p.effort
    if p.fallback_model is not None:
        out["fallback_model"] = p.fallback_model
    if p.max_turns is not None:
        out["max_turns"] = p.max_turns
    if p.max_budget_usd is not None:
        out["max_budget_usd"] = p.max_budget_usd
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): RuntimeProfile + tuning_kwargs applier

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 2: `classifier_options` (shared tools-less builder)

**Files:**
- Modify: `src/babbla/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `RuntimeProfile`, `tuning_kwargs` (Task 1); `ClaudeAgentOptions` from `claude_agent_sdk`.
- Produces: `classifier_options(p: RuntimeProfile, system_prompt: str) -> ClaudeAgentOptions` — the isolated, tools-less classifier options shared by lobby + personal. Always sets `allowed_tools=[]`, `mcp_servers={}`, `setting_sources=[]`, `model=p.model`, and the profile's tuning knobs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py`:

```python
from babbla.runtime import classifier_options


def test_classifier_options_structural_isolation():
    opts = classifier_options(RuntimeProfile(), "sys prompt")
    assert opts.system_prompt == "sys prompt"
    assert opts.allowed_tools == []     # tools-less
    assert opts.mcp_servers == {}       # no MCP servers
    assert opts.setting_sources == []   # no CLAUDE.md / host settings
    assert opts.model == "claude-opus-4-8"  # DEFAULT_MODEL
    # inert: no tuning knobs set on a default profile
    assert opts.effort is None
    assert opts.max_turns is None


def test_classifier_options_applies_profile_tuning():
    p = RuntimeProfile(model="claude-haiku-4-5", effort="low", max_turns=1)
    opts = classifier_options(p, "sys")
    assert opts.model == "claude-haiku-4-5"
    assert opts.effort == "low"
    assert opts.max_turns == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: FAIL with `ImportError: cannot import name 'classifier_options'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/babbla/runtime.py` — update the top import and append the function:

```python
from claude_agent_sdk import ClaudeAgentOptions
```

(add alongside the existing imports), then:

```python
def classifier_options(p: RuntimeProfile, system_prompt: str) -> ClaudeAgentOptions:
    """The shared tools-less classifier options for lobby routing and personal
    intent. setting_sources=[] isolates the classifier from host/project context
    (without it it loads CLAUDE.md and emits prose instead of a bare name — the
    2026-06-20 routing fix); mcp_servers={} + allowed_tools=[] keep it a pure
    label-emitter."""
    return ClaudeAgentOptions(
        model=p.model,
        system_prompt=system_prompt,
        allowed_tools=[],
        mcp_servers={},
        setting_sources=[],
        **tuning_kwargs(p),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): shared classifier_options builder

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 3: `load_profiles` + env parsing/validation

**Files:**
- Modify: `src/babbla/runtime.py`
- Test: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `RuntimeProfile`, `DEFAULT_MODEL`.
- Produces: `load_profiles(env: Mapping[str, str]) -> tuple[RuntimeProfile, RuntimeProfile]` — returns `(ask, classifier)`. Reads `BABBLA_ASK_*` and `BABBLA_CLASSIFIER_*`; both `*_MODEL` default to `BABBLA_MODEL` (itself defaulting to `DEFAULT_MODEL`). Raises `RuntimeError` on invalid effort / non-positive-int turns / non-positive-float budget.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime.py`:

```python
import pytest

from babbla.runtime import load_profiles


def test_load_profiles_defaults_to_opus_when_nothing_set():
    ask, clf = load_profiles({})
    assert ask.model == "claude-opus-4-8"
    assert clf.model == "claude-opus-4-8"
    assert ask.effort is None and clf.effort is None
    assert ask.max_turns is None and ask.max_budget_usd is None


def test_load_profiles_babbla_model_is_shared_default():
    ask, clf = load_profiles({"BABBLA_MODEL": "claude-sonnet-4-6"})
    assert ask.model == "claude-sonnet-4-6"
    assert clf.model == "claude-sonnet-4-6"


def test_load_profiles_per_surface_overrides():
    env = {
        "BABBLA_MODEL": "claude-opus-4-8",
        "BABBLA_ASK_EFFORT": "xhigh",
        "BABBLA_ASK_MAX_TURNS": "6",
        "BABBLA_ASK_MAX_BUDGET_USD": "2.5",
        "BABBLA_ASK_FALLBACK_MODEL": "claude-opus-4-7",
        "BABBLA_CLASSIFIER_MODEL": "claude-haiku-4-5",
        "BABBLA_CLASSIFIER_EFFORT": "low",
    }
    ask, clf = load_profiles(env)
    assert ask.model == "claude-opus-4-8"
    assert ask.effort == "xhigh"
    assert ask.max_turns == 6
    assert ask.max_budget_usd == 2.5
    assert ask.fallback_model == "claude-opus-4-7"
    assert clf.model == "claude-haiku-4-5"
    assert clf.effort == "low"


def test_load_profiles_rejects_bad_effort():
    with pytest.raises(RuntimeError, match="EFFORT"):
        load_profiles({"BABBLA_ASK_EFFORT": "turbo"})


def test_load_profiles_rejects_non_int_turns():
    with pytest.raises(RuntimeError, match="MAX_TURNS"):
        load_profiles({"BABBLA_ASK_MAX_TURNS": "lots"})


def test_load_profiles_rejects_non_positive_budget():
    with pytest.raises(RuntimeError, match="MAX_BUDGET_USD"):
        load_profiles({"BABBLA_CLASSIFIER_MAX_BUDGET_USD": "0"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: FAIL with `ImportError: cannot import name 'load_profiles'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/babbla/runtime.py` — update the top imports to include `Mapping`, then append:

```python
from typing import Mapping
```

(add alongside the existing imports), then:

```python
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _effort(env: Mapping[str, str], key: str) -> str | None:
    v = env.get(key)
    if not v:
        return None
    if v not in _EFFORTS:
        raise RuntimeError(f"{key}={v!r} must be one of {', '.join(_EFFORTS)}")
    return v


def _pos_int(env: Mapping[str, str], key: str) -> int | None:
    v = env.get(key)
    if not v:
        return None
    try:
        n = int(v)
    except ValueError:
        n = 0
    if n <= 0:
        raise RuntimeError(f"{key}={v!r} must be a positive integer")
    return n


def _pos_float(env: Mapping[str, str], key: str) -> float | None:
    v = env.get(key)
    if not v:
        return None
    try:
        x = float(v)
    except ValueError:
        x = 0.0
    if x <= 0:
        raise RuntimeError(f"{key}={v!r} must be a positive number")
    return x


def _profile(env: Mapping[str, str], prefix: str, *, default_model: str) -> RuntimeProfile:
    p = f"BABBLA_{prefix}_"
    return RuntimeProfile(
        model=env.get(p + "MODEL") or default_model,
        effort=_effort(env, p + "EFFORT"),
        fallback_model=env.get(p + "FALLBACK_MODEL") or None,
        max_turns=_pos_int(env, p + "MAX_TURNS"),
        max_budget_usd=_pos_float(env, p + "MAX_BUDGET_USD"),
    )


def load_profiles(env: Mapping[str, str]) -> tuple[RuntimeProfile, RuntimeProfile]:
    """Resolve (ask, classifier) profiles from env. BABBLA_MODEL is the shared
    default for both surfaces' model; the four tuning knobs are per-surface and
    default to None (the SDK runtime default). Raises RuntimeError on a bad
    value so misconfiguration fails at boot, not at the first ask."""
    base_model = env.get("BABBLA_MODEL") or DEFAULT_MODEL
    return (
        _profile(env, "ASK", default_model=base_model),
        _profile(env, "CLASSIFIER", default_model=base_model),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runtime.py -q`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/runtime.py tests/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): load_profiles env resolver with boot-time validation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 4: `Secrets` profile fields + `load_secrets` wiring

**Files:**
- Modify: `src/babbla/agent_runner.py:7` (imports), `:34-39` (`Secrets`)
- Modify: `src/babbla/app.py:27` (drop unused import), `:42-51` (`load_secrets`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `RuntimeProfile`, `load_profiles` (Task 3).
- Produces:
  - `Secrets` gains `ask: RuntimeProfile` and `classifier: RuntimeProfile` fields, each defaulting to `RuntimeProfile()`; the `model` field is **removed**.
  - `load_secrets(env)` populates them via `load_profiles(env)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (import `load_secrets` and `pytest` as needed at the top if not already imported):

```python
def test_load_secrets_resolves_per_surface_profiles():
    from babbla.app import load_secrets
    env = {
        "SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "g",
        "BABBLA_ASK_EFFORT": "high",
        "BABBLA_CLASSIFIER_MODEL": "claude-haiku-4-5",
    }
    s = load_secrets(env)
    assert s.ask.effort == "high"
    assert s.ask.model == "claude-opus-4-8"          # BABBLA_MODEL default
    assert s.classifier.model == "claude-haiku-4-5"


def test_load_secrets_backcompat_babbla_model():
    from babbla.app import load_secrets
    env = {
        "SLACK_BOT_TOKEN": "x", "SLACK_APP_TOKEN": "y", "GITHUB_TOKEN": "g",
        "BABBLA_MODEL": "claude-sonnet-4-6",
    }
    s = load_secrets(env)
    assert s.ask.model == "claude-sonnet-4-6"
    assert s.classifier.model == "claude-sonnet-4-6"
    assert s.ask.effort is None                      # inert by default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -q`
Expected: FAIL — `TypeError` (Secrets has no `ask`/`classifier`) or `AttributeError`.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/agent_runner.py`, change the dataclass import (line 7) from:

```python
from dataclasses import dataclass
```

to:

```python
from dataclasses import dataclass, field
```

Add the runtime import near the other `babbla` imports (after the `read_only` import block, ~line 18):

```python
from babbla.runtime import RuntimeProfile, tuning_kwargs
```

Replace the `Secrets` dataclass (currently lines 34-39):

```python
@dataclass(frozen=True)
class Secrets:
    github_token: str
    model: str = DEFAULT_MODEL
    github_launcher: str = "docker"
    skills_pool: str = "config/skills"
```

with:

```python
@dataclass(frozen=True)
class Secrets:
    github_token: str
    ask: RuntimeProfile = field(default_factory=RuntimeProfile)
    classifier: RuntimeProfile = field(default_factory=RuntimeProfile)
    github_launcher: str = "docker"
    skills_pool: str = "config/skills"
```

In `src/babbla/app.py`, remove the now-unused import at line 27:

```python
from babbla.read_only import DEFAULT_MODEL
```

and add:

```python
from babbla.runtime import load_profiles
```

Replace `load_secrets` body (lines 46-51) so it builds the profiles:

```python
def load_secrets(env: Mapping[str, str]) -> Secrets:
    missing = [k for k in _REQUIRED if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    ask, classifier = load_profiles(env)
    return Secrets(
        github_token=env["GITHUB_TOKEN"],
        ask=ask,
        classifier=classifier,
        github_launcher=env.get("BABBLA_GITHUB_MCP", "docker"),
        skills_pool=env.get("BABBLA_SKILLS_POOL", "config/skills"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -q`
Expected: PASS (new tests green; existing `test_app.py` tests still pass).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/agent_runner.py src/babbla/app.py tests/test_app.py
git commit -m "$(cat <<'EOF'
feat(config): Secrets carries ask + classifier RuntimeProfiles

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 5: Ask path applies the ask profile

**Files:**
- Modify: `src/babbla/agent_runner.py:95-102` (`build_agent_config` call), `:115-134` (`_base_options`)
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `Secrets.ask` (Task 4), `tuning_kwargs` (Task 1).
- Produces: `_base_options` output now carries the ask profile's tuning knobs; the Ask model comes from `secrets.ask.model`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_runner.py` (it already imports `Secrets` and exercises `AgentRunner`; reuse the existing helpers/fixtures). Add a focused unit test on `_base_options`:

```python
from babbla.runtime import RuntimeProfile


def test_base_options_applies_ask_profile_tuning():
    from babbla.read_only import build_agent_config
    secrets = Secrets(
        github_token="g",
        ask=RuntimeProfile(model="claude-opus-4-8", effort="high", max_turns=5),
    )
    runner = AgentRunner(secrets)
    cfg = build_agent_config(
        owner="o", repo="r", github_token="g", model=secrets.ask.model,
    )
    opts = runner._base_options(cfg, None, None)
    assert opts.effort == "high"
    assert opts.max_turns == 5
    assert opts.model == "claude-opus-4-8"


def test_base_options_inert_for_default_profile():
    from babbla.read_only import build_agent_config
    runner = AgentRunner(Secrets(github_token="g"))  # default profiles
    cfg = build_agent_config(owner="o", repo="r", github_token="g")
    opts = runner._base_options(cfg, None, None)
    assert opts.effort is None
    assert opts.max_turns is None
    assert opts.max_budget_usd is None
    assert opts.fallback_model is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_runner.py::test_base_options_applies_ask_profile_tuning -q`
Expected: FAIL — `opts.effort` is `None` (tuning not applied yet).

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/agent_runner.py`, update the `build_agent_config` call inside `run_ask` (line 99) from:

```python
            model=self._secrets.model,
```

to:

```python
            model=self._secrets.ask.model,
```

In `_base_options`, add the ask profile's tuning knobs to the `params` dict. Change the `params = dict(...)` block (currently lines 124-132) so the final line of the literal includes the applier:

```python
        params = dict(
            model=cfg.model,
            system_prompt=system_prompt or cfg.system_prompt,
            allowed_tools=list(cfg.allowed_tools),
            permission_mode=cfg.permission_mode,
            mcp_servers=cfg.mcp_servers,
            setting_sources=[],
            strict_mcp_config=True,
            **tuning_kwargs(self._secrets.ask),
        )
```

(The trailing `params.update(extra)` line is unchanged; `extra` carries only path-specific structural kwargs — `cwd`/`skills`/`hooks`/`setting_sources` — none of which collide with the four tuning keys.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_runner.py -q`
Expected: PASS (new tests green; existing agent_runner tests still pass).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/agent_runner.py tests/test_agent_runner.py
git commit -m "$(cat <<'EOF'
feat(agent): apply ask-tier tuning to the Ask/digest/quiz/adr path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 6: Classifiers consume the classifier profile

**Files:**
- Modify: `src/babbla/lobby.py:6` (imports), `:68-96` (`make_classify_fn`)
- Modify: `src/babbla/personal.py:6` (imports), `:86-133` (`make_intent_fn`)
- Modify: `src/babbla/app.py:60` and `:72` (factory calls)
- Test: `tests/test_lobby.py:150,169`, `tests/test_personal.py`

**Interfaces:**
- Consumes: `RuntimeProfile`, `classifier_options` (Task 2); `Secrets.classifier` (Task 4).
- Produces: `make_classify_fn(query_fn, profile: RuntimeProfile)` and `make_intent_fn(query_fn, profile: RuntimeProfile)` — both build options via `classifier_options(profile, system_prompt)`.

**Note — intentional behavior change:** `make_intent_fn` currently builds `ClaudeAgentOptions(model=, system_prompt=, allowed_tools=[])` only — it lacks the `mcp_servers={}` + `setting_sources=[]` isolation that the lobby classifier has had since the 2026-06-20 routing fix. Routing through `classifier_options` aligns it to the lobby shape: strictly more isolation on an already tools-less path, and consistent between the two classifiers. A new test pins it.

- [ ] **Step 1: Write the failing test**

In `tests/test_lobby.py`, update the two existing call-sites (lines 150 and 169) from:

```python
    classify = make_classify_fn(fake_query, "claude-x")
```

to:

```python
    from babbla.runtime import RuntimeProfile
    classify = make_classify_fn(fake_query, RuntimeProfile(model="claude-x", effort="low"))
```

and extend `test_classify_fn_isolated_from_project_context` (after the existing asserts) with:

```python
    assert opts.model == "claude-x"
    assert opts.effort == "low"   # classifier-tier tuning is applied
```

In `tests/test_personal.py`, add a test pinning intent-classifier isolation + tuning:

```python
async def test_make_intent_fn_isolated_and_tuned():
    from babbla.personal import make_intent_fn
    from babbla.runtime import RuntimeProfile

    captured = {}

    class _Msg:
        def __init__(self, result):
            self.result = result
            self.session_id = None

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield _Msg("NONE")

    intent = make_intent_fn(fake_query, RuntimeProfile(model="claude-c", effort="low"))
    await intent("hi", ["MyTV"])
    opts = captured["options"]
    assert opts.allowed_tools == []
    assert opts.mcp_servers == {}        # now isolated (was not before)
    assert opts.setting_sources == []    # now isolated (was not before)
    assert opts.model == "claude-c"
    assert opts.effort == "low"
```

(If `test_personal.py` lacks the pytest-asyncio marker convention used by `test_lobby.py`, mirror that file's async-test setup — the repo runs async tests the same way across the suite.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lobby.py tests/test_personal.py -q`
Expected: FAIL — `make_classify_fn`/`make_intent_fn` still expect a `model` string and don't apply `classifier_options` (no `effort`, and intent_fn has no `setting_sources`).

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/lobby.py`, replace the import at line 6:

```python
from claude_agent_sdk import ClaudeAgentOptions
```

with:

```python
from babbla.runtime import RuntimeProfile, classifier_options
```

Change `make_classify_fn` (line 68) signature and options build:

```python
def make_classify_fn(query_fn, profile: RuntimeProfile):
    """Default classifier: a tools-less SDK query that returns exactly a name or NONE."""

    async def classify_fn(text: str, catalog: Sequence[CatalogEntry]) -> str:
        listing = "\n".join(
            f"- {e.binding.name}: {e.description or e.binding.repo}" for e in catalog
        )
        system_prompt = (
            "You route a question to one project. Reply with the EXACT name of the single "
            "best-matching project from the list, or the word NONE if none clearly fits. "
            "Reply with ONLY the name or NONE — no other text.\n\nProjects:\n" + listing
        )
        options = classifier_options(profile, system_prompt)
        reply = ""
        async for message in query_fn(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                reply = captured
        return reply

    return classify_fn
```

In `src/babbla/personal.py`, replace the import at line 6:

```python
from claude_agent_sdk import ClaudeAgentOptions
```

with:

```python
from babbla.runtime import RuntimeProfile, classifier_options
```

Change `make_intent_fn` (line 86) signature, and replace the options construction (lines 131-133) — leave the long `system_prompt` text exactly as-is:

```python
def make_intent_fn(query_fn, profile: RuntimeProfile):
    """Default intent classifier: a tools-less SDK query emitting one command line or NONE."""

    async def intent_fn(text: str, project_names: Sequence[str]) -> str:
        listing = "\n".join(f"- {n}" for n in project_names) or "(none configured)"
        system_prompt = (
            # ... unchanged long prompt text ...
        )
        options = classifier_options(profile, system_prompt)
        reply = ""
        async for message in query_fn(prompt=text, options=options):
            captured = _extract_text(message)
            if captured is not None:
                reply = captured
        return reply

    return intent_fn
```

In `src/babbla/app.py`, change the two factory calls — line 60:

```python
    intent_fn = make_intent_fn(_sdk_query, secrets.classifier)
```

and line 72:

```python
        classify_fn=make_classify_fn(_sdk_query, secrets.classifier),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lobby.py tests/test_personal.py tests/test_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/lobby.py src/babbla/personal.py src/babbla/app.py tests/test_lobby.py tests/test_personal.py
git commit -m "$(cat <<'EOF'
feat(classifiers): route lobby + intent through shared classifier_options

Unifies both classifiers onto classifier_options (classifier-tier tuning) and
brings the personal-intent classifier up to the lobby's setting_sources=[] /
mcp_servers={} isolation.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 7: `babbla-doctor` echoes the resolved tiers

**Files:**
- Modify: `src/babbla/doctor/__main__.py`
- Test: `tests/test_doctor_cli.py`

**Interfaces:**
- Consumes: `load_profiles` (Task 3).
- Produces: `babbla-doctor` prints two `[ok]` lines reporting the resolved Ask and Classifier tiers; a `RuntimeError` from bad tuning env prints a clean error and returns exit 2.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_doctor_cli.py` (mirror the file's existing pattern for invoking `main` with `get_json` and capturing stdout via `capsys`; set env via `monkeypatch`):

```python
def test_doctor_prints_resolved_tiers(monkeypatch, capsys, tmp_path):
    # Minimal config + a get_json stub so check_access passes; mirror the
    # existing doctor CLI tests' fixtures for config_path + get_json.
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("BABBLA_CLASSIFIER_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("BABBLA_ASK_EFFORT", "high")
    # ... set BABBLA_CONFIG to a minimal config fixture as the other tests do ...
    from babbla.doctor.__main__ import main
    rc = main([], get_json=lambda url: {"description": "d"})
    out = capsys.readouterr().out
    assert "Ask tier" in out and "claude-opus-4-8" in out and "effort=high" in out
    assert "Classifier tier" in out and "claude-haiku-4-5" in out


def test_doctor_rejects_bad_effort(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "g")
    monkeypatch.setenv("BABBLA_ASK_EFFORT", "turbo")
    from babbla.doctor.__main__ import main
    rc = main([], get_json=lambda url: {"description": "d"})
    assert rc == 2
    assert "EFFORT" in capsys.readouterr().err
```

(Use the same config-fixture mechanism the other `test_doctor_cli.py` tests use — set `BABBLA_CONFIG` to a temp YAML with one reachable project, or reuse an existing fixture/helper in that file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_doctor_cli.py -q`
Expected: FAIL — no tier lines printed; bad effort raises an uncaught `RuntimeError` instead of returning 2.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/doctor/__main__.py`, add the import:

```python
from babbla.runtime import load_profiles
```

Immediately after the `GITHUB_TOKEN` gate (after the `if not token:` block, before building `get_json`), validate + echo the tiers. Because `load_profiles` raises on bad config, wrap it:

```python
    try:
        ask, classifier = load_profiles(os.environ)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    def _tier(p):
        return (
            f"model={p.model} effort={p.effort or '(default)'} "
            f"fallback={p.fallback_model or '(none)'} "
            f"max_turns={p.max_turns or '(default)'} "
            f"max_budget_usd={p.max_budget_usd or '(default)'}"
        )

    print(f"[ok] Ask tier: {_tier(ask)}")
    print(f"[ok] Classifier tier: {_tier(classifier)}")
```

Place this so it runs regardless of whether `get_json` was injected (i.e. after the token guard but it must not depend on `get_json`). Since the `get_json is None` branch returns early only when the token is missing, put the tier block right after that branch and before the `config_path = ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_doctor_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/doctor/__main__.py tests/test_doctor_cli.py
git commit -m "$(cat <<'EOF'
feat(doctor): echo resolved Ask + Classifier tiers; validate tuning env

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

### Task 8: Documentation + full-suite verification

**Files:**
- Modify: `.env.example`, `docs/DEPLOY.md`, `README.md`

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: documented env-var contract.

- [ ] **Step 1: Document the env vars in `.env.example`**

Append a section (keep the existing `BABBLA_MODEL` line if present; document it as the shared default):

```bash
# --- Model / effort tuning (all optional; inert until set) ---
# BABBLA_MODEL is the shared default model for both surfaces below.
# BABBLA_MODEL=claude-opus-4-8
#
# Ask tier (interactive Asks + digests/quiz/ADR runs):
# BABBLA_ASK_MODEL=claude-opus-4-8
# BABBLA_ASK_EFFORT=high            # low|medium|high|xhigh|max
# BABBLA_ASK_FALLBACK_MODEL=claude-opus-4-7
# BABBLA_ASK_MAX_TURNS=8
# BABBLA_ASK_MAX_BUDGET_USD=2.0
#
# Classifier tier (lobby routing + personal-intent — pure label-emitters):
# BABBLA_CLASSIFIER_MODEL=claude-haiku-4-5
# BABBLA_CLASSIFIER_EFFORT=low
# BABBLA_CLASSIFIER_FALLBACK_MODEL=
# BABBLA_CLASSIFIER_MAX_TURNS=1
# BABBLA_CLASSIFIER_MAX_BUDGET_USD=
```

- [ ] **Step 2: Document in `docs/DEPLOY.md` and `README.md`**

Add a short "Model & effort tuning" subsection to the configuration area of each, stating: the two tiers (Ask vs classifier) and what rides each; that `BABBLA_MODEL` remains the shared default so existing deployments are unchanged; the allowed effort values; and that everything is optional/inert until set. Point readers to `babbla-doctor` to see the resolved tiers. Match the surrounding prose style (sentence-case headings, fenced env examples).

- [ ] **Step 3: Run the full suite (excluding live integration)**

Run: `python -m pytest -q -p no:cacheprovider -k "not integration"`
Expected: all pass (≥ the pre-existing 418 + the new tests), 0 failures.

- [ ] **Step 4: Sanity-check backwards-compat manually**

Run:

```bash
python -c "
from babbla.app import load_secrets
s = load_secrets({'SLACK_BOT_TOKEN':'x','SLACK_APP_TOKEN':'y','GITHUB_TOKEN':'g'})
assert s.ask.model == 'claude-opus-4-8' and s.ask.effort is None
assert s.classifier.model == 'claude-opus-4-8'
print('backcompat OK')
"
```

Expected: `backcompat OK`.

- [ ] **Step 5: Commit**

```bash
git add .env.example docs/DEPLOY.md README.md
git commit -m "$(cat <<'EOF'
docs: document per-surface model/effort env vars

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Uct8FWgyP1gdHabPjJ6UA1
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- `RuntimeProfile` + `tuning_kwargs` (omits None, never model) → Task 1. ✓
- `classifier_options` (de-drips lobby + personal) → Task 2. ✓
- `load_profiles` + per-surface env + BABBLA_MODEL shared default + boot validation → Task 3. ✓
- `Secrets` profiles + `load_secrets` → Task 4. ✓
- Ask path applies ask profile (covers Asks + digest/quiz/adr, which ride `run_ask`) → Task 5. ✓
- Classifiers consume classifier profile → Task 6. ✓
- Guard / back-compat (no env → no tuning knobs) → Tasks 5 (`test_base_options_inert_for_default_profile`) + 2 (`test_classifier_options_structural_isolation`) + 4 (`test_load_secrets_backcompat_babbla_model`) + 8 Step 4. ✓
- Doctor tier echo + validation → Task 7. ✓
- Docs (`.env.example`/`DEPLOY.md`/`README.md`) → Task 8. ✓
- Out-of-scope items (per-project config, thinking/task_budget, global BABBLA_EFFORT) are not implemented. ✓

**Refinement vs spec:** The spec said `Secrets.model` is removed and profiles added; the plan makes the profile fields **default to `RuntimeProfile()`** (rather than required) so existing `Secrets(github_token=...)` constructions across the test suite keep working — this preserves the additive principle at the dataclass level with zero churn to unrelated tests. The only test ripple is `test_lobby.py` (passed a model string), updated in Task 6.

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". The one prose-only step is Task 8 Step 2 (README/DEPLOY wording), which is genuinely documentation copy, not code.

**Type consistency:** `RuntimeProfile`, `tuning_kwargs`, `classifier_options`, `load_profiles` names and signatures are identical across Tasks 1-7. `Secrets.ask` / `Secrets.classifier` referenced consistently. `make_classify_fn(query_fn, profile)` / `make_intent_fn(query_fn, profile)` consistent between Task 6 and `app.py` calls.
