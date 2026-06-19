# Shared Subscriptions — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-19
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Builds on:** [Visibility Enforcement](2026-06-18-visibility-enforcement-design.md) (slice 1),
[Lobby](2026-06-18-lobby-design.md) (slice 2)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0007 — access/visibility/redaction](../../adr/0007-access-visibility-redaction.md),
[ADR 0009 — repo is source of truth for "why"](../../adr/0009-repo-is-source-of-truth-for-why.md),
[PROPOSAL-design.md — Surfaces & access model](../../PROPOSAL-design.md)

## Why this slice exists

This is slice 3 of Phase 4 (Visibility → Lobby → **Subscriptions**), scoped to **Shared
Subscriptions** only. It introduces the domain model's decoupling of *Channel* from *Project*.

Today the relationship is locked **1:1**: each `ProjectBinding` carries its own `channel_id`, and
`Config.for_channel(channel_id)` returns exactly one project. A **Shared Subscription** lets a
Channel point at a **set** of Projects — the "portfolio channel" that watches several related
services at once (e.g. one `#video-pipeline` channel tracking stream-starter + simulcast + MyTV), so
a manager follows the portfolio in one place instead of joining N per-project channels.

### What is new here

- **New:** a `Subscription` config concept (channel_id → project names), and a small "which
  project?" clarification reply.
- **Reused:** the entire Lobby routing layer — `lobby.route(text, catalog, classify_fn)`, the
  catalog (with GitHub descriptions), the injected classifier, and the sticky-per-thread
  `LobbyThreadStore`. A subscription channel routes among a **filtered subset** of the catalog.
  There is **one** routing implementation, not two.

### Decisions made during brainstorming

- **Scope:** Shared Subscriptions only. Personal Subscriptions and Topics are deferred to their own
  later slices.
- **Build ahead of need:** the portfolio-channel need does not exist in today's one-project pilot,
  but we build the foundation now, deliberately, as we did for Visibility and Lobby.
- **Config shape:** a dedicated top-level `subscriptions:` block mapping `channel_id` →
  `[project names]`. `projects:` stays the pure catalog. Fully additive.
- **This slice handles Ask routing only.** Shared Digest fan-out (one aggregated digest per
  subscription channel) is a separate follow-up slice; the per-project digest path is untouched.
- **No-match behavior:** when the classifier cannot confidently pick a project, reply with a
  clarification listing the channel's subscribed projects — never guess, never answer the wrong one.

### Access

