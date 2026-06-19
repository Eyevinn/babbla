# Shared Digest Fan-out — Design

> **Folded into [Scheduled Actions Framework](2026-06-19-scheduled-actions-design.md) (2026-06-19).**
> During brainstorming the scope grew: rather than a digest-only scheduler, the shared digest became one
> action in a general scheduled-actions framework (alongside the per-project digest and a new quiz). All
> shared-digest decisions here (per-project anchor, `SubscriptionDigest`, `SharedDigestStateStore`,
> `head_for`, `summarize_shared`, advance-all) carry over verbatim into that spec, which is the one to
> implement. This document is retained for the shared-digest design rationale.

**Status:** Superseded by the Scheduled Actions Framework spec (decisions carried over)
**Date:** 2026-06-19
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Builds on:** [Shared Subscriptions](2026-06-19-shared-subscriptions-design.md) (slice 3),
[Always-on Babbla](2026-06-18-always-on-babbla-design.md) (the digest subsystem)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0008 — release-anchored digests](../../adr/0008-release-anchored-digests.md),
[PROPOSAL-design.md — Digest / Subscription](../../PROPOSAL-design.md)

## Why this slice exists

This is the second half of the portfolio channel. [Shared Subscriptions](2026-06-19-shared-subscriptions-design.md)
gave a subscription channel routed **Asks**; its **Digest** is still per-project — each project posts to
its own `channel_id` via the existing scheduler. This slice makes a subscription channel receive **one
scheduled digest aggregating all its subscribed projects**: "what shipped across the portfolio this
week."

### The core problem

The existing `DigestStateStore` is keyed by `channel_id` with a *single* `watermark_sha` — structurally
one project per channel. A portfolio channel needs **N watermarks** (one per subscribed project) but a
**single channel-level cadence timer**. This slice adds that state shape without disturbing the
per-project one.

### What is new vs. reused

- **New:** a per-subscription digest cadence (`digest: {cadence, tz}` on a `subscriptions:` entry); a
  per-`(channel, project)` watermark store; an aggregated multi-project summary; a scheduler pass over
  digest-enabled subscriptions.
- **Reused:** `current_head` (both anchors), `changes_between` / `changes_since`, `is_due` /
  `cadence_bucket`, the `DigestRunner` → `AgentRunner` path, and `SlackPoster`. The existing per-project
  digest path is **untouched**.

### Decisions made during brainstorming

- **Per-project anchor (decided):** the shared digest computes each project's head via **that project's
  own anchor** — `binding.digest.anchor` (branch or deploy + workflow) if it has a `digest:` config,
  defaulting to `branch` if it has none. Keeps a project's portfolio digest consistent with its own
  per-project digest.
- **Subscription digest config:** a channel-level `digest: {cadence, tz}` only — no `anchor` field
  (anchoring is per-project).
- **State:** a new, separate `SharedDigestStateStore` (per-`(channel, project)` watermark + channel-level
  `last_digest_at`), not an extension of `digest_state`.
- **Quiet handling:** if no subscribed project has changes this cycle → stay quiet (no post, no advance),
  mirroring the per-project "due but nothing new" rule. If some have changes → one aggregated post +
  advance all watermarks + the channel timer.

### Impact when unconfigured

A subscription with no `digest:` block contributes nothing to the scheduler — **zero behavior change**
for the current MyTV pilot and for plain (Ask-only) subscriptions. The shared-digest pass activates only
when a subscription declares a digest cadence.

## Config model

A subscription gains an optional channel-level digest cadence. The anchor stays per-project, so the
subscription block carries only *when to post*.

```python
@dataclass(frozen=True)
class SubscriptionDigest:
    cadence: str   # "daily" | "weekly"
    tz: str        # IANA tz, validated via ZoneInfo

@dataclass(frozen=True)
class Subscription:
    channel_id: str
    project_names: tuple[str, ...]
    digest: SubscriptionDigest | None = None   # None -> no shared digest (inert)
```

`channels.yaml`:

```yaml
subscriptions:
  - channel_id: C900
    projects: [MyTV, Stream]
    digest:
      cadence: weekly
      tz: Europe/Stockholm
```

**Parsing & validation** (reuses the existing `_parse_digest` style, minus `anchor`):

