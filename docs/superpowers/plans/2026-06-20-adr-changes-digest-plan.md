# ADR Changes Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ADR action's one-per-week rotation with a digest of ADRs changed since the last run — an agent-written summary paragraph plus a per-ADR list — and remove the now-dead rotation cursor.

**Architecture:** A new pure helper `changed_adrs()` lists `docs/adr/NNNN-*.md` and (for non-first runs) keeps files whose latest commit is at/after a cutoff derived from the action's existing `ActionTimerStore` timestamp; first run (no timestamp) backfills all. `AdrRunner.teaser` becomes `AdrRunner.digest` (takes the list, returns summary + list). `AdrOfWeekAction` becomes `AdrDigestAction` (timer-only, no cursor). `ActionCursorStore` + its table + tests + wiring are deleted.

**Tech Stack:** Python 3.14, `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` — async tests need no decorator), stdlib `datetime`/`re`, the read-only `get_json` GitHub path, Claude Agent SDK for the digest (read-only).

## Global Constraints

- **Read-only by construction (ADR 0003):** only writes are the Slack post and the local SQLite timer advance. Detection is read-only `get_json` (contents + commits APIs); the digest runs through the existing read-only `AgentRunner.run_ask` path. No repo writes.
- **Window anchor = the action's `ActionTimerStore` timestamp.** `since = None if last is None else datetime.fromtimestamp(last, tz=timezone.utc)`. First run (`since is None`) = full backfill (all ADRs). No new state store.
- **Advance semantics:** advance the timer after a successful post; also advance on the quiet path (nothing changed / no ADRs). On agent/post failure, do NOT advance — the same window retries next bucket. Never wrap the digest call in try/except inside the action (the scheduler catches it).
- **Detection:** keep `docs/adr` entries matching `^\d{4}-.*\.md$`, sorted by name; paths are `f"{dir}/{name}"`. `since` set → keep a file when its latest commit date `>= since` (catches adds and edits); a file whose commit lookup returns `None`/empty is skipped. `None`/empty contents → `[]`.
- **Config unchanged:** the `adr:` block keeps `cadence`, `tz`, `dir`; `AdrConfig` and `adr_bindings()` are untouched. No cap on the ADR list.
- **Naming:** `AdrOfWeekAction` → `AdrDigestAction`; `AdrRunner.teaser` → `AdrRunner.digest`.
- **Run tests with:** `.venv/bin/python -m pytest` from the repo root.
- **Commit style:** Conventional Commits; match existing history.

## File Structure

- `src/babbla/digest/adr.py` — gains `changed_adrs()` (pure detection helper) and `_parse_ts`/`_ADR_RE`; `AdrRunner.teaser` → `AdrRunner.digest`.
- `src/babbla/digest/actions.py` — `AdrOfWeekAction` → `AdrDigestAction` (rotation/cursor removed, delta logic added); the module-level `_ADR_RE` and `import re` here are removed (detection moved to `adr.py`).
- `src/babbla/app.py` — `build_scheduler` wiring renamed and de-cursored; `ActionCursorStore` import dropped.
- `src/babbla/session_store.py` — `ActionCursorStore` class + `_ACTION_CURSOR_SCHEMA` + `action_cursor` table removed.
- Tests: new `tests/test_adr_changes.py`; rewritten `tests/test_adr_action.py`; updated `tests/test_digest_runner_poster.py` and `tests/test_app.py`; removed `tests/test_action_cursor_store.py`.

---

### Task 1: `changed_adrs()` detection helper

**Files:**
- Modify: `src/babbla/digest/adr.py` (add imports + `_ADR_RE`, `_parse_ts`, `changed_adrs`; leave `AdrRunner` as-is for now)
- Test: `tests/test_adr_changes.py` (create)

**Interfaces:**
- Consumes: a `get_json(path) -> object | None` callable (same shape as `babbla.digest.anchors.make_get_json`).
- Produces: `def changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]` — returns `f"{dir}/{name}"` paths in sorted filename order; `since` is a timezone-aware `datetime` or `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_adr_changes.py`:

```python
from datetime import datetime, timezone

from babbla.digest.adr import changed_adrs

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _contents(*names):
    return [{"name": n} for n in names]


def _commit(date_iso):
    return [{"commit": {"committer": {"date": date_iso}}}]


def test_since_none_returns_all_sorted():
    def gj(path):
        assert "/contents/docs/adr" in path
        return _contents("0002-b.md", "0001-a.md", "README.md")
    out = changed_adrs("o", "r", "docs/adr", since=None, get_json=gj)
    assert out == ["docs/adr/0001-a.md", "docs/adr/0002-b.md"]   # sorted, README excluded


def test_since_filters_by_latest_commit_date():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md", "0002-b.md")
        if "0001-a.md" in path:
            return _commit("2026-06-01T00:00:00Z")   # before since -> excluded
        if "0002-b.md" in path:
            return _commit("2026-06-18T00:00:00Z")   # at/after since -> kept
        raise AssertionError(path)
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    out = changed_adrs("o", "r", "docs/adr", since=since, get_json=gj)
    assert out == ["docs/adr/0002-b.md"]


def test_commit_exactly_at_since_is_included():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return _commit("2026-06-15T00:00:00Z")
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert changed_adrs("o", "r", "docs/adr", since=since, get_json=gj) == ["docs/adr/0001-a.md"]


def test_excludes_non_adr_files():
    def gj(path):
        return _contents("README.md", "index.md", "0003-c.md", "notes.txt")
    out = changed_adrs("o", "r", "docs/adr", since=None, get_json=gj)
    assert out == ["docs/adr/0003-c.md"]


def test_empty_or_none_contents():
    assert changed_adrs("o", "r", "docs/adr", since=None, get_json=lambda p: None) == []
    assert changed_adrs("o", "r", "docs/adr", since=None, get_json=lambda p: []) == []


def test_file_with_no_commit_is_skipped():
    def gj(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return None   # commit lookup unavailable
    since = datetime(2026, 6, 15, tzinfo=timezone.utc)
    assert changed_adrs("o", "r", "docs/adr", since=since, get_json=gj) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_adr_changes.py -v`
Expected: FAIL with `ImportError: cannot import name 'changed_adrs'`.

- [ ] **Step 3: Write minimal implementation**

Replace the imports block at the top of `src/babbla/digest/adr.py` (currently `from __future__ import annotations` then `from babbla.config import ProjectBinding`) with:

```python
from __future__ import annotations

import re
from datetime import datetime

from babbla.config import ProjectBinding

_ADR_RE = re.compile(r"^\d{4}-.*\.md$")


def _parse_ts(value: str) -> datetime:
    # GitHub timestamps are ISO 8601 with a trailing Z (UTC).
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]:
    """Return docs/adr/NNNN-*.md paths changed since `since` (None → all). Read-only.

    `since` is a timezone-aware datetime or None. For a set `since`, a file is kept
    when its latest commit date is at/after `since` (catches adds and edits)."""
    entries = get_json(f"/repos/{owner}/{repo}/contents/{dir}")
    names = sorted(e["name"] for e in (entries or []) if _ADR_RE.match(e.get("name", "")))
    if since is None:
        return [f"{dir}/{n}" for n in names]
    out: list[str] = []
    for n in names:
        commits = get_json(f"/repos/{owner}/{repo}/commits?path={dir}/{n}&per_page=1")
        if not commits:
            continue
        if _parse_ts(commits[0]["commit"]["committer"]["date"]) >= since:
            out.append(f"{dir}/{n}")
    return out
```