A subscription-channel Ask is `Surface.CHANNEL` → **always allow** (the channel *is* the access).
One consequence, called out deliberately: subscribing a project to a channel grants that channel's
members access to it — **including a `private` project**, because channel membership is the access
surface. This is consistent with the established access model (ADR 0007: "access = Slack channel
membership"), not a new hole.

### Impact when unconfigured

With no `subscriptions:` block (the committed template default), `handle_ask` takes its existing
single-project / DM path unchanged — **zero behavior change** for the current MyTV pilot. The
subscription path activates only when a channel is listed in `subscriptions:`. Like slices 1–2, this
is built ahead of need and inert until configured.

## Architecture & request flow

A subscription-channel Ask is the only new path. It lives as a branch at the **top of the existing
`handle_ask`**, so `slack_adapter` needs **no change** — it still dispatches lobby-vs-`handle_ask`;
the orchestrator decides subscription-vs-single internally, where the config lives.

```
handle_ask(text, thread_ts, channel_id, is_dm):
  if not is_dm and (sub := config.subscription_for(channel_id)) is not None:
        → subscription path (below)
  else:
        → existing single-project / DM path (unchanged)
```

Subscription path (reuses the Lobby machinery, scoped to the subscribed subset):

```
entries = subscriptions.entries_for(catalog, sub.project_names)
async with lock(thread_ts):
  try:
    entry = await _resolve_subscription(text, thread_ts, entries)
    if entry is None:
        return CitedAnswer(text=subscriptions.subscription_clarify(entries), session_id=None)
    decision = authorize_ask(entry.binding, Surface.CHANNEL)     # always allow; gate kept for consistency
    await lobby_store.put(thread_ts, entry.binding.name)          # sticky
    resume = await store.get_session(thread_ts)
    answer = await runner.run_ask(text, entry.binding, resume)
    if answer.session_id: await store.put_session(thread_ts, answer.session_id)
    return answer                                                 # no pointer suffix — already home
  finally:
    self._release_lock(thread_ts)
```

`_resolve_subscription(text, thread_ts, entries)`:

1. **Size-1 shortcut** — if the subscription has exactly one project, return it directly with **no
   classifier call** (deterministic, no model spend). A size-1 subscription behaves like today's
   single-project channel.
2. **Sticky hit** — `lobby_store.get(thread_ts)` names a project still present in `entries` → return
   it (skip routing), so a vague follow-up ("why?") stays on the thread's project.
3. **Else route** — `lobby.route(text, entries, classify_fn)` → entry or `None`.

### Key behaviors

- **No-match → clarify, never a wrong answer.** Lists the subscribed project names; no model call,
  nothing persisted. The asker re-asks naming the project (or replies in-thread to set stickiness).
- **No pointer suffix** (unlike Lobby) — the asker is already in the right channel.
- **Sticky re-authorization** every hit, mirroring Lobby. For `CHANNEL` it always allows, so a
  sticky `private` project stays answerable to channel members — correct (channel = access).
- **Concurrency** — the existing per-thread lock serializes messages in a thread, so no
  double-routing race. `thread_ts` is globally unique across channels, so reusing `LobbyThreadStore`
  for both lobby and subscription threads cannot collide.

## Components & files

### New: `src/babbla/subscriptions.py`

Subscription-specific behavioral helpers — pure given their inputs. Kept separate from `lobby.py`
(which stays purely lobby) and independently testable; mirrors the one-module-one-purpose layout.

- `entries_for(catalog, names) -> tuple[CatalogEntry, ...]`: order-preserving filter of the catalog
  to the subscription's project names; silently skips a name absent from the catalog (a name that
  passed config validation but, e.g., is missing from a partially-built catalog).
- `subscription_clarify(entries) -> str`: the "Which project — *MyTV* or *StreamStarter*?"
  clarification text, listing the subscribed project names. Renders sensibly for 1 vs. N entries.

### Changed: `src/babbla/config.py`

```python
@dataclass(frozen=True)
class Subscription:
    channel_id: str
    project_names: tuple[str, ...]
```

- `Config` gains `subscriptions: tuple[Subscription, ...] = ()` and
  `subscription_for(channel_id) -> Subscription | None` (first subscription whose `channel_id`
  matches).
- Parse a top-level `subscriptions:` block (sibling of `projects:` / `lobby_channel_id:`). Absent or
  empty → `subscriptions = ()`.
- **Validation (fail-fast at load, like the existing `dm > 1` check):**
  - Every name under a subscription's `projects:` must resolve to a known `ProjectBinding` → else
    `ValueError` naming the channel and the unknown project.
  - A subscription must list **≥ 1** project and have a non-null `channel_id` → else `ValueError`.
  - A `channel_id` may appear in **at most one** subscription → else `ValueError` (ambiguous).
  - If a subscription `channel_id` equals `lobby_channel_id`, emit a `logger.warning` (the lobby
    dispatch wins, so the subscription would be shadowed) — warning, not error, consistent with the
    config module's "load, don't crash a running server" stance.

### Changed: `src/babbla/orchestrator.py`

- Add the subscription branch at the top of `handle_ask` and the `_resolve_subscription` helper, as
  in the flow above. No change to `handle_ask`'s signature, so `slack_adapter` callers are
  untouched.
- Reuses existing constructor dependencies: `self._catalog`, `self._classify_fn`,
  `self._lobby_store`, `self._store`, `self._runner`.

### Changed: `src/babbla/app.py`

- `build_orchestrator` currently wires the routing machinery (catalog, `classify_fn`,
  `LobbyThreadStore`) **only** when `lobby_channel_id` is set. Generalize the condition: build it
  when `lobby_channel_id` is set **OR** `config.subscriptions` is non-empty — subscriptions need the
  same catalog/classifier/sticky store.
- With neither configured, the inert path stays (empty catalog, no classifier), and `handle_ask`'s
  subscription branch is never entered.

### Changed: `config/channels.yaml`

