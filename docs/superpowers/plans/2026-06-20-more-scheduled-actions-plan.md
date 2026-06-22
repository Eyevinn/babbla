# More Scheduled Actions (Stale-PR Nudge + ADR-of-the-Week) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new push actions — a deterministic stale-PR nudge and a rotation-based ADR-of-the-week teaser — behind the existing `Action` protocol, with zero behavior change until configured.

**Architecture:** Both actions plug into the existing `ActionScheduler`. `StalePRAction` is pure-deterministic: fetch open PRs via the read-only `get_json` path, filter by inactivity, post a list. `AdrOfWeekAction` walks `docs/adr/NNNN-*.md` in filename order using a tiny per-project cursor and has the read-only agent write a short teaser. New state lives in a generic `ActionCursorStore` (rotation pointer); timing stays in the existing `ActionTimerStore`.

**Tech Stack:** Python 3.14, `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` — async tests need no decorator), stdlib `sqlite3`, `urllib`, `zoneinfo`. Claude Agent SDK for the ADR teaser (read-only).

## Global Constraints

- **Read-only by construction (ADR 0003):** the only writes are the Slack post and local SQLite (timer + cursor). No repo writes. Stale-PR makes **no** model call; ADR teaser runs through the existing read-only `AgentRunner.run_ask` path.
- **Inert when unconfigured:** no `stale_prs:` and no `adr:` block on any project ⇒ no actions added ⇒ scheduler unchanged.
- **Defaults (verbatim from spec):** stale threshold `14` days; drafts excluded (`include_drafts: false`); weekly cadence for both; ADR dir `docs/adr`.
- **Cadence values:** `daily | weekly` (reuse existing `_CADENCES`); `off`/absent ⇒ `None` (block disabled). Reuse `_parse_cadence_tz`.
- **One check per cadence bucket:** advance the timer every period (even when nothing is posted) so live `updated_at` is re-checked once per bucket, never per 15-minute tick.
- **Run tests with:** `.venv/bin/python -m pytest` from the repo root (`/Users/kunwu/Workspace/babbla`).
- **Commit style:** Conventional Commits (`feat:`, `test:`); match existing history.

---

### Task 1: `ActionCursorStore` — generic rotation pointer

**Files:**
- Modify: `src/babbla/session_store.py` (append a new schema constant + class, mirroring `ActionTimerStore`)
- Test: `tests/test_action_cursor_store.py` (create)

**Interfaces:**
- Consumes: nothing (stdlib `sqlite3`, `asyncio` already imported at top of `session_store.py`).
- Produces:
  - `class ActionCursorStore(db_path: str)`
  - `async def get(self, cursor_key: str) -> str | None`
  - `async def set(self, cursor_key: str, value: str) -> None` (UPSERT)
  - `def close(self) -> None`

- [x] **Step 1: Write the failing test**

Create `tests/test_action_cursor_store.py`:

```python
from babbla.session_store import ActionCursorStore


async def test_get_unknown_returns_none(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    assert await store.get("adr:MyTV") is None
    store.close()


async def test_set_then_get_roundtrip(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    await store.set("adr:MyTV", "0003-read-only.md")
    assert await store.get("adr:MyTV") == "0003-read-only.md"
    store.close()


async def test_set_twice_upserts(tmp_path):
    store = ActionCursorStore(str(tmp_path / "s.db"))
    await store.set("adr:MyTV", "0003-read-only.md")
    await store.set("adr:MyTV", "0004-deploy.md")
    assert await store.get("adr:MyTV") == "0004-deploy.md"
    store.close()


async def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.db")
    store = ActionCursorStore(path)
    await store.set("adr:MyTV", "0001-hybrid.md")
    store.close()
    store2 = ActionCursorStore(path)
    assert await store2.get("adr:MyTV") == "0001-hybrid.md"
    store2.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_action_cursor_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'ActionCursorStore'`.

- [x] **Step 3: Write minimal implementation**

Append to the end of `src/babbla/session_store.py`:

```python
_ACTION_CURSOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_cursor (
    cursor_key TEXT PRIMARY KEY,
    value      TEXT NOT NULL
)
"""


class ActionCursorStore:
    """A minimal generic key->text store for actions needing a tiny bit of string
    state (e.g. the last ADR filename shown per project). Mirrors ActionTimerStore.
    Timing stays in ActionTimerStore — this store is only the rotation pointer."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_ACTION_CURSOR_SCHEMA)
        self._conn.commit()

    async def get(self, cursor_key: str) -> str | None:
        return await asyncio.to_thread(self._get_sync, cursor_key)

    def _get_sync(self, cursor_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM action_cursor WHERE cursor_key = ?", (cursor_key,)
        ).fetchone()
        return row[0] if row else None

    async def set(self, cursor_key: str, value: str) -> None:
        await asyncio.to_thread(self._set_sync, cursor_key, value)

    def _set_sync(self, cursor_key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO action_cursor (cursor_key, value) VALUES (?, ?) "
            "ON CONFLICT(cursor_key) DO UPDATE SET value = excluded.value",
            (cursor_key, value),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_action_cursor_store.py -v`
