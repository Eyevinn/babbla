# Personal Subscriptions — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-19
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Builds on:** [Shared Subscriptions](2026-06-19-shared-subscriptions-design.md) (slice 3 — routing reused verbatim),
[Scheduled Actions Framework](2026-06-19-scheduled-actions-design.md) (the `Action` / `ActionScheduler` this hooks into),
[Visibility Enforcement](2026-06-18-visibility-enforcement-design.md) (slice 1 — the `Surface.DM` gate),
[Lobby](2026-06-18-lobby-design.md) (slice 2 — the catalog + classifier + sticky `LobbyThreadStore`)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0006 — stateful config](../../adr/0006-stateful-config.md),
[ADR 0007 — access/visibility/redaction](../../adr/0007-access-visibility-redaction.md),
[PROPOSAL-design.md — Surfaces, Subscription, Digest](../../PROPOSAL-design.md)

## Why this slice exists

This is the final open slice of Phase 4. A **Personal Subscription** is an individual's own
persisted set of project interests — distinct from a Shared Subscription (tied to a Channel). From
that set, two personal surfaces follow: a **Personal DM Ask** that routes among *your* projects, and
a **Personal Digest** delivered privately by DM on a cadence you choose.

This is also Babbla's **first user-driven write store** and its first **slash-command** surface.
Both are deliberately sanctioned by the proposal: *"the personal Digest config is the only state
Babbla writes"* — fully consistent with [ADR 0003](../../adr/0003-read-only-by-construction.md)
(read-only *toward the projects*; Babbla still owns its own small session/config state per
[ADR 0006](../../adr/0006-stateful-config.md)).

### What is new here

- **New:** a per-user write store (interests + cadence preference), a per-user digest watermark
  store, an umbrella `/babbla` slash command for self-service management, and a `PersonalDigestAction`.
- **Reused:** the entire routing layer — `lobby.route`, `subscriptions.entries_for`,
  `_resolve_subscription`, the sticky `LobbyThreadStore` — and the digest machinery
  (`is_due`, the anchor helpers, `runner.summarize_shared`). There remains **one** routing
  implementation and **one** digest-aggregation shape across Lobby / Shared / Personal.

### Decisions made during brainstorming

- **Scope:** the full slice — interest store + `/babbla` management + Personal DM Ask routing +
  Personal Digest. (All three, per the build-ahead-of-need posture of slices 1–4.)
- **Management surface:** a **single umbrella `/babbla` slash command** with subcommands
  (`subscribe` / `unsubscribe` / `list` / `digest` / `help`). One Slack manifest entry, namespaced
  (no collision with other workspace apps), trivially extensible. Chosen over four separate slash
  commands (discoverability handled by `/babbla help`) and over an App Home tab (disproportionate).
- **Command vs. Ask separation:** slash commands are a *distinct* Slack event, so DM **messages**
  stay purely Asks — no in-DM keyword parsing, no ambiguity.
- **DM Ask routing:** route among the user's subscribed set (≥2 → classifier+sticky; 1 → direct);
  an **empty set falls back to today's single `dm:true` project**, so the live MyTV pilot DM is
  unchanged until a user subscribes.
- **Cadence:** **per-user**, chosen via `/babbla digest <daily|weekly|off>`. A user who never sets
  it inherits a config **default**. `off` pauses the digest while keeping subscriptions for Asks.
- **Visibility:** personal subscriptions cover **public/internal only**, enforced in three places
  (subscribe-time, ask-time, digest-send-time) so a project that *flips* to private after subscribe
  never leaks to a DM.

### Access (visibility) — three checks, defense in depth

A DM is **not** a channel-membership surface, so unlike a Channel Ask (where membership *is* the
access), a `private` project's content must never reach a DM. The constraint is enforced at every
point where a private project could surface:

1. **Subscribe-time** (`/babbla subscribe X`): refuse if `X` is `private` — the user never gets it
   into their set in the first place.
2. **Ask-time:** every resolved entry still passes `authorize_ask(binding, Surface.DM)`, which
   already denies `private` (slice 1). Catches a project that turned private *after* subscribe.
3. **Digest-send-time:** `PersonalDigestAction` filters the user's set to open-tier bindings before
   summarizing. Same flip-after-subscribe guarantee on the push path.

Checks 2–3 are the real guarantees; check 1 is a friendly fast-fail. All three consult the same
open-tier predicate (extracted from `access.py`, not duplicated).

### Impact when unconfigured

- With **no `personal_digest` block** and **no user having run `/babbla subscribe`**, behavior is
  **identical to today**: DM Asks fall through to the `dm:true` project, no digest fires, no new
  rows are written.
- The `/babbla` handler is always registered but is harmless against an empty store.
- The `PersonalDigestAction` is scheduled **only** when `personal_digest` is configured.

