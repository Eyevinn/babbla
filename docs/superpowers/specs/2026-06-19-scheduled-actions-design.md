# Scheduled Actions Framework — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-19
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Supersedes / folds in:** [Shared Digest Fan-out](2026-06-19-shared-digest-design.md) (the shared
digest becomes one action in this framework)
**Builds on:** [Shared Subscriptions](2026-06-19-shared-subscriptions-design.md) (slice 3),
[Always-on Babbla](2026-06-18-always-on-babbla-design.md) (the digest subsystem)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0008 — release-anchored digests](../../adr/0008-release-anchored-digests.md),
[PROPOSAL-design.md — Digest / Subscription](../../PROPOSAL-design.md)

## Why this slice exists

The scheduler today is a *digest* scheduler. We want more scheduled push actions over time — a weekly
**quiz**, later an "ADR-of-the-week" or a stale-PR nudge. Rather than bolt each onto a digest-shaped
loop, we generalise the scheduler into a small **scheduled-actions framework**: a list of `Action`s, each
fully self-contained, driven by the same heartbeat. The digest becomes the first action; this slice also
delivers the **shared (fan-out) digest** and a **minimal read-only weekly quiz**, proving the abstraction
with three concrete actions.

### What is new vs. reused

- **New:** the `Action` protocol + a generic `ActionScheduler`; a `SharedDigestAction` (portfolio fan-out);
  a `QuizAction` (read-only weekly quiz); supporting stores (`SharedDigestStateStore`, `ActionTimerStore`);
  config for a subscription `digest:` and a project `quiz:`.
- **Reused:** the heartbeat loop + `is_due`/`cadence_bucket`; `current_head` / `changes_between` /
  `changes_since`; the `DigestRunner` → `AgentRunner` read-only path; `SlackPoster`. The existing
  per-project digest *behavior* is preserved exactly — it is refactored into the first `Action` under its
  current tests.

### Decisions made during brainstorming

- **Generalise now.** Build the framework with three real actions (per-project digest, shared digest,
  quiz) rather than a digest-only loop. (Earlier inclination was to defer; the user chose to generalise
  now and include a minimal quiz.)
- **Each action is fully encapsulated** behind `maybe_run(now)` — own cadence/tz, target channel, state,
  generation, and posting. The scheduler imposes no shared state model.
- **Shared digest (carried over, already approved):** per-project anchor reuses each project's own
  `digest.anchor` (default `branch`); subscription `digest: {cadence, tz}` (no anchor); separate
  `SharedDigestStateStore` with per-`(channel, project)` watermark + channel-level `last_digest_at`;
  quiet → no post / no advance; some-shipped → one aggregated post (shippers only) + **advance all**
  resolved watermarks + the channel timer; first agent's binding is the MCP context, facts passed as text.
- **Quiz is minimal and read-only:** post N questions about a project (generated from repo content) to its
  channel; reveal answers as a **threaded reply** so they stay collapsed; **no scoring, no per-user
  state** (scoring would be the first per-user write — deferred to Personal Subscriptions). Timing only.

### Impact when unconfigured

No `digest:`, no subscription `digest:`, and no `quiz:` → the action list is empty and the scheduler runs
an inert tick loop, exactly as today. **Zero behavior change** for the current MyTV pilot until a digest
or quiz is configured.

## The Action abstraction

```python
from typing import Protocol
from datetime import datetime

class Action(Protocol):
    label: str                                   # for logging (e.g. "digest:MyTV", "quiz:MyTV")
    async def maybe_run(self, now: datetime) -> None: ...
```

`maybe_run(now)` internally: checks due-ness (`is_due` against its own stored timestamp), and if due does
the read-only work, posts, and advances its own state. It is a no-op when not due or when there is
nothing to post.

```python
class ActionScheduler:
    def __init__(self, *, actions: tuple[Action, ...], now_fn, interval_s: int = 900) -> None: ...

    async def run(self) -> None:                  # heartbeat (unchanged from DigestScheduler.run)
        while True:
            try:
                await self.tick(self._now_fn())
            except Exception:
                logger.exception("action tick failed")
            await asyncio.sleep(self._interval_s)

    async def tick(self, now: datetime) -> None:
        for action in self._actions:
            try:
                await action.maybe_run(now)
            except Exception:
                logger.exception("action failed: %s", action.label)
```