Document the new top-level `subscriptions:` block with a commented example. No real Slack channel id
is committed — the user's local value stays unstaged per repo convention.

### Unchanged

`slack_adapter.py`, `agent_runner.py`, `access.py`, and the digest subsystem are untouched.

## Error handling & edge cases

- **No-match / classifier failure** → clarification reply (never a wrong answer); no model call,
  nothing persisted. A classifier returning prose instead of a bare name is already handled by
  `lobby.route`'s exact-name mapping (→ `None`).
- **Sticky project no longer in the subscription** (config changed mid-thread) → `_resolve_subscription`
  ignores the stale sticky value (not in `entries`) and re-routes.
- **Size-1 subscription** → answered directly, no classifier call.
- **Private project in a subscription channel** → `Surface.CHANNEL` allows (channel = access);
  answered.
- **Unknown / empty / duplicate subscription config** → `ValueError` at load (fail-fast).
- **Subscriptions configured without a lobby** → routing machinery still wired (app.py condition);
  routing works with no lobby channel.
- **Subscriptions not configured** → branch never entered; existing single-project/DM asks unchanged;
  slice inert.
- **One failed subscription ask never crashes the process** → the adapter's existing `try/except`
  around `process_ask` covers it (the subscription path runs inside `handle_ask`).

## Testing

All deterministic — `classify_fn` and the stores/runner are injected fakes; no network, no real
model calls.

### `tests/test_subscriptions.py` (new)

- `entries_for`: filters and orders the catalog by the given names; a name absent from the catalog is
  silently skipped; empty names → empty result.
- `subscription_clarify`: lists the subscribed project names; renders sensibly for a single entry and
  for multiple entries.

### `tests/test_config.py` (extend)

- `subscriptions:` block parses into `Subscription`s with the right `channel_id` / `project_names`.
- `subscription_for`: hit returns the subscription; miss returns `None`.
- Unknown project name in a subscription → `ValueError`.
- Empty project list → `ValueError`.
- Duplicate `channel_id` across two subscriptions → `ValueError`.
- Absent block → `subscriptions == ()`.
- Subscription `channel_id` equal to `lobby_channel_id` → warning emitted (assert via `caplog`),
  config still loads.

### `tests/test_orchestrator.py` (extend; fake runner + fake stores + fake router)

- Subscription channel, router → project X → `run_ask` called with X's binding; sticky project +
  session both persisted; answer carries **no** pointer suffix.
- Sticky thread hit → router **not** called; `run_ask` resumes with the stored session.
- Router → `None` → clarification reply; `run_ask` **not** called; nothing persisted.
- Size-1 subscription → answered with **no** classifier call (assert the fake classifier was not
  invoked).
- `private` project in a subscription channel → `Surface.CHANNEL` allows → `run_ask` called.
- Non-subscription channel → existing single-project path unchanged (regression guard; router not
  called).

### `tests/test_app.py` (extend)

- `subscriptions` present but no `lobby_channel_id` → catalog / classifier / `LobbyThreadStore` are
  still wired.
- Neither configured → inert (no catalog, no classifier).

## Scope summary

- **New:** `src/babbla/subscriptions.py`, `tests/test_subscriptions.py`
- **Changed:** `config.py` (+`Subscription`, `subscriptions`, `subscription_for`, parsing/validation),
  `orchestrator.py` (+subscription branch in `handle_ask`, `_resolve_subscription`),
  `app.py` (wire routing machinery when lobby **or** subscriptions configured),
  `config/channels.yaml` (documented `subscriptions:` example)
- **Behavior when no `subscriptions:` block:** none (fully inert)

## Out of scope (later Phase 4 slices)

- **Shared Digest fan-out** — one aggregated digest per subscription channel covering all its
  subscribed projects; touches the digest scheduler/runner and its per-project cadence/watermark
  model.
- **Personal Subscriptions** — an individual's persisted interests (a new per-user write-store) +
  management commands + a Personal Digest delivered by DM.
- **Topics** — thematic slices within / across projects (e.g. security changes) narrowing a
  subscription.
- **A `primary` project per subscription** — a designated default to answer ambiguous asks instead of
  clarifying; YAGNI unless the clarify reply proves annoying in practice.
