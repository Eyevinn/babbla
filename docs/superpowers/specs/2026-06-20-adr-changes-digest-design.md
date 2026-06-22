# ADR Action → "Recent ADR Changes" Digest — Design

**Status:** Implemented (merged to main)
**Date:** 2026-06-20
**Modifies:** the `AdrOfWeekAction` + `AdrRunner` shipped in
[More Scheduled Actions](2026-06-20-more-scheduled-actions-design.md)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md)

## Why this change

The shipped ADR action rotates: one ADR per week, walking the directory in filename order via a
per-project cursor. The desired behavior is a **weekly digest of ADRs that changed since the last run** —
an opening summary paragraph followed by a list of the changed ADRs, each with a one-line gloss and a link.
This replaces rotation entirely.

## Behavior

`AdrOfWeekAction` is renamed **`AdrDigestAction`** and `AdrRunner.teaser` is renamed
**`AdrRunner.digest`** (the names now describe a batch digest, not a single weekly pick). The config key
stays `adr:` and its fields (`cadence`, `tz`, `dir`) are unchanged.

### Window anchor

The action's **existing `ActionTimerStore` timestamp** is the window anchor — no new state is introduced.
`maybe_run(now)`:

1. `key = f"adr:{binding.name}"`; `last = timer.get(key)`; if `not is_due(now, last, cadence, tz)` → return.
2. `since = None if last is None else datetime.fromtimestamp(last, tz=timezone.utc)`.
3. `changed = changed_adrs(owner, repo, dir, since=since, get_json=get_json)`.
4. **No changed ADRs** → no post; `timer.advance(key, now.timestamp())`; return (quiet).
5. **Changed ADRs present** → `text = await runner.digest(binding, changed)`; post `text` to
   `binding.channel_id`; then `timer.advance(key, now.timestamp())`.
   - Advance only after a successful post. If the agent call or post raises, the scheduler's per-action
     `try/except` catches it and the timer is **not** advanced — the same window retries next bucket.

Because `is_due` gates on the cadence bucket, the previous run's timestamp (`last`) is the previous
bucket's run time; using it as `since` yields exactly "ADRs changed since the last weekly run." A skipped
week widens the window automatically (still anchored on the last *successful* advance).

### First run (no timestamp)

`since is None` → **full backfill**: include every ADR in the directory (no commit-date filter), summarize
them all, then deltas from there.

### Detection — `changed_adrs` (new pure helper, `digest/adr.py`)

```python
def changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]:
    """Return docs/adr/NNNN-*.md paths changed since `since` (None → all). Read-only."""
```

1. `entries = get_json(f"/repos/{owner}/{repo}/contents/{dir}")`. Keep entries whose `name` matches
   `^\d{4}-.*\.md$`; sort by `name`. `None`/empty → `[]`.
2. **`since is None`** (first run) → return all kept paths (`f"{dir}/{name}"`), sorted.
3. **`since` set** → for each kept entry, fetch its latest commit:
   `get_json(f"/repos/{owner}/{repo}/commits?path={dir}/{name}&per_page=1")`; parse
   `[0]["commit"]["committer"]["date"]` (ISO 8601 Z). Keep the path when that date `>= since`. This catches
   both newly-added and edited ADRs. A deleted ADR is absent from the contents listing, so it never appears.

A small pure function over `get_json`, unit-tested in isolation with a fixed `now`/`since` and a fake
`get_json` (no network). Returns paths in sorted filename order.

### Composition — `AdrRunner.digest` (`digest/adr.py`)

```python
async def digest(self, binding: ProjectBinding, adr_paths: list[str]) -> str:
```

Builds one prompt asking the read-only agent to read each file in `adr_paths` (in the bound repo) and write
a Slack post consisting of:
- a short **summary paragraph** synthesizing the batch of changed ADRs, then
- a **bulleted list** — one line per ADR with a one-line gloss and a GitHub link
  (`https://github.com/{owner}/{repo}/blob/HEAD/{path}`).