Expected: PASS (4 passed).

- [x] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_action_cursor_store.py
git commit -m "feat: add ActionCursorStore generic rotation-pointer store"
```

---

### Task 2: `stale_prs()` open-PR fetch/filter helper

**Files:**
- Create: `src/babbla/digest/pulls.py`
- Test: `tests/test_pulls.py` (create)

**Interfaces:**
- Consumes: a `get_json(path) -> object | None` callable (same shape as `babbla.digest.anchors.make_get_json`).
- Produces:
  - `@dataclass(frozen=True) class StalePR` with fields `number: int`, `title: str`, `author: str`, `url: str`, `idle_days: int`
  - `def stale_prs(owner, repo, *, now, threshold_days, include_drafts, get_json) -> list[StalePR]` — sorted oldest-first (most idle first).

- [x] **Step 1: Write the failing test**

Create `tests/test_pulls.py`:

```python
from datetime import datetime, timezone

from babbla.digest.pulls import StalePR, stale_prs

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _pr(number, days_ago, *, draft=False, title=None, login="alice"):
    updated = (NOW - __import__("datetime").timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "number": number,
        "title": title or f"PR {number}",
        "html_url": f"https://github.com/o/r/pull/{number}",
        "draft": draft,
        "updated_at": updated,
        "user": {"login": login},
    }


def _reader(prs):
    def get_json(path):
        assert "state=open" in path and "sort=updated" in path and "direction=asc" in path
        return prs
    return get_json


def test_filters_by_threshold():
    prs = [_pr(1, days_ago=20), _pr(2, days_ago=3)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [1]
    assert out[0] == StalePR(number=1, title="PR 1", author="alice",
                             url="https://github.com/o/r/pull/1", idle_days=20)


def test_excludes_drafts_unless_included():
    prs = [_pr(1, days_ago=30, draft=True), _pr(2, days_ago=30)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [2]
    out2 = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=True, get_json=_reader(prs))
    assert sorted(p.number for p in out2) == [1, 2]


def test_sorts_oldest_first():
    prs = [_pr(1, days_ago=15), _pr(2, days_ago=40), _pr(3, days_ago=20)]
    out = stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=_reader(prs))
    assert [p.number for p in out] == [2, 3, 1]   # most idle first


def test_empty_and_none_input():
    assert stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=lambda p: None) == []
    assert stale_prs("o", "r", now=NOW, threshold_days=14, include_drafts=False, get_json=lambda p: []) == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pulls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.digest.pulls'`.

- [x] **Step 3: Write minimal implementation**

Create `src/babbla/digest/pulls.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StalePR:
    number: int
    title: str
    author: str
    url: str
    idle_days: int


def _parse_ts(value: str) -> datetime:
    # GitHub timestamps are ISO 8601 with a trailing Z (UTC).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stale_prs(owner, repo, *, now, threshold_days, include_drafts, get_json) -> list[StalePR]:
    """Fetch open PRs (read-only), keep those idle >= threshold_days, sort oldest-first."""
    path = f"/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=asc&per_page=100"
    data = get_json(path)
    if not data:
        return []
    out: list[StalePR] = []
    for pr in data:
        if pr.get("draft") and not include_drafts:
            continue
        idle = (now - _parse_ts(pr["updated_at"])).days
        if idle < threshold_days:
            continue
        out.append(
            StalePR(
                number=pr["number"],
                title=pr.get("title", ""),
                author=(pr.get("user") or {}).get("login", ""),
                url=pr.get("html_url", ""),
                idle_days=idle,
            )
        )
    out.sort(key=lambda p: p.idle_days, reverse=True)  # oldest-first = most idle first
    return out
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pulls.py -v`
Expected: PASS (4 passed).

- [x] **Step 5: Commit**

```bash
git add src/babbla/digest/pulls.py tests/test_pulls.py
git commit -m "feat: add stale_prs open-PR fetch/filter helper"
```

---

### Task 3: `StalePRAction` (deterministic nudge)

**Files:**
- Modify: `src/babbla/digest/actions.py` (add import + new class at end)
- Test: `tests/test_stale_pr_action.py` (create)

**Interfaces:**
- Consumes: `stale_prs` (Task 2), `is_due` (already imported in `actions.py`), an `ActionTimerStore`-shaped timer (`get(key)`, `advance(key, ts)`), a `SlackPoster`-shaped poster (`post(channel_id, text) -> ts`), a `get_json` callable, a `ProjectBinding`.
- Produces:
  - `class StalePRAction(binding, timer, get_json, poster, cadence, tz, threshold_days, include_drafts)`
  - attributes: `label = f"stale-pr:{binding.name}"`, `project = binding.name`
  - `async def maybe_run(self, now) -> None`