Consistent with slices 1–4: built ahead of need, inert until used.

## Architecture & module layout

Mirrors the established one-module-one-purpose split (`lobby.py`, `subscriptions.py`).

- **New `src/babbla/personal.py`** — pure helpers (command parsing + reply renderers), no I/O,
  unit-testable without Slack.
- **New store classes in `src/babbla/session_store.py`** — `PersonalSubStore` and
  `PersonalDigestStateStore`, following the existing `asyncio.to_thread` + `sqlite3` pattern.
- **New `PersonalDigestAction`** in `src/babbla/digest/actions.py` — one instance registered with
  the existing `ActionScheduler`; fans out over all subscribers.
- **Touch points:** `slack_adapter.py` (a `/babbla` command handler + thread `user_id` into DM
  Asks), `orchestrator.py` (`handle_command` + personal DM-ask routing), `config.py`
  (`PersonalDigestConfig`), `app.py` (wiring), `config/channels.yaml` + the Slack app manifest
  (document `/babbla` and `personal_digest`), `slack_adapter`'s `SlackPoster` (new `open_dm`).

## Data model & stores

Three new tables, all in `session_store.py`.

### `PersonalSubStore` — interests + per-user cadence

```sql
CREATE TABLE IF NOT EXISTS personal_subs (
    user_id      TEXT NOT NULL,
    project_name TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, project_name)
);
CREATE TABLE IF NOT EXISTS personal_prefs (
    user_id  TEXT PRIMARY KEY,
    cadence  TEXT NOT NULL          -- 'daily' | 'weekly' | 'off'
);
```

Methods (all `async`, via `asyncio.to_thread`):

- `add(user_id, project) -> None` — `INSERT OR IGNORE` (idempotent).
- `remove(user_id, project) -> None` — `DELETE` (idempotent).
- `list_for(user_id) -> tuple[str, ...]` — the user's project names (stable order, e.g. by
  `created_at`).
- `all_user_ids() -> tuple[str, ...]` — distinct user ids with ≥1 subscription (digest fan-out).
- `get_cadence(user_id) -> str | None` — `None` when the user has no `personal_prefs` row (caller
  applies the config default).
- `set_cadence(user_id, cadence) -> None` — upsert (`'daily' | 'weekly' | 'off'`).

`cadence='off'` is stored as a real value, not a deleted row: pausing the digest keeps the user's
subscriptions intact for Asks.

### `PersonalDigestStateStore` — per-user-per-project watermark

A near-exact copy of `SharedDigestStateStore`, keyed by `user_id` instead of `channel_id`. Reuses
the existing `SharedDigestState` dataclass (`watermarks: dict[str, str | None]`, `last_digest_at`)
rather than defining a parallel type.

```sql
CREATE TABLE IF NOT EXISTS personal_digest_state (
    user_id        TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (user_id, project_name)
);
```

- `get(user_id) -> SharedDigestState` — `watermarks` map + `last_digest_at = max(...)` across rows.
- `advance(user_id, heads, last_digest_at) -> None` — upsert per project, mirroring
  `SharedDigestStateStore.advance`.

## The `/babbla` command (management)

Slack delivers a slash command as a distinct event (Bolt `@app.command("/babbla")`), carrying
`command["user_id"]` and `command["text"]` (the text after the command). The handler `ack()`s
immediately and replies **ephemerally** (only the invoking user sees it), so it is usable from any
channel or DM without noise.

Parsing is a pure function in `personal.py` — `parse_command(text) -> Command` — unit-testable with
no Slack:

| Input | Action | Ephemeral reply |
| --- | --- | --- |
| `subscribe MyTV` | add to my set | "✅ Subscribed to *MyTV*." |
| `unsubscribe MyTV` | remove from my set | "Unsubscribed from *MyTV*." (idempotent) |
| `list` *(or empty text)* | show my set + cadence | "You follow *MyTV*, *stream-starter*. Digest: *weekly*." |
| `digest daily\|weekly\|off` | set my cadence | "Personal digest set to *daily*." |
| `help` / anything unknown | usage | lists the subcommands |

**`subscribe` validation** (the gate that matters):

1. Project must exist in the catalog → else reply listing available project names (reuses the
   catalog already wired for Lobby / Subs).