Calls `agent.run_ask(prompt, binding, None)`; returns `answer.text`. One agent call regardless of count.
On the full-backfill first run it reads every ADR — slower/costlier once, accepted. No cap on the number of
ADRs (directories are small and bounded).

## What is removed

Rotation is gone, and with it its only state consumer:
- `ActionCursorStore` (class) + the `action_cursor` table in `session_store.py`.
- `tests/test_action_cursor_store.py`.
- The `cursor_store` construction in `app.build_scheduler` and the cursor argument to the ADR action.

The `AdrDigestAction` constructor drops the `cursor` parameter:
`AdrDigestAction(binding, timer, get_json, runner, poster, cadence, tz, dir)`.

## Read-only preserved

Only writes remain the Slack post and the local SQLite timer advance. Detection is read-only `get_json`
(contents + commits APIs); the digest runs through the existing read-only `AgentRunner` path. No repo writes.

## Error handling & edge cases

- **Action isolation** — runs under the scheduler's per-action `try/except`; one failure never stops others.
- **Not due** → immediate return; nothing fetched/posted.
- **No `docs/adr` / no `NNNN-*.md`** → `changed_adrs` returns `[]` → quiet; timer advances.
- **`get_json` returns `None`/404** (dir or commits unavailable) → treated as no entries / no commit →
  that ADR is skipped; quiet where it yields `[]`; timer advances.
- **Nothing changed since cutoff** → no post; timer advances.
- **Agent digest fails / post fails** → exception caught by scheduler; timer **not** advanced → retries the
  same window next bucket.
- **First run with ADRs present** → posts the full backfill, then advances.

## Testing

All deterministic — fake `get_json`, fake agent runner, fake poster, fixed `now`, `tmp_path` timer; no
network, no real model calls.

- **`tests/test_adr_changes.py`** (new): `changed_adrs()` — `since=None` returns all (sorted);
  `since` set keeps only files whose latest commit `>= since`; regex excludes non-`NNNN-` files; empty/`None`
  contents → `[]`; a file whose commit lookup returns `None`/empty is skipped.
- **`tests/test_adr_action.py`** (rewrite): not-due → nothing; first run (no timer) → posts backfill of all
  ADRs + advances; subsequent run → posts only the changed ADRs + advances; nothing changed → quiet +
  advance; digest failure → timer NOT advanced; no ADRs → quiet + advance.
- **`tests/test_digest_runner_poster.py`** (update): `AdrRunner.digest` prompt includes all given paths and
  asks for a summary + list; returns the agent text.
- **`tests/test_config.py`** — unchanged (`adr:` block is unchanged).
- **`tests/test_app.py`** (update): `build_scheduler` assembles `AdrDigestAction` (renamed) for an `adr:`
  config; the `ActionCursorStore` wiring is gone; inert when unconfigured.
- **Remove `tests/test_action_cursor_store.py`.**

## Scope summary

- **Changed:** `digest/adr.py` (`AdrRunner.digest` + `changed_adrs`), `digest/actions.py`
  (`AdrDigestAction`, renamed, cursor dropped, rotation → delta), `app.py` (`build_scheduler` wiring),
  `session_store.py` (remove `ActionCursorStore` + table).
- **New tests:** `tests/test_adr_changes.py`. **Rewritten:** `tests/test_adr_action.py`.
  **Updated:** `tests/test_digest_runner_poster.py`, `tests/test_app.py`. **Removed:**
  `tests/test_action_cursor_store.py`.
- **Unchanged:** config model (`AdrConfig`, `adr_bindings()`), `StalePRAction`/`stale_prs`, scheduler,
  `ActionTimerStore`.

## Out of scope (future)

- Distinguishing "new" vs "updated" ADRs in the list (treated uniformly as recently changed).
- A cap / pagination on the ADR list (directories are small; revisit only if one grows large).
- Restoring rotation as an alternate mode.