The per-action `try/except` isolates failures: one action raising never stops the others, and a tick
failure never crashes the process (the `run` loop's `try/except`, unchanged).

## The three actions

### `PerProjectDigestAction` (refactor of today's path)

Wraps `(binding, DigestStateStore, get_json, runner, poster)`. `maybe_run` is today's `_maybe_digest` +
`_emit` logic, verbatim in behavior:

- due-check on `DigestStateStore.get(binding.channel_id).last_digest_at` via `is_due`;
- first run: branch anchor seeds a one-cadence-period window (`changes_since`), deploy anchor is silent;
- `head == watermark` (due but nothing new) → stay quiet, do not advance;
- changes → `runner.summarize(...)` → `poster.post(binding.channel_id, text)` → `advance`.

Posts to `binding.channel_id`. The behavior is preserved exactly and guarded by the existing
`test_digest_scheduler.py` cases, retargeted to drive the action.

### `SharedDigestAction` (portfolio fan-out)

Wraps `(subscription, by_name: dict[str, ProjectBinding], SharedDigestStateStore, get_json, runner,
poster)`. `maybe_run`:

1. `state = store.get(sub.channel_id)`; if `not is_due(now, state.last_digest_at, sub.digest.cadence,
   sub.digest.tz)` → return.
2. For each name in `sub.project_names`: resolve its binding via `by_name` (a name with no binding →
   skip + `logger.warning`); compute its head with `head_for(owner, repo, anchor, deploy_workflow,
   get_json)` where `anchor`/`deploy_workflow` come from `binding.digest` if present else `branch`/`None`;
   skip a project whose head is `None` (no ship signal — do not advance it); else gather its changes vs
   `state.watermarks.get(name)` (bootstrap: absent watermark + branch anchor → one-period window; absent
   + deploy → silent; `head == watermark` → empty; else `changes_between`). Collect `heads: {name: head}`
   and `per_project_changes: {name: [Change, ...]}` (only non-empty).
3. No project has changes → stay quiet (no post, no advance).
4. Some shipped → `runner.summarize_shared(channel_label, per_project_changes)` → one
   `poster.post(sub.channel_id, text)` → `store.advance(sub.channel_id, heads, now.timestamp())` for
   **all** resolved projects (advance-all: quiet projects move forward too; the channel timer advances).

`summarize_shared` runs the agent scoped to the **first** subscribed project's binding for GitHub MCP
context; every project's facts are passed as text (authoritative for a digest). The post groups by
project and includes only shippers.

### `QuizAction` (minimal, read-only)

Wraps `(binding, ActionTimerStore, quiz_runner, poster, cadence, tz, count)`. `maybe_run`:

1. `key = f"quiz:{binding.name}"`; if `not is_due(now, timer.get(key), cadence, tz)` → return.
2. `text = await quiz_runner.generate(binding, count)` — the agent reads the repo (read-only:
   README/`docs/`/ADRs/history) and returns:
   ```
   <questions>
   ===ANSWERS===
   <answers>
   ```
3. Split on the first `===ANSWERS===`. Post the questions to `binding.channel_id` (capture the returned
   `ts`); if an answers section exists, post it as a **threaded reply** (`thread_ts=ts`) so it stays
   collapsed in-channel. Missing delimiter → post the whole text as questions, no thread (graceful).
4. `timer.advance(key, now.timestamp())` after a successful post. No scoring, no per-user state.

## Supporting changes

### `SlackPoster.post` (additive)

```python
async def post(self, channel_id: str, text: str, thread_ts: str | None = None) -> str:
    resp = await self._client.chat_postMessage(
        channel=channel_id, text=text, **({"thread_ts": thread_ts} if thread_ts else {})
    )
    return resp["ts"]
```

Returns the message `ts` and accepts an optional `thread_ts`. Existing digest callers ignore the return —
behavior unchanged.

### `head_for` (extracted in `digest/anchors.py`)

```python
def head_for(owner: str, repo: str, anchor: str, deploy_workflow: str | None, *, get_json) -> str | None
```

The branch/deploy head logic lifted out of `current_head`. `current_head(binding, *, get_json)` becomes a
thin wrapper: `head_for(binding.owner, binding.repo, binding.digest.anchor, binding.digest.deploy_workflow,
get_json=get_json)`. The shared action calls `head_for` directly so a project lacking a `digest:` block
(where `current_head` would dereference `None`) still resolves a branch head.

### `DigestRunner.summarize_shared` (digest/runner.py)

`summarize_shared(channel_label: str, per_project_changes: dict[str, list[Change]]) -> str` — one prompt
grouping facts under each project heading, asking for a single Slack digest spanning the portfolio (lead
with the cross-project headline; group by project; cite SHAs by 7-char prefix and PRs by number as GitHub
links). Runs via `agent.run_ask` scoped to the first project's binding.

### `QuizRunner` (digest/quiz.py)

`generate(binding: ProjectBinding, count: int) -> str` — builds a prompt asking for `count` questions
about the project drawn from its repo, with answers below a literal `===ANSWERS===` delimiter; calls
`agent.run_ask(prompt, binding, None)`; returns the raw text for the action to split.

## State model

### `SharedDigestStateStore` (session_store.py)

```sql
CREATE TABLE IF NOT EXISTS shared_digest_state (
    channel_id     TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (channel_id, project_name)
)
```

```python
@dataclass(frozen=True)
class SharedDigestState:
    watermarks: dict[str, str | None]   # project_name -> stored SHA (absent => not yet seen)
    last_digest_at: float | None        # channel cadence timestamp; None when no rows (first run)

class SharedDigestStateStore:
    async def get(self, channel_id) -> SharedDigestState
    async def advance(self, channel_id, heads: dict[str, str], last_digest_at: float) -> None  # UPSERT one row/project
    def close(self) -> None
```

`advance` writes one row per project with its new head + the shared `last_digest_at`; `get` reads
`last_digest_at` via `MAX(last_digest_at)` (consistent — all rows share it), `None` when no rows. Mirrors
`DigestStateStore`'s `asyncio.to_thread` + `time_fn` + UPSERT shape. No TTL (durable, like
`DigestStateStore`).

