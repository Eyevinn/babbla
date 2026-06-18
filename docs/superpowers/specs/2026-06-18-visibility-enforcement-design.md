# Visibility Enforcement — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-18
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0007 — access/visibility/redaction](../../adr/0007-access-visibility-redaction.md),
[PROPOSAL-design.md — Surfaces & access model](../../PROPOSAL-design.md)

## Why this slice exists

Phase 4 bundles three subsystems — **Visibility**, **Lobby**, and **Subscriptions**. They have a
natural dependency order: visibility is the access rule the Lobby enforces, and subscriptions layer
on top of both. We decompose Phase 4 and build the **Visibility foundation first**: a single, tested
authorization primitive — *"may this asker, on this surface, ask about this project?"* — that the
Lobby slice will later reuse.

### Current state

- `visibility` is a **stored-but-unused** field on `ProjectBinding` (`src/babbla/config.py`). Nothing
  in `orchestrator._resolve` or the agent path enforces it.
- Access is already *implicitly* Slack membership: a Channel Ask works if you're in the channel; a
  DM (Private Ask) works for the single `dm: true` project for any workspace member.
- `public` and `internal` are **indistinguishable on every surface that exists today** — within one
  Slack workspace, a channel member and a DM-er are both "workspace members", so "answerable to
  anyone" and "answerable to any workspace member" collapse to the same behavior. That distinction
  only bites at a future *external* / Lobby edge.

So **`private` is the only tier that introduces an actual access restriction right now.**

### Impact on the current pilot

With today's config (MyTV = `public` + `dm: true`), this slice changes **no observable behavior** —
MyTV stays answerable on every surface. Its value is forward-looking: it makes onboarding the spine's
*private client project* safe, and gives the Lobby a primitive to reuse. We build it ahead of need,
deliberately, as the agreed foundation slice.

## The access rule (decided)

Behavior for a `private` project on a non-channel surface is **points-don't-reveal, surface-based**
(not a Slack membership API check):

> On a project's **own channel** → always allow (membership in the channel *is* the access).
> On any **non-channel** surface (DM, later Lobby) → allow iff visibility is `public`/`internal`;
> if `private`, **deny and point** the asker to the project's channel.

This needs **no Slack `conversations.members` API call**, no membership cache, and no fail-closed
network path. Visibility maps directly to surface type, and the whole policy is testable from config
alone. It matches the proposal's "access = Slack channel membership" stance: the channel *is* the
access surface; DM and Lobby are non-channel surfaces and therefore only ever serve public/internal.

## Architecture

A new standalone policy module, `src/babbla/access.py`, holds the entire visibility policy as pure,
dependency-free functions. The orchestrator calls it as a **pre-flight gate** — after resolving the
project binding, before taking the per-thread lock or invoking the agent runner — so a denied Ask
never spends a model call and never touches the session store. This mirrors the repo's
one-module-one-purpose layout (`read_only.py`, `session_store.py`).

```
slack_adapter (channel | dm)
        │  text, channel_id, is_dm, thread_ts
        ▼
orchestrator.handle_ask
        │  _resolve(channel_id, is_dm) -> ProjectBinding
        │  surface = Surface.DM if is_dm else Surface.CHANNEL
        ▼
access.authorize_ask(binding, surface) -> AccessDecision
        ├─ allowed=False ─► return denial CitedAnswer (pointer text, no citations)
        │                    [runner NOT called, store NOT written]
        └─ allowed=True  ─► existing path: lock -> runner.run_ask -> store session
```

## Components

### `src/babbla/access.py` (new)

```python
class Surface(Enum):
    CHANNEL = "channel"   # a project's bound Slack channel
    DM      = "dm"        # Private Ask (1:1)
    # LOBBY = "lobby"     # added by the Lobby slice

@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None      # why denied (for logs)
    pointer: str | None = None     # user-facing denial text, if denied

def authorize_ask(binding: ProjectBinding, surface: Surface) -> AccessDecision:
    ...
```

Rule, in full:

- `surface == Surface.CHANNEL` → **allow** unconditionally. (The project was resolved *by* its
  `channel_id`; posting there means membership — the channel is the access.)
- otherwise (DM, later Lobby):
  - `binding.visibility in {"public", "internal"}` → **allow**
  - `binding.visibility == "private"` → **deny**, with a pointer naming the project's channel.