2. Project must be **public or internal** → a `private` project is refused:
   *"🔒 *MyTV* is private — personal subscriptions only cover public/internal projects."*
   (visibility check #1; uses the open-tier predicate from `access.py`).

The orchestrator exposes a thin `handle_command(user_id, command) -> str` that maps the parsed
`Command` to store writes and returns the reply text. The adapter only `ack`s and posts that string —
all policy in the orchestrator, all Slack-wire concerns in the adapter, mirroring the Ask path.

## Personal DM Ask routing

The DM Ask path changes from "always the `dm:true` project" to "route among *my* subscribed set",
reusing the Shared Subscriptions machinery verbatim.

**User identity:** the `message` event already carries `event["user"]`. `process_ask` and
`handle_ask` gain a `user_id` parameter (meaningful only when `is_dm=True`; channel/lobby callers
pass `None`).

**Resolution inside `handle_ask` when `is_dm`:**

```
projects = personal_store.list_for(user_id)
entries  = subscriptions.entries_for(catalog, projects)
  len == 0  -> existing dm:true single-project path (unchanged fallback)
  len == 1  -> answer directly, no classifier call
  len >= 2  -> _resolve_subscription(text, thread_ts, entries)   # sticky + lobby.route, reused
```

- **Reuses** `subscriptions.entries_for` and `_resolve_subscription` unchanged — one routing
  implementation across Lobby / Shared / Personal.
- **Sticky:** the per-thread `LobbyThreadStore` applies; a DM thread's `thread_ts` is globally
  unique, so no collision with lobby/subscription threads.
- **Visibility check #2:** each resolved entry passes `authorize_ask(binding, Surface.DM)`, which
  denies `private`. A project that flipped to private after subscribe is refused with the standard
  pointer — no stale-subscription leak. (The set is also filtered defensively, but the gate is the
  guarantee.)
- **No pointer suffix** on the answer — the asker is already in their own DM, "home" (matches
  Shared Subs).
- **Empty-set fallback** preserves today's `dm:true` behavior until a user subscribes.

## Personal Digest action

A new `PersonalDigestAction` in `digest/actions.py`, registered with the existing `ActionScheduler`
(one instance, fanning out over all subscribers). It mirrors `SharedDigestAction`'s
watermark/aggregation logic, keyed by `user_id` and delivered by DM.

```
maybe_run(now):
  for user_id in personal_store.all_user_ids():
      cadence = personal_store.get_cadence(user_id) or config.personal_digest.default_cadence
      if cadence == "off": continue
      state = personal_digest_state.get(user_id)
      if not is_due(now, state.last_digest_at, cadence, config.personal_digest.tz): continue
      projects = personal_store.list_for(user_id)
      bindings = [by_name[p] for p in projects
                  if p in by_name and open_tier(by_name[p])]   # visibility check #3 (send-time)
      ... compute per_project_changes + heads, exactly as SharedDigestAction ...
      if not per_project_changes: continue                     # all quiet -> no DM, no advance
      text = await runner.summarize_shared(context_binding, per_project_changes)
      dm_channel = await poster.open_dm(user_id)                # conversations.open
      await poster.post(dm_channel, text)
      await personal_digest_state.advance(user_id, heads, now.timestamp())
```

- **Reuse:** `is_due` / `cadence_bucket`, the `changes_since` / `changes_between` / `head_for`
  anchor helpers, and `runner.summarize_shared` — all unchanged. A Personal Digest is a Shared
  Digest scoped to one person's set, delivered privately.
- **New mechanic — DM delivery:** `SlackPoster` gains `open_dm(user_id) -> channel_id` (wrapping
  `conversations.open`). `poster.post` already takes a `channel_id`, so once the DM channel is
  resolved, posting is unchanged.
- **Per-user `is_due`:** each user is evaluated against their own cadence bucket, so `daily` and
  `weekly` users on the same tick are handled independently. The config default applies to a user
  who never ran `/babbla digest`.
- **Resilience:** one user's digest failing (e.g. `conversations.open` error) is caught per-user
  inside the loop and logged, never aborting other users or the tick (the scheduler already swallows
  per-action exceptions).

## Config & wiring

### `config.py`

A new optional top-level block, sibling of `subscriptions:` / `lobby_channel_id:`:

```yaml
personal_digest:
  default_cadence: weekly        # daily | weekly
  tz: Europe/Stockholm
```

→ `PersonalDigestConfig(default_cadence: str, tz: str)`; `Config.personal_digest:
PersonalDigestConfig | None = None`.

- Absent → personal digest disabled (action not scheduled), but `/babbla subscribe` and personal
  DM-ask routing still work (they do not depend on the digest block).
- **Validation (fail-fast at load):** `default_cadence ∈ {daily, weekly}` else `ValueError`.

### `app.py`

- Build `PersonalSubStore` + `PersonalDigestStateStore` (cheap SQLite, same db file).
- Register the `/babbla` handler **always** (harmless against an empty store).
- Generalize the routing-machinery condition so the catalog / classifier / `LobbyThreadStore` are
  wired when lobby **or** subscriptions **or** personal subscriptions are in play (personal DM-ask
  routing needs them).
- Schedule `PersonalDigestAction` **only** when `config.personal_digest` is set.

### `config/channels.yaml` + Slack manifest

- Document the `personal_digest:` block with a commented example (no real values committed; the
  user's local config stays unstaged per repo convention).
- Document the `/babbla` slash command and its subcommands; note that adding it requires a Slack app
  manifest entry (`features.slash_commands`) + the `commands` OAuth scope.

### Unchanged

`agent_runner.py`, `access.py` (beyond exposing the open-tier predicate), the per-project and
shared digest paths, and the Lobby/Shared-Subscription ask paths are untouched.

## Error handling & edge cases

- **`/babbla subscribe` unknown project** → ephemeral reply listing available projects; nothing
  written.
- **`/babbla subscribe` private project** → refused with the lock pointer; nothing written.
- **`/babbla` with empty / unknown text** → `list` / `help` respectively; never an error.
- **DM Ask, empty subscription set** → existing `dm:true` single-project path (unchanged).
- **DM Ask, size-1 set** → answered directly, no classifier call.
- **DM Ask, subscribed project flipped to `private`** → `authorize_ask(Surface.DM)` denies it.
- **Digest: user paused (`off`)** → skipped, no DM, no advance.
- **Digest: due but all projects quiet** → no DM, no advance (stay silent).
- **Digest: a subscribed project missing from the catalog or now private** → filtered at send time;
  the rest of the user's digest still goes out.
- **Digest: `conversations.open` / post failure for one user** → caught per-user, logged, other
  users unaffected.
- **Concurrency** → the existing per-thread lock serializes a DM thread's messages; `thread_ts` is
  globally unique so the shared `LobbyThreadStore` cannot collide across surfaces.
- **Unconfigured** → fully inert (see *Impact when unconfigured*).

## Testing

All deterministic — injected fakes (classifier, runner, poster, stores); no network, no real model.

### `tests/test_personal.py` (new)

- `parse_command`: each verb; empty text → `list`; unknown → `help`; case/leading-whitespace
  tolerance; `digest` with/without a valid cadence arg.
- Reply renderers: subscribe/unsubscribe/list (single + multiple + none)/digest-set/help.

### `tests/test_session_store.py` (extend)

- `PersonalSubStore`: `add`/`remove` idempotency; `list_for` order; `all_user_ids` distinctness;
  `get_cadence` default-`None`; `set_cadence` round-trip incl. `off`.
- `PersonalDigestStateStore`: watermark round-trip; `last_digest_at = max(...)`; multi-project.

### `tests/test_orchestrator.py` (extend; fakes)

- `handle_command`: subscribe known → persisted; subscribe unknown → catalog reply, nothing
  written; subscribe private → refused, nothing written; unsubscribe; `digest weekly` → cadence
  persisted; `list`.
- DM ask routing: empty set → `dm:true` fallback (`run_ask` with the dm project); size-1 → direct,
  no classifier; ≥2 → routed; sticky hit → classifier not called; project flipped private → denied
  via `Surface.DM`.

### digest test (`tests/test_action_scheduler.py` or new `tests/test_personal_digest.py`)

- `PersonalDigestAction`: not-due → skip; `off` → skip; due + all quiet → no DM, no advance; due +
  changes → `open_dm` + post + advance; private-at-send-time filtered out (rest still sent);
  one user's `open_dm` raising doesn't abort the others.

### `tests/test_app.py` (extend)

- Personal store + `/babbla` handler wired; routing machinery built when only personal subs are in
  play; `PersonalDigestAction` scheduled only when `personal_digest` is configured.

### `tests/test_config.py` (extend)

- `personal_digest` parses into `PersonalDigestConfig`; absent → `None`; invalid `default_cadence`
  → `ValueError`.

## Scope summary

- **New:** `src/babbla/personal.py`, `tests/test_personal.py`; `PersonalSubStore` +
  `PersonalDigestStateStore` (in `session_store.py`); `PersonalDigestAction` (in
  `digest/actions.py`); the `/babbla` command handler; `SlackPoster.open_dm`.
- **Changed:** `orchestrator.py` (`handle_command`, personal DM-ask routing),
  `config.py` (`PersonalDigestConfig`, parse/validate), `app.py` (wiring + generalized routing
  condition + conditional digest action), `slack_adapter.py` (`/babbla` handler + `user_id`
  threading), `access.py` (expose the open-tier predicate),
  `config/channels.yaml` + Slack manifest (document `/babbla` + `personal_digest`).
- **Behavior when unconfigured / unused:** none (fully inert).

## Out of scope (deferred)

- **Topics** — thematic slices narrowing a subscription (fuzziest Phase 4 item; revisit on real
  need).
- **Per-user timezone** — global `tz` from the `personal_digest` block only; no per-user tz.
- **Subscribing to private projects** — disallowed by construction (the DM is not a membership
  surface).
- **Digest content customization** (per-user audience/length) — reuses `summarize_shared` as-is.
- **An App Home tab UI** — the `/babbla` command covers management; revisit if discoverability
  proves insufficient.