### `ActionTimerStore` (session_store.py)

```sql
CREATE TABLE IF NOT EXISTS action_timer (
    action_key     TEXT PRIMARY KEY,
    last_fired_at  REAL NOT NULL
)
```

```python
class ActionTimerStore:
    async def get(self, action_key) -> float | None
    async def advance(self, action_key, last_fired_at: float) -> None   # UPSERT
    def close(self) -> None
```

A minimal "when did this action last fire" store for stateless actions (the quiz). Digests keep their own
`last_digest_at` in their existing stores — unchanged.

## Config model (config.py)

```python
@dataclass(frozen=True)
class SubscriptionDigest:
    cadence: str   # daily | weekly
    tz: str

@dataclass(frozen=True)
class QuizConfig:
    cadence: str   # daily | weekly
    tz: str
    count: int = 3
```

- `Subscription.digest: SubscriptionDigest | None = None`; `Config.digest_subscriptions()` → subscriptions
  with a digest.
- `ProjectBinding.quiz: QuizConfig | None = None`; `Config.quiz_bindings()` → projects with `quiz` **and**
  a non-null `channel_id`.
- Parsing/validation reuses the digest style: `cadence` in `daily|weekly` (off/absent → `None`), valid
  `ZoneInfo` tz; `count` a positive int (default 3) → else `ValueError`. The subscription digest has **no
  anchor** (per-project). Each new field is independently optional and additive.

`channels.yaml`:

```yaml
projects:
  - name: MyTV
    ...
    quiz:
      cadence: weekly
      tz: Europe/Stockholm
      count: 3
subscriptions:
  - channel_id: C900
    projects: [MyTV, Stream]
    digest:
      cadence: weekly
      tz: Europe/Stockholm
```

## Wiring (app.py)

`build_scheduler` builds the stores, assembles the action list, and returns an `ActionScheduler`:

```python
def build_scheduler(*, config, secrets, db_path, client) -> ActionScheduler:
    get_json = make_get_json(secrets.github_token)
    poster = SlackPoster(client)
    digest_runner = DigestRunner(AgentRunner(secrets))
    quiz_runner = QuizRunner(AgentRunner(secrets))
    digest_store = DigestStateStore(db_path)
    shared_store = SharedDigestStateStore(db_path)
    timer_store = ActionTimerStore(db_path)
    by_name = {b.name: b for b in config.bindings}
    actions = []
    for b in config.digest_bindings():
        actions.append(PerProjectDigestAction(b, digest_store, get_json, digest_runner, poster))
    for s in config.digest_subscriptions():
        actions.append(SharedDigestAction(s, by_name, shared_store, get_json, digest_runner, poster))
    for b in config.quiz_bindings():
        actions.append(QuizAction(b, timer_store, quiz_runner, poster, b.quiz.cadence, b.quiz.tz, b.quiz.count))
    return ActionScheduler(actions=tuple(actions), now_fn=_utcnow)
```

Empty `actions` → an inert tick loop (unchanged from today).

## Error handling & edge cases

