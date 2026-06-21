# Configurable model + effort (per-surface) — design

**Date:** 2026-06-21
**Phase:** Roadmap Phase 6, second bullet ("Configurable model + effort").
**Status:** Approved design, ready for an implementation plan.

## Why

Babbla exposes exactly one runtime knob today — `BABBLA_MODEL` (default
`claude-opus-4-8`) — which flows unchanged to all three Claude Agent SDK
call-sites. Effort, fallback model, turn cap, and budget cap are never set, so
every surface runs at the SDK/CLI runtime default. The two classifiers (lobby
routing and personal-intent) are pure label-emitters — they return a project
name or `NONE` — and have no need of a strong model or high effort, yet they run
at the same tier as a full Ask. This phase exposes the SDK's tuning knobs,
splits configuration by surface (Ask vs classifier), and centralizes the
options build so the three call-sites stop drifting (they currently each
construct `ClaudeAgentOptions` independently).

This operationalizes the second half of [ADR 0002](../../adr/0002-runtime-agnostic-via-mcp.md);
the pluggable-runtime first bullet of Phase 6 is **not** in scope here.

## Guiding principle: strictly additive, inert until configured

With **no new environment variables set, behavior is byte-identical to today.**
The feature adds capability; it forces nothing. This matches Babbla's
established discipline (the committed `channels.yaml` NULL template, ADR-driven
inert features). A guard test pins this: with a clean env, the resulting
`ClaudeAgentOptions` for both surfaces carry none of the new knobs.

## The SDK surface (verified)

`claude_agent_sdk.ClaudeAgentOptions` (installed 0.2.104; container ships
0.2.106) exposes these tuning fields, all defaulting to `None`:

| Field | Type | In scope? |
| --- | --- | --- |
| `model` | `str \| None` | yes (already wired via `BABBLA_MODEL`) |
| `effort` | `Literal['low','medium','high','xhigh','max'] \| None` | **yes** (headline gap) |
| `fallback_model` | `str \| None` | **yes** |
| `max_turns` | `int \| None` | **yes** |
| `max_budget_usd` | `float \| None` | **yes** |
| `thinking` | union `\| None` | no (YAGNI) |
| `max_thinking_tokens` | `int \| None` | no (YAGNI) |
| `task_budget` | `TaskBudget \| None` | no (YAGNI) |

Setting a knob to `None` is equivalent to omitting it (the SDK default), but the
applier **omits** `None` keys rather than passing them, so the constructed
options dict stays minimal and the guard test can assert exact backwards-compat.

## Surfaces and which profile each uses

There are exactly two tiers:

- **Ask profile** — the interactive Ask runner (`AgentRunner.run_ask`) **and
  everything that rides it**: Asks (DM + channel + lobby-routed), and the
  scheduled digest / quiz / ADR runs (`digest/runner.py`, `digest/quiz.py`,
  `digest/adr.py` all call `run_ask`). These are Q&A or content generation and
  warrant the stronger tier.
- **Classifier profile** — the lobby router classifier
  (`lobby.make_classify_fn`) and the personal-intent classifier
  (`personal.make_intent_fn`). Both are tools-less, MCP-less, single-shot
  label-emitters.

## Components

### 1. `RuntimeProfile` + shared applier — new `src/babbla/runtime.py`

```python
from dataclasses import dataclass
from claude_agent_sdk import ClaudeAgentOptions
from babbla.read_only import DEFAULT_MODEL

@dataclass(frozen=True)
class RuntimeProfile:
    model: str = DEFAULT_MODEL
    effort: str | None = None            # 'low'|'medium'|'high'|'xhigh'|'max'
    fallback_model: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None

def tuning_kwargs(p: RuntimeProfile) -> dict:
    """The four NEW optional knobs as ClaudeAgentOptions kwargs.

    Omits any knob left at None so the SDK keeps its own default — this is what
    makes the feature inert-until-configured and keeps the guard test exact.
    `model` is NOT emitted here: it stays on its existing call-site-specific
    path (cfg.model on the Ask path; profile.model on the classifier path) so it
    is never double-keyed into the options constructor.
    """
    out = {}
    if p.effort is not None:
        out["effort"] = p.effort
    if p.fallback_model is not None:
        out["fallback_model"] = p.fallback_model
    if p.max_turns is not None:
        out["max_turns"] = p.max_turns
    if p.max_budget_usd is not None:
        out["max_budget_usd"] = p.max_budget_usd
    return out

def classifier_options(p: RuntimeProfile, system_prompt: str) -> ClaudeAgentOptions:
    """The shared tools-less classifier options, de-duplicated from lobby +
    personal. setting_sources=[] isolates the classifier from host/project
    context (without it the classifier loads CLAUDE.md and starts emitting prose
    instead of a bare name — the 2026-06-20 routing fix)."""
    return ClaudeAgentOptions(
        model=p.model,
        system_prompt=system_prompt,
        allowed_tools=[],
        mcp_servers={},
        setting_sources=[],
        **tuning_kwargs(p),
    )
```

`model` deliberately keeps its existing handling: it is the one knob already
wired, and the classifier's `model`/`setting_sources` handling is load-bearing
(the routing-fragility history). `tuning_kwargs` therefore only adds the four
*new* knobs.

### 2. Two profiles on `Secrets`, resolved in `load_secrets`

`Secrets` (in `agent_runner.py`) gains two fields:

```python
@dataclass(frozen=True)
class Secrets:
    github_token: str
    ask: RuntimeProfile
    classifier: RuntimeProfile
    github_launcher: str = "docker"
    skills_pool: str = "config/skills"
```

