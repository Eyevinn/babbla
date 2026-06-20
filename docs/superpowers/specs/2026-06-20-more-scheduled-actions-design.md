# More Scheduled Actions (Stale-PR Nudge + ADR-of-the-Week) — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-20
**Builds on:** [Scheduled Actions Framework](2026-06-19-scheduled-actions-design.md) (the `Action` protocol,
`ActionScheduler`, `ActionTimerStore`, `SlackPoster.post`, the read-only GitHub path)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ROADMAP — deferred from the scheduled-actions slice](../../ROADMAP.md)

## Why this slice exists

The scheduled-actions framework was built so new push actions plug in behind one tiny `Action` protocol
(`label` + `maybe_run(now)`). The framework spec named two intended next actions: a **stale-PR nudge** and
an **ADR-of-the-week**. This slice delivers both. Neither needs new scheduler plumbing; both reuse the
heartbeat, `is_due`/`cadence_bucket`, the read-only `get_json` GitHub path, and `SlackPoster`.

### What is new vs. reused

- **New:** `StalePRAction` + `AdrOfWeekAction` (`digest/actions.py`); an `AdrRunner` (`digest/adr.py`,
  mirrors `QuizRunner`); a generic `ActionCursorStore` (`session_store.py`) for rotation pointers; a
  deterministic open-PR fetch/filter helper; `StalePRConfig` + `AdrConfig` + `stale_pr_bindings()` +
  `adr_bindings()` (`config.py`).
- **Reused:** `Action`/`ActionScheduler`, `ActionTimerStore`, `is_due`/`cadence_bucket`, `get_json`
  (read-only GitHub), `SlackPoster.post`, and the read-only `AgentRunner` path (for the ADR teaser only).

### Decisions made during brainstorming

- **Stale-PR nudge is deterministic — no LLM.** It is a list of facts (which PRs are idle); a model call
  would add cost and hallucination risk for no benefit. Fetch open PRs, filter by inactivity, post a list.
- **ADR-of-the-week is rotation + LLM teaser.** Walk `docs/adr/NNNN-*.md` in filename order (a tiny
  per-project cursor), and have the read-only agent write a short engaging summary of the chosen ADR. This
  covers every ADR over time with no repeats, and produces an inviting post rather than a raw excerpt.
- **Defaults:** stale threshold `14` days; drafts excluded; weekly cadence for both; ADR dir `docs/adr`.

### Impact when unconfigured

No `stale_prs:` and no `adr:` block on any project → no actions added → the scheduler is unchanged.
**Zero behavior change** until a block is configured.

## `StalePRAction` (deterministic)

Wraps `(binding, ActionTimerStore, get_json, poster, cadence, tz, threshold_days, include_drafts)`.
`label = f"stale-pr:{binding.name}"`. `maybe_run(now)`:

1. `key = f"stale-pr:{binding.name}"`; if `not is_due(now, timer.get(key), cadence, tz)` → return.
2. `prs = get_json(f"/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=asc&per_page=100")`
   (read-only). `None`/empty → treat as no open PRs.
3. Keep a PR when `now - parse(pr["updated_at"]) >= timedelta(days=threshold_days)` and
   (`include_drafts` or not `pr["draft"]`).
4. **Stale PRs present** → post a deterministic message to `binding.channel_id`:
   - a header line (e.g. `🧹 *<repo> — N open PRs idle ≥ <threshold>d*`)
   - one bullet per PR: `• <html_url|#42> *title* — idle <days>d, @<user.login>`
   sorted oldest-first (the `sort=updated&direction=asc` order). Cap the list at a sensible max (e.g. 20)
   with a `…and M more` tail line so a very stale repo can't post a wall of text (the message itself
   states the overflow — nothing is silently dropped).
5. **No stale PRs** → no post.
6. **Always** `timer.advance(key, now.timestamp())` (whether or not anything was posted): one check per
   cadence bucket, never per-tick. There is no watermark — staleness is recomputed each period from live
   `updated_at`, so re-querying every 15-minute tick would be wasteful and is avoided by advancing the timer.

Posts to `binding.channel_id`. No model call. No repo writes.

### Open-PR fetch/filter helper (`digest/pulls.py`, new)

```python
@dataclass(frozen=True)
class StalePR:
    number: int
    title: str
    author: str
    url: str
    idle_days: int

def stale_prs(owner, repo, *, now, threshold_days, include_drafts, get_json) -> list[StalePR]:
    ...  # fetch open PRs, parse updated_at (ISO 8601 Z), filter, sort oldest-first
```