- **Action isolation** — per-action `try/except` in `tick`; a tick failure never crashes the process.
- **Not due** → the action returns immediately; nothing fetched/posted.
- **Digest quiet** (per-project or all-shared-projects quiet) → no post, no advance; re-checks next tick.
- **Shared: some quiet, some shipped** → one post for shippers; advance-all watermarks + channel timer.
- **Project without `digest:` in a shared digest** → branch anchor; bootstrap window on first run.
- **Deploy-anchored project with no successful run** → head `None` → skipped, not advanced.
- **Subscribed name with no binding** → skipped + warning; the rest proceed.
- **Quiz with no `===ANSWERS===`** → questions only, no thread (graceful).
- **Read-only preserved** — every action's only writes are the Slack post and local SQLite; generation
  runs through the read-only agent path; no repo writes; the quiz collects no answers/scores.

## Testing

All deterministic — fake `get_json`, fake agent runner, fake poster, fixed `now`/`time_fn`, `tmp_path`
stores; no network, no real model calls.

- **`tests/test_action_scheduler.py`** (new): `tick` calls each action's `maybe_run`; one action raising
  doesn't stop the others; empty actions → harmless tick.
- **`tests/test_digest_scheduler.py`** (adapt): existing not-due / bootstrap / quiet / shipped cases
  retargeted to `PerProjectDigestAction`, proving the refactor preserved behavior.
- **`tests/test_digest_shared.py`** (new): not-due; first-run branch bootstrap; all-quiet → no post/no
  advance; some-shipped → one post (shippers only) + advance-all + timer; project without own `digest:`
  defaults to branch; deploy-anchored project uses deploy head; mixed anchors; `None`-head skipped;
  no-binding name skipped + warning.
- **`tests/test_quiz.py`** (new): not-due → nothing; due → posts questions, posts answers as a thread
  reply (asserts `thread_ts` = the questions `ts`), advances timer; missing delimiter → questions only,
  no thread; same-bucket second tick → not due.
- **`tests/test_digest_state_store.py`** (extend): `SharedDigestStateStore` multi-project round-trip,
  consistent `last_digest_at`, unknown channel → empty/None; `ActionTimerStore` get/advance, unknown key
  → None.
- **`tests/test_digest_anchors.py`** (extend): `head_for` branch vs deploy; `current_head` wrapper
  unchanged.
- **`tests/test_digest_runner_poster.py`** (extend): `summarize_shared` prompt + text; `QuizRunner.generate`
  prompt shape + text; `SlackPoster.post` returns `ts` and forwards `thread_ts`.
- **`tests/test_config.py`** (extend): subscription `digest:` and project `quiz:` parse/validate;
  `digest_subscriptions()` / `quiz_bindings()` filters.
- **`tests/test_app.py`** (extend): `build_scheduler` returns an `ActionScheduler` whose `actions` contain
  the expected types for a config with a project digest, a subscription digest, and a quiz.

## Scope summary

- **New:** `Action` + `ActionScheduler` (digest/scheduler.py), `PerProjectDigestAction`,
  `SharedDigestAction`, `QuizAction` (digest/actions.py), `QuizRunner` (digest/quiz.py),
  `SharedDigestStateStore` + `ActionTimerStore` + `SharedDigestState` (session_store.py),
  `SubscriptionDigest` + `QuizConfig` + `digest_subscriptions()` + `quiz_bindings()` (config.py),
  `head_for` (anchors.py), `summarize_shared` (runner.py); `tests/test_action_scheduler.py`,
  `tests/test_digest_shared.py`, `tests/test_quiz.py`.
- **Changed:** `config.py`, `session_store.py`, `digest/{anchors,runner,scheduler,poster}.py`, `app.py`,
  `config/channels.yaml` (document subscription `digest:` and project `quiz:`), `tests/test_digest_scheduler.py`
  (retarget to the action).
- **Unchanged:** `orchestrator.py`, `slack_adapter.py`, `access.py`, `lobby.py`, `subscriptions.py`.
- **Inert when no digest/quiz configured.**

## Out of scope (future)

- **Quiz scoring / per-user state** — collecting answers and tracking scores (the first per-user write;
  belongs with Personal Subscriptions).
- **More action types** — ADR-of-the-week, stale-PR nudge, etc.; trivial to add once the framework lands.
- **Per-Topic scoping; Personal/DM digests** — as before.
- **Summary customisation** (per-digest `audience` field) and **skill-based summary** — deferred (the
  latter needs a headless-SDK skill-loading spike).