`public` and `internal` are handled identically **on purpose**. A code comment records *why*
(single-workspace: every DM-er is a workspace member; the tiers diverge only at a future
external/Lobby edge) so a later reader does not "fix" the apparent redundancy.

### `src/babbla/orchestrator.py` (changed)

- Derive `Surface` from the existing `is_dm` argument — no change to `handle_ask`'s signature or to
  the `slack_adapter` callers: `surface = Surface.DM if is_dm else Surface.CHANNEL`.
- Insert the pre-flight gate after `_resolve`, before `_lock_for`/`run_ask`.
- On denial, return a `CitedAnswer` constructed directly (pointer text, empty citations). The
  "no model call" guarantee holds because the object is built locally, not by the runner.

### `src/babbla/config.py` (changed)

- One `logger.warning` at load time when a project is `private` **and** `dm: true` — that DM surface
  resolves to a project it will always deny (a dead DM surface). **Not** a hard error: visibility can
  be downgraded after `dm: true` was set, and config loading must not crash a running server.

### Unchanged

`slack_adapter.py`, `agent_runner.py`, and the digest subsystem are untouched.

## Data flow & denial behavior

A denial is returned as a `CitedAnswer` whose `text` is the pointer and whose citations are empty.
The `slack_adapter` already renders `answer.text` via `chat_update`, so the adapter needs no changes
and denial handling stays entirely in `access.py` + `orchestrator.py` (not conflated with the
adapter's generic error path).

Pointer text is built from the binding:

- `channel_id` set → `"🔒 *<name>* is private — ask about it in <#channel_id>."`
  (Slack renders `<#C…>` as a clickable channel link.)
- `channel_id is None` (private project, no channel bound yet) → graceful degrade:
  `"🔒 *<name>* is private and has no channel yet — ask once its channel is set up."`
  Still a deny (never reveal), just without a link.

## Error handling

- **Pre-flight, fail-safe:** denial happens before any model call or store write; a denied Ask cannot
  partially execute.
- **No new network path:** the surface-based rule makes no Slack API call, so there is no API-down
  failure mode to fail closed against.
- **Graceful config:** a `private` + `dm: true` misconfiguration logs a warning but loads.
- **Null channel:** handled explicitly (graceful pointer, still denies).

## Testing

Everything is testable from config alone — no Slack API, no model calls. Follows the repo's TDD
culture (pure-unit-friendly; fake runner + fake store for orchestrator tests, as existing tests do).

### `tests/test_access.py` (new) — policy matrix

| `binding.visibility` | `Surface` | expected |
| --- | --- | --- |
| public   | CHANNEL | allow |
| public   | DM      | allow |
| internal | CHANNEL | allow |
| internal | DM      | allow |
| private  | CHANNEL | allow |
| private  | DM      | deny, pointer names channel |
| private (`channel_id=None`) | DM | deny, graceful pointer, no link |

Plus: a `Surface` value round-trip, and an assertion that `public` and `internal` produce identical
decisions (guards the intentional-redundancy comment).

### Orchestrator integration tests (extend existing)

- DM Ask about a `private` project → returns the pointer `CitedAnswer`; **fake runner never called**
  and **store never written** (asserts the pre-flight short-circuit).
- DM Ask about a `public` project → runner *is* called (MyTV regression guard; current behavior
  preserved).
- Channel Ask about a `private` project → runner *is* called (channel = access).

### Config test

- `private` + `dm: true` loads successfully and emits the warning (assert via `caplog`).

## Scope summary

- **New:** `src/babbla/access.py`, `tests/test_access.py`
- **Changed:** `src/babbla/orchestrator.py`, `src/babbla/config.py`
- **Unchanged:** `slack_adapter.py`, `agent_runner.py`, digest subsystem
- **Pilot behavior:** unchanged (MyTV stays answerable everywhere)

## Out of scope (later Phase 4 slices)

- **Lobby** — the open discovery surface that *locates* the relevant project(s) for an unbound
  question; it will add `Surface.LOBBY` and reuse `authorize_ask`.
- **Subscriptions / Topics** — Shared (channel ↔ many projects) and Personal (persisted individual
  interests); ask/digest scoping.
- **Slack membership checks** — explicitly rejected for this slice in favor of surface-based
  points-don't-reveal.