A small pure function over `get_json` so the action stays thin and the filtering is unit-tested in
isolation (fixed `now`, fake `get_json`).

## `AdrOfWeekAction` (rotation + LLM teaser)

Wraps `(binding, ActionTimerStore, ActionCursorStore, get_json, adr_runner, poster, cadence, tz, dir)`.
`label = f"adr:{binding.name}"`. `maybe_run(now)`:

1. `key = f"adr:{binding.name}"`; if `not is_due(now, timer.get(key), cadence, tz)` → return.
2. `entries = get_json(f"/repos/{owner}/{repo}/contents/{dir}")` (read-only). Keep entries whose `name`
   matches `^\d{4}-.*\.md$` (the ADR convention), sort by `name`. Exclude an index/README implicitly
   (it lacks the `NNNN-` prefix).
3. **No ADRs** → no post; `timer.advance` and return (graceful: a project without ADRs just stays quiet).
4. Pick the **next** ADR after the stored cursor: find the cursor filename's index in the sorted list and
   take the following entry; wrap to the first when the cursor is last or absent/stale.
5. `text = await adr_runner.teaser(binding, adr_path)` — the read-only agent reads that one file and
   returns a short, engaging summary (what the decision was, why it mattered) ending with a link to the
   ADR on GitHub.
6. Post `text` to `binding.channel_id`; then `cursor.set(key, chosen_name)` and `timer.advance(key, ...)`.
   (Advance both only after a successful post so a transient failure retries next tick with the same ADR.)

### `AdrRunner` (`digest/adr.py`, new)

`teaser(binding: ProjectBinding, adr_path: str) -> str` — builds a prompt asking the agent to read the
single file at `adr_path` in the bound repo and write a 1-short-paragraph teaser (decision + why +
GitHub link); calls `agent.run_ask(prompt, binding, None)`; returns the text. Mirrors `QuizRunner.generate`
in shape and in being a thin read-only wrapper around `AgentRunner`.

## State model

### `ActionCursorStore` (`session_store.py`, new)

```sql
CREATE TABLE IF NOT EXISTS action_cursor (
    cursor_key TEXT PRIMARY KEY,
    value      TEXT NOT NULL
)
```

```python
class ActionCursorStore:
    async def get(self, cursor_key: str) -> str | None
    async def set(self, cursor_key: str, value: str) -> None   # UPSERT
    def close(self) -> None
```

A minimal generic key→text store for actions that need a tiny bit of string state (here: the last ADR
filename shown per project). Mirrors `ActionTimerStore`'s `asyncio.to_thread` + UPSERT shape. No TTL.
Timing for both new actions stays in the existing `ActionTimerStore` — this store is *only* the rotation
pointer.

## Config model (`config.py`)

```python
@dataclass(frozen=True)
class StalePRConfig:
    cadence: str          # daily | weekly
    tz: str
    threshold_days: int = 14
    include_drafts: bool = False

@dataclass(frozen=True)
class AdrConfig:
    cadence: str          # daily | weekly
    tz: str
    dir: str = "docs/adr"
```

- `ProjectBinding.stale_prs: StalePRConfig | None = None`; `ProjectBinding.adr: AdrConfig | None = None`.
- `Config.stale_pr_bindings()` → projects with `stale_prs` **and** a non-null `channel_id`.
- `Config.adr_bindings()` → projects with `adr` **and** a non-null `channel_id`.
- Parsing reuses the existing `_parse_cadence_tz` helper (off/absent → `None`; `cadence` in `daily|weekly`;
  valid `ZoneInfo` tz). `threshold_days` must be a positive int (default 14) else `ValueError`;
  `include_drafts` coerced to bool; `dir` defaults to `docs/adr`. Each field independently optional/additive.

`channels.yaml` (documented, commented-out by default):

```yaml
projects:
  - name: MyTV
    ...
    stale_prs:
      cadence: weekly
      tz: Europe/Stockholm
      threshold_days: 14      # idle this many days => nudge
      include_drafts: false
    adr:
      cadence: weekly
      tz: Europe/Stockholm
      dir: docs/adr           # default
```

## Wiring (`app.py`)

`build_scheduler` gains two loops alongside the existing ones:

```python
cursor_store = ActionCursorStore(db_path)
adr_runner = AdrRunner(AgentRunner(secrets))
...
for b in config.stale_pr_bindings():
    actions.append(StalePRAction(b, timer_store, get_json, poster,
                                 b.stale_prs.cadence, b.stale_prs.tz,
                                 b.stale_prs.threshold_days, b.stale_prs.include_drafts))
for b in config.adr_bindings():
    actions.append(AdrOfWeekAction(b, timer_store, cursor_store, get_json, adr_runner, poster,
                                   b.adr.cadence, b.adr.tz, b.adr.dir))
```

Empty config → no actions added → inert tick loop, unchanged.

## Error handling & edge cases

- **Action isolation** — both run under the scheduler's existing per-action `try/except`; a failure in one
  never stops the others.
- **Not due** → immediate return; nothing fetched/posted.
- **Stale-PR: none stale** → no post; timer still advances (one check per period).
- **Stale-PR: very stale repo** → list capped with an `…and M more` tail.
- **Stale-PR: `get_json` returns `None`/404** (repo or PRs unavailable) → treated as no open PRs; quiet;
  timer advances.
- **ADR: no `docs/adr` / no `NNNN-*.md`** → quiet; timer advances (no error).
- **ADR: cursor names a file no longer present** (renamed/deleted) → treated as absent → wrap to first.
- **ADR: agent teaser fails** → exception caught by the scheduler; cursor and timer NOT advanced → retries
  same ADR next tick.
- **Read-only preserved** — only writes are the Slack post and local SQLite (timer + cursor). Stale-PR
  makes no model call; ADR teaser runs through the read-only agent path. No repo writes.

## Testing

All deterministic — fake `get_json`, fake agent runner, fake poster, fixed `now`, `tmp_path` stores; no
network, no real model calls.

- **`tests/test_pulls.py`** (new): `stale_prs()` filters by `updated_at` vs `threshold_days` with a fixed
  `now`; excludes drafts unless `include_drafts`; sorts oldest-first; empty/`None` input → `[]`.
- **`tests/test_stale_pr_action.py`** (new): not-due → nothing; stale present → one list post + timer
  advance; none stale → no post + timer advance; drafts excluded; list cap + `…and M more`; same-bucket
  second tick → not due.
- **`tests/test_adr_action.py`** (new): not-due → nothing; first run (no cursor) → posts first ADR, sets
  cursor, advances timer; subsequent run → next ADR; wrap-around at end; no ADRs → quiet + advance; cursor
  names a missing file → wraps to first; teaser failure → cursor/timer NOT advanced.
- **`tests/test_action_cursor_store.py`** (new): get unknown → `None`; set → get round-trip; set twice →
  upsert.
- **`tests/test_digest_runner_poster.py`** (extend): `AdrRunner.teaser` prompt shape + returned text.
- **`tests/test_config.py`** (extend): `stale_prs:` and `adr:` parse/validate (cadence, tz, threshold,
  drafts, dir defaults); `stale_pr_bindings()` / `adr_bindings()` require a channel.
- **`tests/test_app.py`** (extend): `build_scheduler` assembles `StalePRAction` / `AdrOfWeekAction` for a
  config with each block; inert when neither configured.

## Scope summary

- **New:** `StalePRAction`, `AdrOfWeekAction` (`digest/actions.py`); `AdrRunner` (`digest/adr.py`);
  `stale_prs()` + `StalePR` (`digest/pulls.py`); `ActionCursorStore` (`session_store.py`); `StalePRConfig`
  + `AdrConfig` + `stale_pr_bindings()` + `adr_bindings()` (`config.py`); the new test files above.
- **Changed:** `config.py` (parsing), `app.py` (`build_scheduler`), `config/channels.yaml` (document the
  two blocks).
- **Unchanged:** `orchestrator.py`, `slack_adapter.py`, `access.py`, `lobby.py`, `subscriptions.py`,
  `digest/scheduler.py` (the framework is already general).
- **Inert when no `stale_prs:`/`adr:` configured.**

## Out of scope (future)

- **More action types** beyond these two (the framework stays open).
- **Per-user / interactive** behavior (reactions, scoring) — actions here are one-way posts.
- **Stale-PR LLM commentary** — deliberately deterministic; revisit only if a richer nudge is wanted.
- **ADR selection by "interestingness"** — rotation is intentional (full coverage, no repeats).