- Absent `digest:` or `cadence: off` (incl. PyYAML's bare-`off`→`False` coercion) → `digest = None`.
- `cadence` must be `daily|weekly` → else `ValueError` (same message shape as the project digest).
- `tz` must be a valid `ZoneInfo` → else `ValueError`.
- **No `anchor` field** on the subscription digest. Each project's anchor comes from its own
  `digest.anchor` if present, else `branch`.

`Config.digest_subscriptions() -> tuple[Subscription, ...]` returns subscriptions whose `digest is not
None` (mirrors `digest_bindings()`).

**Why a separate `SubscriptionDigest`, not `DigestConfig`:** `DigestConfig` mandates an `anchor` (and
`deploy_workflow` for deploy). A subscription has no single anchor — anchoring is per-project. A lean
two-field type keeps that distinction honest.

## State model

New `SharedDigestStateStore` (mirrors `DigestStateStore`'s `asyncio.to_thread` + `time_fn` + UPSERT
shape), one table:

```sql
CREATE TABLE IF NOT EXISTS shared_digest_state (
    channel_id     TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (channel_id, project_name)
)
```

API:

```python
@dataclass(frozen=True)
class SharedDigestState:
    watermarks: dict[str, str | None]   # project_name -> stored SHA (absent project => not yet seen)
    last_digest_at: float | None        # channel cadence timestamp; None when no rows yet (first run)

class SharedDigestStateStore:
    def get(self, channel_id) -> SharedDigestState        # async (asyncio.to_thread)
    def advance(self, channel_id, heads: dict[str, str], last_digest_at: float) -> None  # async; UPSERT one row per project
    def close(self) -> None
```

`advance` writes one row per project with its new head and the shared `last_digest_at`; all rows for a
channel carry the same `last_digest_at` (it is channel-level; reading it back from any row is
consistent). `get` reads `last_digest_at` from any row (e.g. `MAX(last_digest_at)`), `None` when no rows
exist.

**Why a new table, not extending `digest_state`:** `digest_state` is `channel_id`-PK with a single
watermark — structurally one project per channel. A separate table keeps the two digest modes fully
isolated (no key collision between a portfolio channel and a project home channel) and leaves the
per-project path's storage byte-for-byte unchanged. No TTL (digest state is durable, like
`DigestStateStore`).

## Scheduler flow

`tick` gains a second pass after the existing per-project loop (the first pass is untouched):

```python
async def tick(self, now):
    for binding in self._config.digest_bindings():        # existing per-project pass (unchanged)
        try: await self._maybe_digest(binding, now)
        except Exception: logger.exception("digest failed for %s", binding.name)
    for sub in self._config.digest_subscriptions():       # NEW pass
        try: await self._maybe_shared_digest(sub, now)
        except Exception: logger.exception("shared digest failed for %s", sub.channel_id)
```

`_maybe_shared_digest(sub, now)`:

1. `state = shared_store.get(sub.channel_id)`; if `not is_due(now, state.last_digest_at,
   sub.digest.cadence, sub.digest.tz)` → return.
2. For each project name in `sub.project_names`: resolve its `ProjectBinding` (via `config`); compute its
   head via its own anchor (`binding.digest.anchor` if present, else `branch`); gather its changes vs
   `state.watermarks.get(name)` using the bootstrap rules below. Collect `heads: {name: head}` and
   `per_project_changes: {name: [Change, ...]}`. Skip a project whose head is `None` (no ship signal yet
   — do not advance it).
3. If **no** project has changes → stay quiet: **do not post, do not advance** (re-check next tick).
4. If **some** projects have changes → render **one** aggregated digest covering only the projects with
   changes; post once to `sub.channel_id`; then `advance(channel_id, heads, now.timestamp())` for **all**
   resolved projects (quiet projects still move their watermark forward; the channel timer advances).

**Bootstrap, per project (mirrors the existing per-project first run):**

- No stored watermark for that project (`name not in state.watermarks`) → if its anchor is `branch`, seed
  with a one-cadence-period window (`changes_since(cutoff)`); if `deploy`, start silent (no backfill).
- Stored watermark, `head == watermark` → that project contributed nothing this cycle (empty changes).
- Stored watermark, `head != watermark` → `changes_between(watermark, head)`.

**Head computation refactor (small, targeted):** extract `head_for(owner, repo, anchor, deploy_workflow,
*, get_json) -> str | None` from the current `current_head`; rewrite `current_head(binding, *, get_json)`
as a thin wrapper reading `binding.digest`. The shared path calls `head_for` directly with the
per-project anchor, handling projects with no `digest:` block (where `current_head` would dereference
`None`). The existing `current_head` call site and tests are preserved.

**A subscribed project name that resolves to no binding** is skipped with a `logger.warning` (config
validation from slice 3 already guarantees names exist as bindings, so this only guards a partially-built
config; it never crashes the tick).

## Aggregated summary

New `DigestRunner.summarize_shared(channel_label, per_project_changes) -> str`. It builds one prompt that
groups the change facts under each project heading and asks for a single Slack digest spanning the
portfolio (lead with the cross-project headline; group by project; cite SHAs by 7-char prefix and PRs by
number as GitHub links, as the per-project digest does). The agent call runs scoped to the **first
subscribed project's binding** for GitHub MCP context; the facts for every project are passed as text in
the prompt, which is authoritative for a digest (the summary works from the supplied commit list, not
live repo browsing). `per_project_changes` excludes projects with no changes, so the post only mentions
projects that shipped.

## Error handling & edge cases