The existing `Secrets.model` field is **removed**; the model now lives on each
profile (`secrets.ask.model`, `secrets.classifier.model`).

`load_secrets(env)` (in `app.py`) resolves both profiles:

```python
base_model = env.get("BABBLA_MODEL", DEFAULT_MODEL)
ask        = _profile(env, "ASK",        default_model=base_model)
classifier = _profile(env, "CLASSIFIER", default_model=base_model)
```

where `_profile` reads `BABBLA_<PREFIX>_{MODEL,EFFORT,FALLBACK_MODEL,MAX_TURNS,MAX_BUDGET_USD}`.

Env-var contract:

| Var | Default | Notes |
| --- | --- | --- |
| `BABBLA_MODEL` | `claude-opus-4-8` | **Back-compat**: shared default for both `*_MODEL` vars. Unchanged for existing `.env`. |
| `BABBLA_ASK_MODEL` | `BABBLA_MODEL` | |
| `BABBLA_ASK_EFFORT` | unset → SDK default | one of `low/medium/high/xhigh/max` |
| `BABBLA_ASK_FALLBACK_MODEL` | unset | |
| `BABBLA_ASK_MAX_TURNS` | unset | int |
| `BABBLA_ASK_MAX_BUDGET_USD` | unset | float |
| `BABBLA_CLASSIFIER_MODEL` | `BABBLA_MODEL` | set to e.g. `claude-haiku-4-5` for a cheap classifier |
| `BABBLA_CLASSIFIER_EFFORT` | unset | set to `low` for cheap classification |
| `BABBLA_CLASSIFIER_FALLBACK_MODEL` | unset | |
| `BABBLA_CLASSIFIER_MAX_TURNS` | unset | int |
| `BABBLA_CLASSIFIER_MAX_BUDGET_USD` | unset | float |

**Validation at boot.** `_profile` validates each value and raises
`RuntimeError` (the same failure mode as missing required env, surfaced by
`load_secrets`) on:
- an `EFFORT` not in the allowed literal set,
- a `MAX_TURNS` that is not a positive integer,
- a `MAX_BUDGET_USD` that is not a positive float.

Misconfiguration fails the process at startup / `babbla-doctor`, never at the
first ask.

### 3. Three call-sites consume the profiles

- **`agent_runner.py`**
  - `build_agent_config(..., model=self._secrets.ask.model, ...)` (was
    `self._secrets.model`).
  - `_base_options(...)` adds `**tuning_kwargs(self._secrets.ask)` to its params
    dict, alongside the existing `model=cfg.model`. The new knobs ride through
    `params.update(extra)` cleanly; the read-only confinement
    (`setting_sources=[]`, `strict_mcp_config`, hooks) is unchanged.
- **`lobby.py`** — `make_classify_fn(query_fn, profile)` (signature changes from
  `model: str` to `profile: RuntimeProfile`); builds options via
  `classifier_options(profile, system_prompt)`.
- **`personal.py`** — `make_intent_fn(query_fn, profile)` likewise via
  `classifier_options`.
- **`app.py`** — `make_intent_fn(_sdk_query, secrets.classifier)` and
  `make_classify_fn(_sdk_query, secrets.classifier)`.

### 4. Doctor / preflight

`babbla-doctor` (and the boot preflight) echoes the resolved tiers for
visibility, e.g.:

```
Ask tier:        model=claude-opus-4-8 effort=high
Classifier tier: model=claude-haiku-4-5 effort=low
```

This is read-only reporting; the parsing/validation already happens in
`load_secrets`.

### 5. Documentation

Add the new env vars to `.env.example`, `DEPLOY.md`, and the README's
configuration section, framed as optional tuning with the back-compat note that
`BABBLA_MODEL` still applies to both surfaces.

## Testing (TDD)

- **`tuning_kwargs`**: omits `None` knobs; includes set values with correct keys.
- **`classifier_options`**: carries the structural kwargs
  (`allowed_tools=[]`, `mcp_servers={}`, `setting_sources=[]`) plus the
  profile's model and tuning knobs.
- **`load_secrets`**: per-surface env resolution; `BABBLA_MODEL` fallback for
  both `*_MODEL`; `RuntimeError` on invalid effort / non-int turns /
  non-float budget.
- **Guard / back-compat**: with a clean env (only the required vars), the Ask
  `_base_options` output and the classifier options carry **none** of
  `effort`/`fallback_model`/`max_turns`/`max_budget_usd`.
- **Wiring**: `make_classify_fn` / `make_intent_fn` apply the classifier
  profile; `_base_options` applies the ask profile.

## Out of scope (YAGNI)

- Per-project model/effort in `channels.yaml` (complicates the NULL-template
  discipline; no current need).
- Exposing `thinking`, `max_thinking_tokens`, or `task_budget`.
- A global `BABBLA_EFFORT` (per-surface vars cover it).
- The pluggable-runtime first bullet of Phase 6 (Claude → Copilot/Codex).

## Files touched

- `src/babbla/runtime.py` (new): `RuntimeProfile`, `tuning_kwargs`,
  `classifier_options`.
- `src/babbla/agent_runner.py`: `Secrets` profiles; `_base_options` +
  `build_agent_config` model source.
- `src/babbla/lobby.py`, `src/babbla/personal.py`: factory signatures +
  `classifier_options`.
- `src/babbla/app.py`: `load_secrets` builds profiles; pass `secrets.classifier`
  to the factories.
- `src/babbla/doctor/…`: resolved-tier echo.
- `.env.example`, `DEPLOY.md`, `README.md`: document new vars.
- Tests for each component above.