- [x] **Step 1: Write the failing test**

Create `tests/test_stale_pr_action.py`:

```python
from datetime import datetime, timedelta, timezone

from babbla.config import ProjectBinding, StalePRConfig
from babbla.digest.actions import StalePRAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          stale_prs=StalePRConfig("weekly", "UTC", 14, False))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


def _pr(number, days_ago, *, draft=False, login="alice", title=None):
    updated = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"number": number, "title": title or f"PR {number}",
            "html_url": f"https://github.com/Wkkkkk/MyTV/pull/{number}",
            "draft": draft, "updated_at": updated, "user": {"login": login}}


def _reader(prs):
    return lambda path: prs


def _action(last, prs, *, threshold_days=14, include_drafts=False):
    timer, poster = FakeTimer(last), FakePoster()
    action = StalePRAction(_binding(), timer, _reader(prs), poster,
                           "weekly", "UTC", threshold_days, include_drafts)
    return action, timer, poster


async def test_not_due_does_nothing():
    action, timer, poster = _action(NOW.timestamp(), [_pr(1, 30)])
    await action.maybe_run(NOW)
    assert poster.posts == [] and timer.advanced == []


async def test_stale_present_posts_list_and_advances():
    action, timer, poster = _action(None, [_pr(42, 30, login="bob", title="fix thing")])
    await action.maybe_run(NOW)
    assert len(poster.posts) == 1
    channel, text = poster.posts[0]
    assert channel == "C0XXXXXXXXX"
    assert "MyTV" in text and "14d" in text
    assert "<https://github.com/Wkkkkk/MyTV/pull/42|#42>" in text
    assert "*fix thing*" in text and "30d" in text and "@bob" in text
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_none_stale_no_post_but_advances():
    action, timer, poster = _action(None, [_pr(1, 3)])   # fresh PR, below threshold
    await action.maybe_run(NOW)
    assert poster.posts == []
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_drafts_excluded_by_default():
    action, timer, poster = _action(None, [_pr(1, 30, draft=True)])
    await action.maybe_run(NOW)
    assert poster.posts == []                 # only stale PR was a draft
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_list_capped_with_and_more_tail():
    prs = [_pr(n, 30 + n) for n in range(1, 26)]   # 25 stale PRs
    action, timer, poster = _action(None, prs)
    await action.maybe_run(NOW)
    text = poster.posts[0][1]
    assert text.count("• ") == 20                  # capped at 20 bullets
    assert "…and 5 more" in text


async def test_none_returned_treated_as_no_prs():
    timer, poster = FakeTimer(None), FakePoster()
    action = StalePRAction(_binding(), timer, lambda p: None, poster, "weekly", "UTC", 14, False)
    await action.maybe_run(NOW)
    assert poster.posts == []
    assert timer.advanced == [("stale-pr:MyTV", NOW.timestamp())]


async def test_same_bucket_second_tick_not_due():
    action, timer, poster = _action((NOW - timedelta(hours=1)).timestamp(), [_pr(1, 30)])
    await action.maybe_run(NOW)
    assert poster.posts == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stale_pr_action.py -v`
Expected: FAIL — `ImportError: cannot import name 'StalePRAction'` (and `StalePRConfig`, added in Task 6; if running this task before Task 6, define the import will fail first on `StalePRConfig`). If the `StalePRConfig` import blocks the run, complete Task 6's config dataclass first, then return — but the canonical order keeps Task 6 later, so temporarily this test imports `StalePRConfig` which does not yet exist. To keep tasks independently runnable, add the `StalePRConfig` dataclass as part of THIS task's Step 3 as well (see note).

> **Note:** `StalePRConfig` is formally defined in Task 6. This test imports it only to build a binding. If you implement strictly in order, add the `StalePRConfig`/`AdrConfig` dataclasses and the `ProjectBinding` fields from Task 6 Step 3 *now* (they are pure dataclass additions with defaults and break nothing), then finish the rest of Task 6 later. The dataclass code is repeated here for convenience:
>
> ```python
> @dataclass(frozen=True)
> class StalePRConfig:
>     cadence: str
>     tz: str
>     threshold_days: int = 14
>     include_drafts: bool = False
> ```
> and add to `ProjectBinding`: `stale_prs: "StalePRConfig | None" = None`.

- [x] **Step 3: Write minimal implementation**

At the top of `src/babbla/digest/actions.py`, add to the imports (after the existing `from babbla.digest.cadence import is_due`):

```python
from babbla.digest.pulls import stale_prs
```

Append this class to the end of `src/babbla/digest/actions.py`:

```python
class StalePRAction:
    _MAX = 20

    def __init__(self, binding, timer, get_json, poster, cadence: str, tz: str,
                 threshold_days: int, include_drafts: bool) -> None:
        self._b = binding
        self._timer = timer
        self._get_json = get_json
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._threshold_days = threshold_days
        self._include_drafts = include_drafts
        self._key = f"stale-pr:{binding.name}"
        self.project = binding.name
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        prs = stale_prs(
            self._b.owner, self._b.repo, now=now,
            threshold_days=self._threshold_days,
            include_drafts=self._include_drafts, get_json=self._get_json,
        )
        if prs:
            await self._poster.post(self._b.channel_id, self._render(prs))
        # Always advance: one check per cadence bucket, never per-tick. No watermark —
        # staleness is recomputed each period from live updated_at.
        await self._timer.advance(self._key, now.timestamp())

    def _render(self, prs) -> str:
        lines = [f"🧹 *{self._b.repo} — {len(prs)} open PRs idle ≥ {self._threshold_days}d*"]
        for pr in prs[: self._MAX]:
            lines.append(
                f"• <{pr.url}|#{pr.number}> *{pr.title}* — idle {pr.idle_days}d, @{pr.author}"
            )
        if len(prs) > self._MAX:
            lines.append(f"…and {len(prs) - self._MAX} more")
        return "\n".join(lines)
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_stale_pr_action.py -v`
Expected: PASS (7 passed).

- [x] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_stale_pr_action.py src/babbla/config.py
git commit -m "feat: add StalePRAction deterministic stale-PR nudge"
```

---

### Task 4: `AdrRunner` (read-only LLM teaser)

**Files:**
- Create: `src/babbla/digest/adr.py`
- Test: `tests/test_digest_runner_poster.py` (extend — append at end)

**Interfaces:**
- Consumes: an `AgentRunner`-shaped object with `async run_ask(prompt, binding, resume_session_id) -> CitedAnswer` (CitedAnswer has `.text`). A `ProjectBinding`.
- Produces:
  - `class AdrRunner(agent_runner)`
  - `async def teaser(self, binding: ProjectBinding, adr_path: str) -> str`

- [x] **Step 1: Write the failing test**

Append to `tests/test_digest_runner_poster.py`:

```python
from babbla.digest.adr import AdrRunner


async def test_adr_runner_builds_prompt_and_returns_text():
    agent = SentinelAgent("TEASER TEXT")
    out = await AdrRunner(agent).teaser(_binding(), "docs/adr/0003-read-only.md")
    assert out == "TEASER TEXT"
    p = agent.prompt
    assert "docs/adr/0003-read-only.md" in p
    assert "Wkkkkk/MyTV" in p                       # repo slug for the GitHub link
    assert "github.com/Wkkkkk/MyTV" in p            # asks for a link back to the ADR
```

(`SentinelAgent` and `_binding` already exist in this file. `SentinelAgent.run_ask` asserts `resume_session_id is None`, which the runner satisfies.)

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py::test_adr_runner_builds_prompt_and_returns_text -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.digest.adr'`.

- [x] **Step 3: Write minimal implementation**

Create `src/babbla/digest/adr.py`:

```python
from __future__ import annotations

from babbla.config import ProjectBinding


class AdrRunner:
    """Thin read-only wrapper around AgentRunner that turns one ADR file into a short,
    engaging Slack teaser. Mirrors QuizRunner in shape."""

    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def teaser(self, binding: ProjectBinding, adr_path: str) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        prompt = (
            f"Read the single file at {adr_path} in the repository {slug}. Write one short, "
            f"engaging paragraph for a Slack channel: what the architectural decision was and "
            f"why it mattered. End with a link to the ADR on GitHub "
            f"(https://github.com/{slug}/blob/HEAD/{adr_path}). Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py::test_adr_runner_builds_prompt_and_returns_text -v`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add src/babbla/digest/adr.py tests/test_digest_runner_poster.py
git commit -m "feat: add AdrRunner read-only ADR teaser generator"
```

---

### Task 5: `AdrOfWeekAction` (rotation + teaser)

**Files:**
- Modify: `src/babbla/digest/actions.py` (add `import re` + new class at end)
- Test: `tests/test_adr_action.py` (create)

**Interfaces:**
- Consumes: `is_due` (imported), an `ActionTimerStore`-shaped timer, an `ActionCursorStore`-shaped cursor (`get(key)`, `set(key, value)`), a `get_json` callable, an `AdrRunner`-shaped runner (`async teaser(binding, adr_path) -> str`), a `SlackPoster`-shaped poster, a `ProjectBinding`.
- Produces:
  - `class AdrOfWeekAction(binding, timer, cursor, get_json, runner, poster, cadence, tz, dir)`
  - attributes: `label = f"adr:{binding.name}"`, `project = binding.name`
  - `async def maybe_run(self, now) -> None`

- [x] **Step 1: Write the failing test**

Create `tests/test_adr_action.py`:

```python
from datetime import datetime, timedelta, timezone