- **Not due** → return immediately; nothing fetched, nothing posted.
- **All projects quiet** (every head equals its watermark, or all heads `None`) → no post, no advance;
  the cycle re-checks next tick.
- **Some quiet, some shipped** → one post covering the shippers; all resolved projects' watermarks
  advance; the channel timer advances.
- **A project with no `digest:` config** → anchor defaults to `branch`; bootstrap window on first run.
- **A deploy-anchored project with no successful run** → head `None` → skipped (not advanced), no
  backfill.
- **A subscription with no `digest:`** → not in `digest_subscriptions()`; the shared pass never touches
  it (inert).
- **A subscribed name with no binding** → skipped with a warning; tick continues.
- **One shared digest failing never crosses to another** → per-subscription `try/except` in `tick`, and a
  digest failure never crashes the process (existing `run` loop `try/except`).
- **Read-only preserved** — the only writes are the Slack post and the local SQLite `shared_digest_state`;
  no repo writes; the summary runs through the same read-only agent path.

## Testing

All deterministic — injected fake `get_json`, fake agent runner, fake poster, and `time_fn` / `now_fn`;
no network, no real model calls.

### `tests/test_config.py` (extend)

- A subscription `digest:` parses into `SubscriptionDigest(cadence, tz)`; absent / `cadence: off` →
  `digest is None`; bad cadence → `ValueError`; bad tz → `ValueError`.
- `digest_subscriptions()` returns only subscriptions with a digest.

### `tests/test_digest_state_store.py` (extend)

- `SharedDigestStateStore.advance` then `get` round-trips multiple projects under one channel;
  `last_digest_at` is consistent across rows; a second `advance` updates heads and timer; unknown channel
  → empty `watermarks` and `last_digest_at is None`.

### `tests/test_digest_anchors.py` (extend)

- `head_for` returns the branch head for `anchor="branch"` and the deploy `head_sha` for
  `anchor="deploy"`; `current_head` wrapper still returns the same as before for a binding with a digest.

### `tests/test_digest_runner_poster.py` (extend)

- `summarize_shared` builds a prompt that names each project and includes its facts, and returns the
  agent's text.

### `tests/test_digest_shared.py` (new — the heart)

Using a fake scheduler wiring (fake `get_json`, fake `SharedDigestStateStore` or a real one on
`tmp_path`, fake runner, fake poster, fixed `now`):

- **Not due** → no fetch, no post, no advance.
- **First run, branch bootstrap** → seeds a window, posts one aggregated digest, advances all watermarks
  + timer.
- **All quiet** (heads equal watermarks) → no post, no advance.
- **Some shipped** → one post mentioning only the shipped projects; all resolved watermarks advance; the
  channel timer advances.
- **Project without its own `digest:` config** → defaults to branch and participates.
- **Deploy-anchored project** → uses the deploy head; mixed anchors in one subscription both contribute.
- **A project head `None`** → skipped, not advanced; others still post.
- **A subscribed name with no binding** → skipped with a warning; the rest proceed.

### `tests/test_app.py` (extend)

- `build_scheduler` injects a `SharedDigestStateStore`.

## Scope summary

- **New:** `SubscriptionDigest` + `Config.digest_subscriptions()` (config.py); `SharedDigestStateStore`
  + `SharedDigestState` (session_store.py); `DigestRunner.summarize_shared` (digest/runner.py);
  `DigestScheduler._maybe_shared_digest` + second `tick` pass (digest/scheduler.py); `head_for`
  (digest/anchors.py); `tests/test_digest_shared.py`.
- **Changed:** `config.py`, `session_store.py`, `digest/{anchors,runner,scheduler}.py`, `app.py`
  (`build_scheduler` wiring), `config/channels.yaml` (document the subscription `digest:`).
- **Unchanged:** `orchestrator.py`, `slack_adapter.py`, `access.py`, `lobby.py`, `subscriptions.py`, and
  the per-project digest behavior.
- **Inert when no subscription declares `digest:`.**

## Out of scope (future)

- **Per-Topic digest scoping** — narrowing a shared digest to a thematic slice.
- **Personal / DM digests** — an individual's Personal Digest delivered privately.
- **Unifying the engines** — collapsing the per-project and shared digest paths into one; kept separate
  deliberately so this slice does not disturb the proven per-project path.
- **Summary customisation** — an optional per-digest `audience` / instructions free-text field appended
  to the base prompt (per-channel tone/altitude), keeping the non-negotiable parts (cite SHAs/PRs,
  group, headline). Small, config-driven, version-controlled; deferred to keep this slice focused on
  fan-out + state.
- **Skill-based summary** — replacing the inline summarisation prompt with a versioned read-only
  "digest-writing skill" loaded by the agent. Read-only-by-construction is unaffected (skills are
  instructions, not capabilities), but it first needs a spike to confirm the **headless** Agent SDK
  path loads skills the way the interactive agent does. Its own future slice.