(Leave the existing `AdrRunner` class below it unchanged in this task.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_adr_changes.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS (all prior tests + 6 new; nothing else changed yet).

```bash
git add src/babbla/digest/adr.py tests/test_adr_changes.py
git commit -m "feat: add changed_adrs detection helper for ADR digest"
```

---

### Task 2: switch ADR action to the changes digest (atomic rename + cursor removal)

This task replaces rotation with the digest **atomically** — the runner method, the action, the wiring, and the cursor removal must land together so every commit keeps the suite green (`app.py` imports the action by name; renaming the action alone would break the import).

**Files:**
- Modify: `src/babbla/digest/adr.py` (`AdrRunner.teaser` → `AdrRunner.digest`)
- Modify: `src/babbla/digest/actions.py` (`AdrOfWeekAction` → `AdrDigestAction`; remove `import re` + module-level `_ADR_RE`; add `timezone` + `changed_adrs` imports)
- Modify: `src/babbla/app.py` (`build_scheduler` wiring + imports)
- Modify: `src/babbla/session_store.py` (remove `ActionCursorStore` + table)
- Rewrite: `tests/test_adr_action.py`
- Modify: `tests/test_digest_runner_poster.py` (the AdrRunner test)
- Modify: `tests/test_app.py` (renamed action assertions)
- Delete: `tests/test_action_cursor_store.py`

**Interfaces:**
- Consumes: `changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]` (Task 1); `is_due` (already imported in `actions.py`); `ActionTimerStore`-shaped timer; `SlackPoster`-shaped poster; a runner with `async digest(binding, adr_paths) -> str`.
- Produces:
  - `AdrRunner.digest(self, binding: ProjectBinding, adr_paths: list[str]) -> str`
  - `class AdrDigestAction(binding, timer, get_json, runner, poster, cadence, tz, dir)` with `label = f"adr:{binding.name}"`, `project = binding.name`, `async maybe_run(now)`.

- [ ] **Step 1: Rewrite the action test (the failing test)**

Replace the **entire contents** of `tests/test_adr_action.py` with:

```python
from datetime import datetime, timedelta, timezone

from babbla.config import AdrConfig, ProjectBinding
from babbla.digest.actions import AdrDigestAction

NOW = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          adr=AdrConfig("weekly", "UTC", "docs/adr"))


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None, blocks=None):
        self.posts.append((channel_id, text)); return "TS1"


class FakeAdrRunner:
    def __init__(self, text="DIGEST", fail=False):
        self._text = text; self._fail = fail; self.calls = []
    async def digest(self, binding, adr_paths):
        self.calls.append(list(adr_paths))
        if self._fail:
            raise RuntimeError("agent boom")
        return self._text


def _contents(*names):
    return [{"name": n} for n in names]


def _commit(date_iso):
    return [{"commit": {"committer": {"date": date_iso}}}]


def _action(last, reader, runner):
    timer, poster = FakeTimer(last), FakePoster()
    action = AdrDigestAction(_binding(), timer, reader, runner, poster, "weekly", "UTC", "docs/adr")
    return action, timer, poster


async def test_not_due_does_nothing():
    runner = FakeAdrRunner()
    action, timer, poster = _action(NOW.timestamp(), lambda p: _contents("0001-a.md"), runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == []


async def test_first_run_backfills_all_and_advances():
    runner = FakeAdrRunner("DIGEST A")
    reader = lambda p: _contents("0002-b.md", "0001-a.md", "README.md")   # since=None -> no commit calls
    action, timer, poster = _action(None, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [["docs/adr/0001-a.md", "docs/adr/0002-b.md"]]   # all, sorted, README excluded
    assert poster.posts == [("C0XXXXXXXXX", "DIGEST A")]
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_subsequent_run_posts_only_changed():
    runner = FakeAdrRunner("DIGEST B")
    last = (NOW - timedelta(days=7)).timestamp()        # previous weekly bucket -> due; since = 7d ago
    def reader(path):
        if "/contents/" in path:
            return _contents("0001-a.md", "0002-b.md")
        if "0001-a.md" in path:
            return _commit("2026-06-01T00:00:00Z")      # before since -> excluded
        if "0002-b.md" in path:
            return _commit("2026-06-18T00:00:00Z")      # within window -> included
        raise AssertionError(path)
    action, timer, poster = _action(last, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [["docs/adr/0002-b.md"]]
    assert poster.posts == [("C0XXXXXXXXX", "DIGEST B")]
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_nothing_changed_quiet_but_advances():
    runner = FakeAdrRunner()
    last = (NOW - timedelta(days=7)).timestamp()
    def reader(path):
        if "/contents/" in path:
            return _contents("0001-a.md")
        return _commit("2026-06-01T00:00:00Z")          # before since -> nothing changed
    action, timer, poster = _action(last, reader, runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_no_adrs_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, poster = _action(None, lambda p: _contents("README.md"), runner)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_none_contents_quiet_but_advances():
    runner = FakeAdrRunner()
    action, timer, poster = _action(None, lambda p: None, runner)
    await action.maybe_run(NOW)
    assert poster.posts == [] and timer.advanced == [("adr:MyTV", NOW.timestamp())]


async def test_digest_failure_does_not_advance():
    runner = FakeAdrRunner(fail=True)
    action, timer, poster = _action(None, lambda p: _contents("0001-a.md"), runner)
    try:
        await action.maybe_run(NOW)
    except RuntimeError:
        pass
    assert poster.posts == [] and timer.advanced == []
```

- [ ] **Step 2: Update the AdrRunner test in `tests/test_digest_runner_poster.py`**

Replace the existing block (currently the import on line 167 and `test_adr_runner_builds_prompt_and_returns_text`):

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

with:

```python
from babbla.digest.adr import AdrRunner


async def test_adr_runner_digest_builds_prompt_and_returns_text():
    agent = SentinelAgent("DIGEST TEXT")
    out = await AdrRunner(agent).digest(_binding(), ["docs/adr/0001-a.md", "docs/adr/0002-b.md"])
    assert out == "DIGEST TEXT"
    p = agent.prompt
    assert "docs/adr/0001-a.md" in p and "docs/adr/0002-b.md" in p   # all paths in the prompt
    assert "Wkkkkk/MyTV" in p                                        # repo slug for links
    assert "github.com/Wkkkkk/MyTV" in p                             # asks for GitHub links
    assert "summary" in p.lower() and "list" in p.lower()            # summary + list structure
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_adr_action.py tests/test_digest_runner_poster.py -q`
Expected: FAIL — `ImportError: cannot import name 'AdrDigestAction'` (test_adr_action) and `AttributeError`/assertion on `digest` (the runner test).

- [ ] **Step 4: Implement `AdrRunner.digest`**

In `src/babbla/digest/adr.py`, replace the whole `AdrRunner` class (docstring + `teaser` method) with:

```python
class AdrRunner:
    """Thin read-only wrapper around AgentRunner that turns a set of changed ADRs into a
    Slack digest: an opening summary paragraph plus a per-ADR list. Mirrors QuizRunner in shape."""

    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def digest(self, binding: ProjectBinding, adr_paths: list[str]) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        listing = "\n".join(
            f"- {p}  (link: https://github.com/{slug}/blob/HEAD/{p})" for p in adr_paths
        )
        prompt = (
            f"Read each of these Architecture Decision Records in the repository {slug}:\n"
            f"{listing}\n\n"
            f"Write a Slack post in two parts: (1) a short opening summary paragraph "
            f"synthesizing what these ADRs cover and why they matter; then (2) a bulleted "
            f"list with one bullet per ADR — a one-line gloss and its GitHub link. "
            f"Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
```

- [ ] **Step 5: Implement `AdrDigestAction` and drop the old action**

In `src/babbla/digest/actions.py`:

(a) Update the top imports. Change line 4-5 from:

```python
import re
from datetime import datetime, timedelta
```

to:

```python
from datetime import datetime, timedelta, timezone
```

and add, after the existing `from babbla.digest.pulls import stale_prs` line:

```python
from babbla.digest.adr import changed_adrs
```

(b) Replace the entire ADR section — the module-level `_ADR_RE` (currently `_ADR_RE = re.compile(r"^\d{4}-.*\.md$")`) and the whole `AdrOfWeekAction` class (including its `_next` staticmethod) — with:

```python
class AdrDigestAction:
    def __init__(self, binding, timer, get_json, runner, poster,
                 cadence: str, tz: str, dir: str) -> None:
        self._b = binding
        self._timer = timer
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
        since = None if last is None else datetime.fromtimestamp(last, tz=timezone.utc)
        paths = changed_adrs(
            self._b.owner, self._b.repo, self._dir, since=since, get_json=self._get_json
        )
        if not paths:
            # Nothing changed (or no ADRs): stay quiet, advance once per period.
            await self._timer.advance(self._key, now.timestamp())
            return
        # digest failure raises here -> scheduler catches it -> timer NOT advanced
        # -> retries the same window next bucket.
        text = await self._runner.digest(self._b, paths)
        await self._poster.post(self._b.channel_id, text)
        await self._timer.advance(self._key, now.timestamp())
```

- [ ] **Step 6: Update `build_scheduler` wiring in `src/babbla/app.py`**

(a) In the `from babbla.digest.actions import (...)` group, change `AdrOfWeekAction` to `AdrDigestAction`:

```python
from babbla.digest.actions import (
    AdrDigestAction, PerProjectDigestAction, PersonalDigestAction, QuizAction, StalePRAction,
)
```

(b) In the `from babbla.session_store import (...)` group, remove `ActionCursorStore`:

```python
from babbla.session_store import (
    ActionTimerStore, DigestStateStore, LobbyThreadStore,
    PersonalDigestStateStore, PersonalSubStore, SessionStore,
)
```

(c) In `build_scheduler`, delete the line `cursor_store = ActionCursorStore(db_path)` and change the adr loop to drop the cursor argument:

```python
    adr_runner = AdrRunner(AgentRunner(secrets))
    for b in config.stale_pr_bindings():
        actions.append(StalePRAction(
            b, timer_store, get_json, poster,
            b.stale_prs.cadence, b.stale_prs.tz,
            b.stale_prs.threshold_days, b.stale_prs.include_drafts,
        ))
    for b in config.adr_bindings():
        actions.append(AdrDigestAction(
            b, timer_store, get_json, adr_runner, poster,
            b.adr.cadence, b.adr.tz, b.adr.dir,
        ))
```

- [ ] **Step 7: Remove `ActionCursorStore` from `src/babbla/session_store.py`**

Delete the trailing `ActionCursorStore` block — the `_ACTION_CURSOR_SCHEMA` string, the `ActionCursorStore` class, and the two blank lines preceding `_ACTION_CURSOR_SCHEMA` (currently the last ~38 lines of the file, ending the file at `PersonalDigestStateStore.close`).

- [ ] **Step 8: Delete the cursor-store test**

```bash
git rm tests/test_action_cursor_store.py
```

- [ ] **Step 9: Update `tests/test_app.py`**

(a) Change the import (currently `from babbla.digest.actions import StalePRAction, AdrOfWeekAction`) to:

```python
from babbla.digest.actions import StalePRAction, AdrDigestAction
```

(b) Change the assertion in `test_build_scheduler_assembles_stale_pr_and_adr` (currently `assert kinds == ["AdrOfWeekAction", "StalePRAction"]`) to:

```python
    assert kinds == ["AdrDigestAction", "StalePRAction"]
```

(c) Change the assertion in `test_build_scheduler_inert_includes_no_new_actions` (currently `assert "StalePRAction" not in names and "AdrOfWeekAction" not in names`) to:

```python
    assert "StalePRAction" not in names and "AdrDigestAction" not in names
```

- [ ] **Step 10: Verify no dangling references, then run the focused tests**

Run: `grep -rn "AdrOfWeekAction\|ActionCursorStore\|\.teaser(" src tests`
Expected: no matches (all renamed/removed).

Run: `.venv/bin/python -m pytest tests/test_adr_action.py tests/test_digest_runner_poster.py tests/test_app.py -q`
Expected: PASS.

- [ ] **Step 11: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS (the suite shrinks by the 4 removed cursor-store tests and the net ADR test changes; 0 failures).

```bash
git add src/babbla/digest/adr.py src/babbla/digest/actions.py src/babbla/app.py \
        src/babbla/session_store.py tests/test_adr_action.py \
        tests/test_digest_runner_poster.py tests/test_app.py
git commit -m "feat: ADR action posts a digest of recently changed ADRs

Replace one-per-week rotation with a summary + per-ADR list of ADRs
changed since the last run (commit-date window; full backfill on first
run). Rename AdrOfWeekAction -> AdrDigestAction, AdrRunner.teaser ->
.digest, and remove the now-unused ActionCursorStore."
```

---

### Task 3: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite (excluding live integration)**

Run: `.venv/bin/python -m pytest -m "not integration" -q`
Expected: PASS, 0 failures.

- [ ] **Step 2: Confirm the invariants hold**

Confirm by inspection / the passing tests:
- No `AdrOfWeekAction`, `ActionCursorStore`, or `.teaser(` references remain (`grep -rn "AdrOfWeekAction\|ActionCursorStore\|\.teaser(" src tests` → empty).
- Read-only preserved (no repo writes; detection is `get_json`; digest via read-only agent).
- Inert when unconfigured (no `adr:` block → no `AdrDigestAction`).

- [ ] **Step 3: If anything fails, debug with systematic-debugging**

Use the `superpowers:systematic-debugging` skill before changing code; do not loosen assertions.

---

## Self-Review

**1. Spec coverage:**

| Spec item | Task |
|---|---|
| Window anchored on the timer; `since` from `last`; first run backfill | Task 2 (action) |
| `changed_adrs` detection (contents + per-file commit date, regex, sort, None/empty, skip no-commit) | Task 1 |
| `AdrRunner.digest` summary + per-ADR list with links | Task 2 (runner) |
| `AdrOfWeekAction` → `AdrDigestAction` (timer-only, cursor dropped, delta) | Task 2 (action) |
| Quiet-but-advance on nothing-changed / no-ADRs; failure → no advance | Task 2 tests |
| `build_scheduler` rename + cursor drop | Task 2 (wiring) |
| Remove `ActionCursorStore` + table + tests + wiring | Task 2 (steps 7-9) |
| Config unchanged | (no task needed — `AdrConfig`/`adr_bindings()` untouched; covered by existing tests staying green) |
| Read-only preserved | Constraints + Task 3 |

All spec sections map to a task.

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code step shows full code or an exact find/replace.

**3. Type consistency:** `changed_adrs(owner, repo, dir, *, since, get_json) -> list[str]` is defined in Task 1 and called identically in Task 2's action. `AdrRunner.digest(binding, adr_paths)` is defined and called consistently (action passes `paths`; tests pass a list). `AdrDigestAction(binding, timer, get_json, runner, poster, cadence, tz, dir)` matches between the class, the wiring in Task 2 Step 6, and the tests' `_action` helper. The renamed names (`AdrDigestAction`, `.digest`) are used consistently across actions.py, app.py, and all three test files.