from babbla.config import AdrConfig, ProjectBinding
from babbla.digest.actions import AdrOfWeekAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          adr=AdrConfig("weekly", "UTC", "docs/adr"))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakeCursor:
    def __init__(self, value=None): self._v = value; self.sets = []
    async def get(self, key): return self._v
    async def set(self, key, value): self.sets.append((key, value)); self._v = value


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


class FakeAdrRunner:
    def __init__(self, text="TEASER", fail=False):
        self._text = text; self._fail = fail; self.calls = []
    async def teaser(self, binding, adr_path):
        self.calls.append(adr_path)
        if self._fail:
            raise RuntimeError("agent boom")
        return self._text


def _entries(*names):
    # GitHub contents API returns a list of entries with a `name` each.
    items = [{"name": n} for n in names]
    return lambda path: items


def _action(last, cursor_value, reader, runner):
    timer, cursor, poster = FakeTimer(last), FakeCursor(cursor_value), FakePoster()
    action = AdrOfWeekAction(_binding(), timer, cursor, reader, runner, poster,
                             "weekly", "UTC", "docs/adr")
    return action, timer, cursor, poster


async def test_not_due_does_nothing():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(NOW.timestamp(), None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == [] and cursor.sets == []


async def test_first_run_posts_first_adr_sets_cursor_advances():
    runner = FakeAdrRunner("TEASER A")
    reader = _entries("0002-b.md", "0001-a.md", "README.md")  # unsorted + non-NNNN excluded
    action, timer, cursor, poster = _action(None, None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]      # sorted-first, README ignored
    assert poster.posts == [("C0XXXXXXXXX", "TEASER A")]
    assert cursor.sets == [("adr:MyTV", "0001-a.md")]
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_subsequent_run_picks_next_adr():
    runner = FakeAdrRunner("TEASER B")
    reader = _entries("0001-a.md", "0002-b.md", "0003-c.md")
    action, timer, cursor, poster = _action(None, "0001-a.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0002-b.md"]
    assert cursor.sets == [("adr:MyTV", "0002-b.md")]


async def test_wraps_around_at_end():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, "0002-b.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]      # wrapped to first


async def test_cursor_names_missing_file_wraps_to_first():
    runner = FakeAdrRunner()
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, "9999-gone.md", reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == ["docs/adr/0001-a.md"]


async def test_no_adrs_quiet_but_advances():
    runner = FakeAdrRunner()
    reader = _entries("README.md", "index.md")          # nothing matches NNNN-*.md
    action, timer, cursor, poster = _action(None, None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert cursor.sets == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_none_contents_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, cursor, poster = _action(None, None, lambda p: None, runner)
    await action.maybe_run(NOW)
    assert poster.posts == [] and timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_teaser_failure_does_not_advance_cursor_or_timer():
    runner = FakeAdrRunner(fail=True)
    reader = _entries("0001-a.md", "0002-b.md")
    action, timer, cursor, poster = _action(None, None, reader, runner)
    try:
        await action.maybe_run(NOW)
    except RuntimeError:
        pass
    assert poster.posts == [] and cursor.sets == [] and timer.advanced == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adr_action.py -v`
Expected: FAIL — `ImportError: cannot import name 'AdrOfWeekAction'` (and `AdrConfig`, formally added in Task 6 — same note as Task 3: add the `AdrConfig` dataclass and `ProjectBinding.adr` field now if implementing strictly in order).

- [x] **Step 3: Write minimal implementation**

At the top of `src/babbla/digest/actions.py`, add `import re` to the stdlib imports (next to `import logging`):

```python
import re
```

Append this class to the end of `src/babbla/digest/actions.py`:

```python
_ADR_RE = re.compile(r"^\d{4}-.*\.md$")


class AdrOfWeekAction:
    def __init__(self, binding, timer, cursor, get_json, runner, poster,
                 cadence: str, tz: str, dir: str) -> None:
        self._b = binding
        self._timer = timer
        self._cursor = cursor
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._dir = dir
        self._key = f"adr:{binding.name}"
        self.project = binding.name
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        entries = self._get_json(f"/repos/{self._b.owner}/{self._b.repo}/contents/{self._dir}")
        names = sorted(
            e["name"] for e in (entries or []) if _ADR_RE.match(e.get("name", ""))
        )
        if not names:
            # No ADRs (or no docs/adr): stay quiet, but advance so we check once per period.
            await self._timer.advance(self._key, now.timestamp())
            return
        chosen = self._next(names, await self._cursor.get(self._key))
        # Teaser failure raises here -> scheduler catches it -> cursor/timer NOT advanced
        # -> retries the same ADR next tick.
        text = await self._runner.teaser(self._b, f"{self._dir}/{chosen}")
        await self._poster.post(self._b.channel_id, text)
        await self._cursor.set(self._key, chosen)
        await self._timer.advance(self._key, now.timestamp())

    @staticmethod
    def _next(names: list[str], cursor: str | None) -> str:
        # Next ADR after the cursor; wrap to first when cursor is last, absent, or stale.
        if cursor in names:
            return names[(names.index(cursor) + 1) % len(names)]
        return names[0]
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adr_action.py -v`
Expected: PASS (8 passed).

- [x] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_adr_action.py src/babbla/config.py
git commit -m "feat: add AdrOfWeekAction rotation + teaser"
```

---

### Task 6: Config — `StalePRConfig`, `AdrConfig`, parsing, bindings

**Files:**
- Modify: `src/babbla/config.py`
- Modify: `config/channels.yaml` (document the two blocks, commented-out)
- Test: `tests/test_config.py` (extend — append at end)

> If Task 3/Task 5 already added the `StalePRConfig`/`AdrConfig` dataclasses and `ProjectBinding` fields per their notes, skip re-adding them here and implement only the parse functions, `load_config` wiring, and binding methods.

**Interfaces:**
- Consumes: `_parse_cadence_tz(label, raw, kind) -> (cadence, tz) | None` (existing).
- Produces:
  - `@dataclass(frozen=True) class StalePRConfig` (`cadence`, `tz`, `threshold_days=14`, `include_drafts=False`)
  - `@dataclass(frozen=True) class AdrConfig` (`cadence`, `tz`, `dir="docs/adr"`)
  - `ProjectBinding.stale_prs: StalePRConfig | None = None`, `ProjectBinding.adr: AdrConfig | None = None`
  - `Config.stale_pr_bindings() -> tuple[ProjectBinding, ...]`, `Config.adr_bindings() -> tuple[ProjectBinding, ...]`
  - module-level `_parse_stale_prs(name, raw)`, `_parse_adr(name, raw)`

- [x] **Step 1: Write the failing test**

Append to `tests/test_config.py` (and update the top import to include the two new configs):

Change line 7 from:
```python
from babbla.config import Config, ProjectBinding, load_config, QuizConfig, PersonalDigestConfig
```
to:
```python
from babbla.config import (
    Config, ProjectBinding, load_config, QuizConfig, PersonalDigestConfig,
    StalePRConfig, AdrConfig,
)
```

Append at the end of the file:

```python
STALE_PR_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    stale_prs:
      cadence: weekly
      tz: Europe/Stockholm
      threshold_days: 21
      include_drafts: true
"""

ADR_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    adr:
      cadence: weekly
      tz: Europe/Stockholm
"""


def test_stale_prs_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, STALE_PR_FIXTURE))
    assert cfg.bindings[0].stale_prs == StalePRConfig(
        cadence="weekly", tz="Europe/Stockholm", threshold_days=21, include_drafts=True
    )


def test_stale_prs_defaults(tmp_path):
    text = STALE_PR_FIXTURE.replace("      threshold_days: 21\n", "").replace(
        "      include_drafts: true\n", ""
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].stale_prs.threshold_days == 14
    assert cfg.bindings[0].stale_prs.include_drafts is False


def test_stale_prs_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].stale_prs is None


def test_stale_prs_bad_threshold_raises(tmp_path):
    text = STALE_PR_FIXTURE.replace("threshold_days: 21", "threshold_days: 0")
    with pytest.raises(ValueError, match="threshold_days"):
        load_config(_write(tmp_path, text))


def test_stale_pr_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, STALE_PR_FIXTURE))
    assert tuple(b.name for b in cfg.stale_pr_bindings()) == ("MyTV",)
    text = STALE_PR_FIXTURE.replace("channel_id: C123", "channel_id: null")
    assert load_config(_write(tmp_path, text)).stale_pr_bindings() == ()


def test_adr_parsed_with_dir_default(tmp_path):
    cfg = load_config(_write(tmp_path, ADR_FIXTURE))
    assert cfg.bindings[0].adr == AdrConfig(cadence="weekly", tz="Europe/Stockholm", dir="docs/adr")


def test_adr_custom_dir(tmp_path):
    text = ADR_FIXTURE.replace(
        "      tz: Europe/Stockholm\n",
        "      tz: Europe/Stockholm\n      dir: documentation/decisions\n",
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].adr.dir == "documentation/decisions"


def test_adr_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].adr is None


def test_adr_bad_cadence_raises(tmp_path):
    text = ADR_FIXTURE.replace("cadence: weekly", "cadence: hourly")
    with pytest.raises(ValueError, match="adr.cadence"):
        load_config(_write(tmp_path, text))


def test_adr_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, ADR_FIXTURE))
    assert tuple(b.name for b in cfg.adr_bindings()) == ("MyTV",)
    text = ADR_FIXTURE.replace("channel_id: C123", "channel_id: null")
    assert load_config(_write(tmp_path, text)).adr_bindings() == ()
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'StalePRConfig'` (or, if dataclasses already added in Tasks 3/5, failures on `_parse_stale_prs`/`stale_pr_bindings` not existing).

- [x] **Step 3: Write minimal implementation**

In `src/babbla/config.py`, add the two dataclasses after `QuizConfig` (skip if already added in Tasks 3/5):

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

Add to `ProjectBinding` (after `quiz: QuizConfig | None = None`) — skip if already added:

```python
    stale_prs: "StalePRConfig | None" = None
    adr: "AdrConfig | None" = None
```

Add these methods to `Config` (after `quiz_bindings`):

```python
    def stale_pr_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.stale_prs is not None and b.channel_id)

    def adr_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.adr is not None and b.channel_id)
```

Add these parse helpers after `_parse_quiz`:

```python
def _parse_stale_prs(name: str, raw: dict | None) -> "StalePRConfig | None":
    ct = _parse_cadence_tz(name, raw, "stale_prs")
    if ct is None:
        return None
    threshold = raw.get("threshold_days", 14)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise ValueError(
            f"{name}: stale_prs.threshold_days must be a positive integer, got {threshold!r}"
        )
    include_drafts = bool(raw.get("include_drafts", False))
    return StalePRConfig(cadence=ct[0], tz=ct[1], threshold_days=threshold,
                         include_drafts=include_drafts)


def _parse_adr(name: str, raw: dict | None) -> "AdrConfig | None":
    ct = _parse_cadence_tz(name, raw, "adr")
    if ct is None:
        return None
    return AdrConfig(cadence=ct[0], tz=ct[1], dir=str(raw.get("dir", "docs/adr")))
```

In `load_config`, add the two fields to the `ProjectBinding(...)` construction (after `quiz=_parse_quiz(p["name"], p.get("quiz")),`):

```python
            stale_prs=_parse_stale_prs(p["name"], p.get("stale_prs")),
            adr=_parse_adr(p["name"], p.get("adr")),
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the 10 new ones).

- [x] **Step 5: Document the blocks in `config/channels.yaml`**

In `config/channels.yaml`, extend the existing "Scheduled actions (optional)" comment block (the one documenting `quiz:`). After the `quiz:` documentation lines and before the "Personal Subscriptions (optional)" comment, add:

```yaml
# You can also nudge about stale open PRs (deterministic — no model call) and post
# a rotating "ADR of the week" teaser (read-only agent) to a PROJECT's channel:
#      stale_prs:
#        cadence: weekly         # daily | weekly
#        tz: Europe/Stockholm
#        threshold_days: 14      # idle this many days => nudge (default 14)
#        include_drafts: false   # default false
#      adr:
#        cadence: weekly         # daily | weekly
#        tz: Europe/Stockholm
#        dir: docs/adr           # default
```

> Do **not** alter the existing real `channel_id`/`lobby_channel_id` bindings already in the working copy — only add the commented documentation lines.

- [x] **Step 6: Run the full config suite again + commit**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS.

```bash
git add src/babbla/config.py tests/test_config.py config/channels.yaml
git commit -m "feat: parse stale_prs and adr config blocks + document in channels.yaml"
```

---

### Task 7: Wiring — `build_scheduler` assembles the two actions

**Files:**
- Modify: `src/babbla/app.py` (imports + `build_scheduler`)
- Test: `tests/test_app.py` (extend — append at end)

**Interfaces:**
- Consumes: `StalePRAction`, `AdrOfWeekAction` (Tasks 3/5); `AdrRunner` (Task 4); `ActionCursorStore` (Task 1); `Config.stale_pr_bindings()`, `Config.adr_bindings()` (Task 6); existing `ActionTimerStore`, `make_get_json`, `SlackPoster`, `AgentRunner`.
- Produces: a `build_scheduler` that appends a `StalePRAction` per `stale_pr_bindings()` and an `AdrOfWeekAction` per `adr_bindings()`.

- [x] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
from babbla.digest.actions import StalePRAction, AdrOfWeekAction


def test_build_scheduler_assembles_stale_pr_and_adr(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    stale_prs:\n      cadence: weekly\n      tz: UTC\n"
        "    adr:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    kinds = sorted(type(a).__name__ for a in sched._actions)
    assert kinds == ["AdrOfWeekAction", "StalePRAction"]


def test_build_scheduler_stale_pr_only(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    stale_prs:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert [type(a).__name__ for a in sched._actions] == ["StalePRAction"]


def test_build_scheduler_inert_includes_no_new_actions(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    names = [type(a).__name__ for a in sched._actions]
    assert "StalePRAction" not in names and "AdrOfWeekAction" not in names
    assert sched._actions == ()
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "stale_pr or adr" -v`
Expected: FAIL — `assert kinds == [...]` mismatch (`build_scheduler` does not yet add the new actions; the lists come back empty).

- [x] **Step 3: Write minimal implementation**

In `src/babbla/app.py`, update the actions import to add the two new classes:

```python
from babbla.digest.actions import (
    AdrOfWeekAction, PerProjectDigestAction, PersonalDigestAction, QuizAction, StalePRAction,
)
```

Add the `AdrRunner` and `ActionCursorStore` imports:

```python
from babbla.digest.adr import AdrRunner
```

and add `ActionCursorStore` to the `session_store` import group:

```python
from babbla.session_store import (
    ActionCursorStore, ActionTimerStore, DigestStateStore, LobbyThreadStore,
    PersonalDigestStateStore, PersonalSubStore, SessionStore,
)
```

In `build_scheduler`, after the `quiz_bindings` loop and before the `if config.personal_digest is not None:` block, add:

```python
    cursor_store = ActionCursorStore(db_path)
    adr_runner = AdrRunner(AgentRunner(secrets))
    for b in config.stale_pr_bindings():
        actions.append(StalePRAction(
            b, timer_store, get_json, poster,
            b.stale_prs.cadence, b.stale_prs.tz,
            b.stale_prs.threshold_days, b.stale_prs.include_drafts,
        ))
    for b in config.adr_bindings():
        actions.append(AdrOfWeekAction(
            b, timer_store, cursor_store, get_json, adr_runner, poster,
            b.adr.cadence, b.adr.tz, b.adr.dir,
        ))
```

- [x] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (all app tests, including the 3 new ones).

- [x] **Step 5: Commit**

```bash
git add src/babbla/app.py tests/test_app.py
git commit -m "feat: wire StalePRAction and AdrOfWeekAction into build_scheduler"
```

---

### Task 8: Full-suite verification

**Files:** none (verification only).

- [x] **Step 1: Run the entire test suite (excluding live integration)**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS — all prior tests plus the new files green; the count rises by the new tests (~25 new across the 5 new/extended files). Zero failures.

- [x] **Step 2: If anything fails, debug with systematic-debugging**

If a test fails, use the `superpowers:systematic-debugging` skill before changing code. Do not "fix forward" by loosening assertions.

- [x] **Step 3: Final confirmation**

Confirm: no new behavior when neither block is configured (`test_build_scheduler_inert_includes_no_new_actions` green), read-only preserved (no repo writes anywhere), and the full suite is green. Report the final passed count.

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| `ActionCursorStore` (session_store.py) | Task 1 |
| `stale_prs()` + `StalePR` (digest/pulls.py) | Task 2 |
| `StalePRAction` (deterministic, list post, cap+tail, always-advance) | Task 3 |
| `AdrRunner` (digest/adr.py) | Task 4 |
| `AdrOfWeekAction` (rotation, wrap, teaser-fail-no-advance, quiet-when-empty) | Task 5 |
| `StalePRConfig` + `AdrConfig` + parsing + `stale_pr_bindings()`/`adr_bindings()` | Task 6 |
| `channels.yaml` documentation | Task 6 Step 5 |
| `build_scheduler` wiring | Task 7 |
| Inert-when-unconfigured | Tasks 6 (bindings require block+channel) + 7 (test) |
| Read-only preserved | Constraints + Tasks 3/4/5 (no repo writes; stale-PR no model call) |
| Testing matrix (test_pulls, test_stale_pr_action, test_adr_action, test_action_cursor_store, test_digest_runner_poster ext, test_config ext, test_app ext) | Tasks 1–7 |

All spec sections map to a task. No gaps.

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code step shows full code. The cross-task dataclass dependency (Tasks 3/5 need `StalePRConfig`/`AdrConfig` from Task 6) is called out explicitly in each task's note with the repeated dataclass code so tasks are runnable in order.

**3. Type consistency:** `StalePRAction(binding, timer, get_json, poster, cadence, tz, threshold_days, include_drafts)`, `AdrOfWeekAction(binding, timer, cursor, get_json, runner, poster, cadence, tz, dir)`, `AdrRunner.teaser(binding, adr_path)`, `stale_prs(owner, repo, *, now, threshold_days, include_drafts, get_json)`, `ActionCursorStore.get/set` — signatures match across the action classes, the wiring in Task 7, and the tests. `StalePR` field names (`number/title/author/url/idle_days`) are consistent between Task 2's helper and Task 3's `_render`. Labels (`stale-pr:<name>`, `adr:<name>`) match between actions and their tests.
