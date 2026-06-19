# Scheduled Actions Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the digest scheduler into a scheduled-actions framework and deliver three actions — the existing per-project digest (refactored), a shared portfolio digest (fan-out), and a minimal read-only weekly quiz.

**Architecture:** `DigestScheduler` becomes a generic `ActionScheduler` that loops a list of `Action`s, each fully encapsulating its cadence/state/generation/posting behind `maybe_run(now)`. Config gains a subscription `digest:` and a project `quiz:`; new stores hold per-`(channel,project)` watermarks and stateless action timers; the agent generates digest/quiz text over the existing read-only path.

**Tech Stack:** Python 3, `dataclasses`, `typing.Protocol`, PyYAML, sqlite3 + `asyncio.to_thread`, `pytest` (`asyncio_mode=auto`), Claude Agent SDK (only via `AgentRunner.run_ask`, never in tests).

## Global Constraints

- **Read-only by construction.** Every action's only writes are the Slack post and local SQLite; all generation runs through `AgentRunner.run_ask` (read-only agent); no repo writes; the quiz collects no answers/scores.
- **Inert when unconfigured.** No `digest:`, no subscription `digest:`, no `quiz:` → empty action list → the scheduler runs an inert tick loop, byte-for-byte today's no-op behavior.
- **Behavior-preserving refactor.** `PerProjectDigestAction.maybe_run` must reproduce `DigestScheduler._maybe_digest`+`_emit` exactly (due-check, branch bootstrap window, deploy-silent bootstrap, quiet→no-post-no-advance, post-then-advance).
- **Action isolation.** `ActionScheduler.tick` wraps each `action.maybe_run` in `try/except`; one failure never stops the others; a tick failure never crashes the process.
- **Shared digest rules:** per-project anchor reuses each project's own `digest.anchor` (default `branch`, `deploy_workflow=None`); quiet (no project shipped) → no post / no advance; some shipped → one aggregated post (shippers only) + **advance ALL resolved projects' watermarks** + the channel timer; `summarize_shared` context = the first shipper's binding; a subscribed name with no binding is skipped with a `logger.warning`.
- **Quiz rules:** answers posted as a **threaded reply** (`thread_ts` = the questions message `ts`); missing `===ANSWERS===` delimiter → post the whole text as questions, no thread; no scoring, no per-user state.
- **`ProjectBinding` field order:** `(name, owner, repo, visibility, channel_id, dm, digest=None, quiz=None)` — `quiz` is appended LAST so existing positional constructions keep working.
- **Run tests with** `.venv/bin/python -m pytest`. Tests are `asyncio_mode=auto` — async tests need no decorator.
- **`from __future__ import annotations`** at the top of every source module (NOT test files).
- **Secrets hygiene:** never `git add -A`; never stage the operator's real Slack ids in `config/channels.yaml` (Task 14 is controller-handled). Committed content uses placeholders/`null`/comments only.

---

### Task 1: Subscription digest config

**Files:**
- Modify: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `Subscription`, `_parse_subscriptions`, `_CADENCES`, `ZoneInfo` in config.py.
- Produces: `SubscriptionDigest(cadence: str, tz: str)`; `Subscription.digest: SubscriptionDigest | None = None`; `Config.digest_subscriptions() -> tuple[Subscription, ...]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
from babbla.config import SubscriptionDigest

SUBS_DIGEST_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
subscriptions:
  - channel_id: C900
    projects: [MyTV]
    digest:
      cadence: weekly
      tz: Europe/Stockholm
"""


def test_subscription_digest_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_DIGEST_FIXTURE))
    sub = cfg.subscription_for("C900")
    assert sub.digest == SubscriptionDigest(cadence="weekly", tz="Europe/Stockholm")


def test_subscription_without_digest_is_none(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace(
        "    digest:\n      cadence: weekly\n      tz: Europe/Stockholm\n", ""
    )
    cfg = load_config(_write(tmp_path, text))
    assert cfg.subscription_for("C900").digest is None


def test_digest_subscriptions_filters(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_DIGEST_FIXTURE))
    assert tuple(s.channel_id for s in cfg.digest_subscriptions()) == ("C900",)


def test_subscription_digest_bad_cadence_raises(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace("cadence: weekly", "cadence: hourly")
    with pytest.raises(ValueError, match="digest.cadence"):
        load_config(_write(tmp_path, text))


def test_subscription_digest_bad_tz_raises(tmp_path):
    text = SUBS_DIGEST_FIXTURE.replace("tz: Europe/Stockholm", "tz: Mars/Phobos")
    with pytest.raises(ValueError, match="time zone"):
        load_config(_write(tmp_path, text))
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -k subscription_digest -v`
Expected: FAIL (`cannot import name 'SubscriptionDigest'`).

- [ ] **Step 3: Implement** — in `src/babbla/config.py`:

Add the dataclass after `Subscription`:

```python
@dataclass(frozen=True)
class SubscriptionDigest:
    cadence: str
    tz: str
```

Add a parser above `_parse_subscriptions`:

```python
def _parse_cadence_tz(label: str, raw: dict | None, kind: str):
    """Shared cadence+tz parse for subscription digest / quiz. Returns (cadence, tz) or None."""
    if not raw:
        return None
    raw_cadence = raw.get("cadence", "off")
    if raw_cadence is False or str(raw_cadence).strip().lower() == "off":
        return None
    cadence = str(raw_cadence)
    if cadence not in _CADENCES:
        raise ValueError(f"{label}: {kind}.cadence must be one of off|daily|weekly, got {cadence!r}")
    tz = str(raw.get("tz", "UTC"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{label}: {kind}.tz is not a valid time zone: {tz!r}") from exc
    return cadence, tz
```

In `_parse_subscriptions`, build the digest per subscription and pass it in. Replace the
`subscriptions.append(...)` line with:

```python
        ct = _parse_cadence_tz(f"subscription {channel_id}", raw_sub.get("digest"), "digest")
        digest = SubscriptionDigest(cadence=ct[0], tz=ct[1]) if ct else None
        subscriptions.append(
            Subscription(channel_id=channel_id, project_names=names, digest=digest)
        )
```

Add the `digest` field to `Subscription`:

```python
@dataclass(frozen=True)
class Subscription:
    channel_id: str
    project_names: tuple[str, ...]
    digest: SubscriptionDigest | None = None
```

Add the accessor to `Config` (next to `digest_bindings`):

```python
    def digest_subscriptions(self) -> tuple[Subscription, ...]:
        return tuple(s for s in self.subscriptions if s.digest is not None)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/config.py tests/test_config.py
git commit -m "feat: subscription digest config (cadence/tz)"
```

---

### Task 2: Project quiz config

**Files:**
- Modify: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `_parse_cadence_tz` (Task 1), `ProjectBinding`, `load_config`.
- Produces: `QuizConfig(cadence: str, tz: str, count: int = 3)`; `ProjectBinding.quiz: QuizConfig | None = None`; `Config.quiz_bindings() -> tuple[ProjectBinding, ...]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
from babbla.config import QuizConfig

QUIZ_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
    quiz:
      cadence: weekly
      tz: Europe/Stockholm
      count: 5
"""


def test_quiz_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, QUIZ_FIXTURE))
    assert cfg.bindings[0].quiz == QuizConfig(cadence="weekly", tz="Europe/Stockholm", count=5)


def test_quiz_count_defaults_to_three(tmp_path):
    text = QUIZ_FIXTURE.replace("      count: 5\n", "")
    cfg = load_config(_write(tmp_path, text))
    assert cfg.bindings[0].quiz.count == 3


def test_quiz_absent_is_none(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.bindings[0].quiz is None


def test_quiz_bindings_requires_channel(tmp_path):
    cfg = load_config(_write(tmp_path, QUIZ_FIXTURE))
    assert tuple(b.name for b in cfg.quiz_bindings()) == ("MyTV",)
    text = QUIZ_FIXTURE.replace("channel_id: C123", "channel_id: null")
    cfg2 = load_config(_write(tmp_path, text))
    assert cfg2.quiz_bindings() == ()          # no channel to post to


def test_quiz_bad_count_raises(tmp_path):
    text = QUIZ_FIXTURE.replace("count: 5", "count: 0")
    with pytest.raises(ValueError, match="quiz.count"):
        load_config(_write(tmp_path, text))
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -k quiz -v`
Expected: FAIL (`cannot import name 'QuizConfig'`).

- [ ] **Step 3: Implement** — in `src/babbla/config.py`:

Add the dataclass (place above `ProjectBinding` so it reads top-down; with `from __future__ import
annotations` the order is not load-bearing):

```python
@dataclass(frozen=True)
class QuizConfig:
    cadence: str
    tz: str
    count: int = 3
```

Add `quiz` to `ProjectBinding` (appended LAST):

```python
@dataclass(frozen=True)
class ProjectBinding:
    name: str
    owner: str
    repo: str
    visibility: str
    channel_id: str | None
    dm: bool
    digest: DigestConfig | None = None
    quiz: QuizConfig | None = None
```

Add a quiz parser (below `_parse_cadence_tz`):

```python
def _parse_quiz(name: str, raw: dict | None) -> QuizConfig | None:
    ct = _parse_cadence_tz(name, raw, "quiz")
    if ct is None:
        return None
    count = raw.get("count", 3)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"{name}: quiz.count must be a positive integer, got {count!r}")
    return QuizConfig(cadence=ct[0], tz=ct[1], count=count)
```

In `load_config`, add `quiz=...` to the `ProjectBinding(...)` construction:

```python
            digest=_parse_digest(p["name"], p.get("digest")),
            quiz=_parse_quiz(p["name"], p.get("quiz")),
```

Add the accessor to `Config`:

```python
    def quiz_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.quiz is not None and b.channel_id)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all existing + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/config.py tests/test_config.py
git commit -m "feat: project quiz config (cadence/tz/count)"
```

---

### Task 3: SharedDigestStateStore

**Files:**
- Modify: `src/babbla/session_store.py`
- Test: `tests/test_digest_state_store.py`

**Interfaces:**
- Consumes: sqlite/asyncio patterns already in session_store.py.
- Produces: `SharedDigestState(watermarks: dict[str, str | None], last_digest_at: float | None)`; `SharedDigestStateStore(db_path)` with `async get(channel_id) -> SharedDigestState`, `async advance(channel_id, heads: dict[str, str], last_digest_at: float)`, `close()`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_digest_state_store.py`:

```python
from babbla.session_store import SharedDigestState, SharedDigestStateStore


@pytest.fixture
def shared(tmp_path):
    s = SharedDigestStateStore(str(tmp_path / "shared.db"))
    yield s
    s.close()


async def test_shared_unknown_channel_is_empty(shared):
    st = await shared.get("C900")
    assert st == SharedDigestState(watermarks={}, last_digest_at=None)


async def test_shared_advance_roundtrips_multiple_projects(shared):
    await shared.advance("C900", {"MyTV": "h1", "Stream": "h2"}, 1000.0)
    st = await shared.get("C900")
    assert st.watermarks == {"MyTV": "h1", "Stream": "h2"}
    assert st.last_digest_at == 1000.0


async def test_shared_advance_updates_and_keeps_timer_consistent(shared):
    await shared.advance("C900", {"MyTV": "h1", "Stream": "h2"}, 1000.0)
    await shared.advance("C900", {"MyTV": "h3", "Stream": "h2"}, 2000.0)
    st = await shared.get("C900")
    assert st.watermarks == {"MyTV": "h3", "Stream": "h2"}
    assert st.last_digest_at == 2000.0      # consistent across rows


async def test_shared_channels_independent(shared):
    await shared.advance("C900", {"MyTV": "h1"}, 10.0)
    await shared.advance("C901", {"Other": "z1"}, 20.0)
    assert (await shared.get("C900")).watermarks == {"MyTV": "h1"}
    assert (await shared.get("C901")).watermarks == {"Other": "z1"}
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_state_store.py -k shared -v`
Expected: FAIL (`cannot import name 'SharedDigestState'`).

- [ ] **Step 3: Implement** — append to `src/babbla/session_store.py`:

```python
_SHARED_DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_digest_state (
    channel_id     TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (channel_id, project_name)
)
"""


@dataclass(frozen=True)
class SharedDigestState:
    watermarks: dict[str, str | None]
    last_digest_at: float | None


class SharedDigestStateStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_SHARED_DIGEST_SCHEMA)
        self._conn.commit()

    async def get(self, channel_id: str) -> SharedDigestState:
        return await asyncio.to_thread(self._get_sync, channel_id)

    def _get_sync(self, channel_id: str) -> SharedDigestState:
        rows = self._conn.execute(
            "SELECT project_name, watermark_sha, last_digest_at FROM shared_digest_state "
            "WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        watermarks = {r[0]: r[1] for r in rows}
        last = max((r[2] for r in rows if r[2] is not None), default=None)
        return SharedDigestState(watermarks=watermarks, last_digest_at=last)

    async def advance(self, channel_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, channel_id, heads, last_digest_at)

    def _advance_sync(self, channel_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        for project_name, head in heads.items():
            self._conn.execute(
                "INSERT INTO shared_digest_state (channel_id, project_name, watermark_sha, last_digest_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(channel_id, project_name) DO UPDATE SET "
                "watermark_sha = excluded.watermark_sha, last_digest_at = excluded.last_digest_at",
                (channel_id, project_name, head, last_digest_at),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_state_store.py -v`
Expected: PASS (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_digest_state_store.py
git commit -m "feat: SharedDigestStateStore (per-channel,project watermarks)"
```

---

### Task 4: ActionTimerStore

**Files:**
- Modify: `src/babbla/session_store.py`
- Test: `tests/test_digest_state_store.py`

**Interfaces:**
- Produces: `ActionTimerStore(db_path)` with `async get(action_key) -> float | None`, `async advance(action_key, last_fired_at: float)`, `close()`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_digest_state_store.py`:

```python
from babbla.session_store import ActionTimerStore


@pytest.fixture
def timer(tmp_path):
    s = ActionTimerStore(str(tmp_path / "timer.db"))
    yield s
    s.close()


async def test_timer_unknown_key_is_none(timer):
    assert await timer.get("quiz:MyTV") is None


async def test_timer_advance_roundtrips(timer):
    await timer.advance("quiz:MyTV", 1234.0)
    assert await timer.get("quiz:MyTV") == 1234.0


async def test_timer_advance_is_upsert(timer):
    await timer.advance("quiz:MyTV", 1.0)
    await timer.advance("quiz:MyTV", 2.0)
    assert await timer.get("quiz:MyTV") == 2.0
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_state_store.py -k timer -v`
Expected: FAIL (`cannot import name 'ActionTimerStore'`).

- [ ] **Step 3: Implement** — append to `src/babbla/session_store.py`:

```python
_ACTION_TIMER_SCHEMA = """
CREATE TABLE IF NOT EXISTS action_timer (
    action_key    TEXT PRIMARY KEY,
    last_fired_at REAL NOT NULL
)
"""


class ActionTimerStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_ACTION_TIMER_SCHEMA)
        self._conn.commit()

    async def get(self, action_key: str) -> float | None:
        return await asyncio.to_thread(self._get_sync, action_key)

    def _get_sync(self, action_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT last_fired_at FROM action_timer WHERE action_key = ?", (action_key,)
        ).fetchone()
        return row[0] if row else None

    async def advance(self, action_key: str, last_fired_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, action_key, last_fired_at)

    def _advance_sync(self, action_key: str, last_fired_at: float) -> None:
        self._conn.execute(
            "INSERT INTO action_timer (action_key, last_fired_at) VALUES (?, ?) "
            "ON CONFLICT(action_key) DO UPDATE SET last_fired_at = excluded.last_fired_at",
            (action_key, last_fired_at),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_state_store.py -v`
Expected: PASS (existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_digest_state_store.py
git commit -m "feat: ActionTimerStore (last-fired timing for stateless actions)"
```

---

### Task 5: head_for anchor helper

**Files:**
- Modify: `src/babbla/digest/anchors.py`
- Test: `tests/test_digest_anchors.py`

**Interfaces:**
- Produces: `head_for(owner: str, repo: str, anchor: str, deploy_workflow: str | None, *, get_json) -> str | None`.
- Keeps: `current_head(binding, *, get_json) -> str | None` (now a thin wrapper) — unchanged behavior/signature.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_digest_anchors.py`:

```python
from babbla.digest.anchors import head_for


def test_head_for_branch():
    gj = _fake({"/repos/o/r/commits": [{"sha": "head1", "commit": {"message": "x"}}]})
    assert head_for("o", "r", "branch", None, get_json=gj) == "head1"


def test_head_for_deploy():
    gj = _fake({"/repos/o/r/actions/workflows/cicd_prod.yml/runs": {"workflow_runs": [{"head_sha": "dep1"}]}})
    assert head_for("o", "r", "deploy", "cicd_prod.yml", get_json=gj) == "dep1"


def test_head_for_branch_none_when_empty():
    assert head_for("o", "r", "branch", None, get_json=_fake({"/repos/o/r/commits": []})) is None
```

(The existing `current_head` tests must keep passing unchanged.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_anchors.py -k head_for -v`
Expected: FAIL (`cannot import name 'head_for'`).

- [ ] **Step 3: Implement** — in `src/babbla/digest/anchors.py`, replace `current_head` with `head_for` + a thin wrapper:

```python
def head_for(owner: str, repo: str, anchor: str, deploy_workflow: str | None, *, get_json) -> str | None:
    if anchor == "branch":
        commits = get_json(f"/repos/{owner}/{repo}/commits?per_page=1")
        if commits:
            return commits[0]["sha"]
        return None
    # deploy: latest successful run of the configured workflow
    wf = urllib.parse.quote(deploy_workflow, safe="")
    runs = get_json(f"/repos/{owner}/{repo}/actions/workflows/{wf}/runs?status=success&per_page=1")
    items = (runs or {}).get("workflow_runs", [])
    return items[0]["head_sha"] if items else None


def current_head(binding: ProjectBinding, *, get_json) -> str | None:
    d = binding.digest
    return head_for(binding.owner, binding.repo, d.anchor, d.deploy_workflow, get_json=get_json)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_anchors.py -v`
Expected: PASS (existing `current_head` tests + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/anchors.py tests/test_digest_anchors.py
git commit -m "refactor: extract head_for from current_head"
```

---

### Task 6: DigestRunner.summarize_shared

**Files:**
- Modify: `src/babbla/digest/runner.py`
- Test: `tests/test_digest_runner_poster.py`

**Interfaces:**
- Consumes: `_facts` (existing in runner.py), `AgentRunner.run_ask`.
- Produces: `DigestRunner.summarize_shared(context_binding: ProjectBinding, per_project_changes: dict[str, list[Change]]) -> str`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_digest_runner_poster.py`:

```python
async def test_summarize_shared_groups_by_project():
    agent = FakeAgent()
    out = await DigestRunner(agent).summarize_shared(
        _binding(),
        {
            "MyTV": [Change("abc1234", "feat: playback (#7)", 7)],
            "Stream": [Change("def5678", "fix: retry", None)],
        },
    )
    assert out == "SUMMARY"
    p = agent.prompt
    assert "MyTV" in p and "Stream" in p
    assert "abc1234" in p and "feat: playback (#7)" in p
    assert "def5678" in p and "fix: retry" in p
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py -k summarize_shared -v`
Expected: FAIL (`AttributeError: 'DigestRunner' object has no attribute 'summarize_shared'`).

- [ ] **Step 3: Implement** — add the method to `DigestRunner` in `src/babbla/digest/runner.py`:

```python
    async def summarize_shared(
        self, context_binding: ProjectBinding, per_project_changes: dict[str, list[Change]]
    ) -> str:
        sections = "\n\n".join(
            f"## {name}\n{_facts(changes)}" for name, changes in per_project_changes.items()
        )
        prompt = (
            "Write ONE concise Slack digest of what shipped across several projects this period. "
            "Lead with a short cross-project headline, then a section per project. Summarize at a "
            "reader-friendly altitude, group related work, and CITE commits by SHA and PRs by number "
            "as GitHub links. Keep it short and Slack-friendly.\n\n"
            f"{sections}"
        )
        answer = await self._agent.run_ask(prompt, context_binding, None)
        return answer.text
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/runner.py tests/test_digest_runner_poster.py
git commit -m "feat: DigestRunner.summarize_shared (aggregated multi-project digest)"
```

---

### Task 7: QuizRunner

**Files:**
- Create: `src/babbla/digest/quiz.py`
- Test: `tests/test_quiz.py`

**Interfaces:**
- Consumes: `AgentRunner.run_ask`, `ProjectBinding`.
- Produces: `QuizRunner(agent_runner)` with `async generate(binding: ProjectBinding, count: int) -> str`.

- [ ] **Step 1: Write the failing test** — create `tests/test_quiz.py`:

```python
from babbla.agent_runner import CitedAnswer
from babbla.config import ProjectBinding, QuizConfig
from babbla.digest.quiz import QuizRunner


def _binding():
    return ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C0XXXXXXXXX", False,
                          quiz=QuizConfig("weekly", "UTC", 3))


class FakeAgent:
    def __init__(self): self.prompt = None
    async def run_ask(self, text, binding, resume_session_id):
        self.prompt = text
        assert resume_session_id is None
        return CitedAnswer(text="Q1?\n===ANSWERS===\nA1", session_id="ignored")


async def test_quiz_runner_builds_prompt_and_returns_text():
    agent = FakeAgent()
    out = await QuizRunner(agent).generate(_binding(), 3)
    assert out == "Q1?\n===ANSWERS===\nA1"
    p = agent.prompt
    assert "Wkkkkk/MyTV" in p
    assert "3" in p
    assert "===ANSWERS===" in p
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_quiz.py -v`
Expected: FAIL (`No module named 'babbla.digest.quiz'`).

- [ ] **Step 3: Implement** — create `src/babbla/digest/quiz.py`:

```python
from __future__ import annotations

from babbla.config import ProjectBinding


class QuizRunner:
    def __init__(self, agent_runner) -> None:
        self._agent = agent_runner

    async def generate(self, binding: ProjectBinding, count: int) -> str:
        slug = f"{binding.owner}/{binding.repo}"
        prompt = (
            f"Create a short Slack quiz of {count} questions to test a colleague's knowledge of the "
            f"project {slug}. Draw the questions from the project's README, docs/, ADRs, and notable "
            f"history. Number the questions. After the last question, output a line containing exactly "
            f"===ANSWERS=== and then the numbered answers. Keep it concise and Slack-friendly."
        )
        answer = await self._agent.run_ask(prompt, binding, None)
        return answer.text
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_quiz.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/quiz.py tests/test_quiz.py
git commit -m "feat: QuizRunner (read-only repo-grounded quiz generation)"
```

---

### Task 8: SlackPoster returns ts + supports threads

**Files:**
- Modify: `src/babbla/digest/poster.py`
- Test: `tests/test_digest_runner_poster.py`

**Interfaces:**
- Produces: `SlackPoster.post(channel_id, text, thread_ts: str | None = None) -> str` (returns the message `ts`; forwards `thread_ts` when set).

- [ ] **Step 1: Write the failing tests** — replace the existing `test_poster_posts_top_level_message` in `tests/test_digest_runner_poster.py` and add a thread test. Update the `FakeClient` to return a `ts`:

```python
class FakeClient:
    def __init__(self): self.calls = []
    async def chat_postMessage(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "ts": "111.222"}


async def test_poster_posts_top_level_and_returns_ts():
    client = FakeClient()
    ts = await SlackPoster(client).post("C0XXXXXXXXX", "hello")
    assert ts == "111.222"
    assert client.calls == [{"channel": "C0XXXXXXXXX", "text": "hello"}]


async def test_poster_posts_threaded_reply():
    client = FakeClient()
    await SlackPoster(client).post("C0XXXXXXXXX", "answer", thread_ts="111.222")
    assert client.calls == [{"channel": "C0XXXXXXXXX", "text": "answer", "thread_ts": "111.222"}]
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py -k poster -v`
Expected: FAIL (old `post` returns `None`, doesn't forward `thread_ts`).

- [ ] **Step 3: Implement** — replace `src/babbla/digest/poster.py` body:

```python
from __future__ import annotations


class SlackPoster:
    def __init__(self, client) -> None:
        self._client = client

    async def post(self, channel_id: str, text: str, thread_ts: str | None = None) -> str:
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts is not None:
            kwargs["thread_ts"] = thread_ts
        resp = await self._client.chat_postMessage(**kwargs)
        return resp["ts"]
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_runner_poster.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/poster.py tests/test_digest_runner_poster.py
git commit -m "feat: SlackPoster.post returns ts and supports threaded replies"
```

---

### Task 9: ActionScheduler + Action protocol

**Files:**
- Modify: `src/babbla/digest/scheduler.py`
- Test: `tests/test_action_scheduler.py`

**Interfaces:**
- Produces: `Action` (Protocol with `label: str` and `async maybe_run(now)`); `ActionScheduler(*, actions: tuple, now_fn, interval_s: int = 900)` with `async run()` and `async tick(now)`.
- Coexists with the existing `DigestScheduler` (removed in Task 13).

- [ ] **Step 1: Write the failing tests** — create `tests/test_action_scheduler.py`:

```python
from datetime import datetime, timezone
from babbla.digest.scheduler import ActionScheduler

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


class RecordingAction:
    def __init__(self, label): self.label = label; self.ran = []
    async def maybe_run(self, now): self.ran.append(now)


class BoomAction:
    label = "boom"
    async def maybe_run(self, now): raise RuntimeError("kaboom")


async def test_tick_runs_each_action():
    a, b = RecordingAction("a"), RecordingAction("b")
    await ActionScheduler(actions=(a, b), now_fn=lambda: NOW).tick(NOW)
    assert a.ran == [NOW] and b.ran == [NOW]


async def test_tick_isolates_failures():
    good = RecordingAction("good")
    await ActionScheduler(actions=(BoomAction(), good), now_fn=lambda: NOW).tick(NOW)
    assert good.ran == [NOW]          # a raising action does not stop the others


async def test_tick_empty_is_harmless():
    await ActionScheduler(actions=(), now_fn=lambda: NOW).tick(NOW)
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_action_scheduler.py -v`
Expected: FAIL (`cannot import name 'ActionScheduler'`).

- [ ] **Step 3: Implement** — add to `src/babbla/digest/scheduler.py` (keep the existing `DigestScheduler` for now). Add at the top with the other imports: `from typing import Protocol`. Then append:

```python
class Action(Protocol):
    label: str

    async def maybe_run(self, now: datetime) -> None: ...


class ActionScheduler:
    def __init__(self, *, actions: tuple[Action, ...], now_fn, interval_s: int = 900) -> None:
        self._actions = actions
        self._now_fn = now_fn
        self._interval_s = interval_s

    async def run(self) -> None:
        while True:
            try:
                await self.tick(self._now_fn())
            except Exception:  # an action failure must never crash the process
                logger.exception("action tick failed")
            await asyncio.sleep(self._interval_s)

    async def tick(self, now: datetime) -> None:
        for action in self._actions:
            try:
                await action.maybe_run(now)
            except Exception:
                logger.exception("action failed: %s", action.label)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_action_scheduler.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/scheduler.py tests/test_action_scheduler.py
git commit -m "feat: generic ActionScheduler + Action protocol"
```

---

### Task 10: PerProjectDigestAction (refactor of the per-project digest)

**Files:**
- Create: `src/babbla/digest/actions.py`
- Test: `tests/test_digest_scheduler.py` (rewrite to drive the action)

**Interfaces:**
- Consumes: `is_due` (cadence), `current_head` / `changes_between` / `changes_since` (anchors), a `DigestStateStore`-shaped store (`get(channel_id) -> DigestState`, `advance(channel_id, watermark_sha, last_digest_at)`), a `DigestRunner`-shaped runner (`summarize`), a `SlackPoster`-shaped poster (`post`).
- Produces: `PerProjectDigestAction(binding, store, get_json, runner, poster)` with `label` and `async maybe_run(now)`; module-level `_PERIOD`.

This is a behavior-preserving refactor: `maybe_run` reproduces `DigestScheduler._maybe_digest` + `_emit` exactly.

- [ ] **Step 1: Rewrite the test to drive the action** — replace the entire contents of `tests/test_digest_scheduler.py`:

```python
from datetime import datetime, timedelta, timezone
import babbla.digest.actions as A
from babbla.config import DigestConfig, ProjectBinding
from babbla.digest.actions import PerProjectDigestAction
from babbla.session_store import DigestState
from babbla.digest.anchors import Change


def _binding(anchor="branch", wf=None):
    return ProjectBinding("MyTV", "o", "r", "public", "C0XXXXXXXXX", False,
                          DigestConfig("weekly", "UTC", anchor, wf))


class FakeStore:
    def __init__(self, state): self._state = state; self.advanced = []
    async def get(self, channel_id): return self._state
    async def advance(self, channel_id, watermark_sha, last_digest_at):
        self.advanced.append((channel_id, watermark_sha, last_digest_at))


class FakeRunner:
    def __init__(self): self.calls = []
    async def summarize(self, binding, changes, head_sha):
        self.calls.append((binding.name, [c.sha for c in changes], head_sha))
        return f"digest:{head_sha}"


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text)); return "ts"


def _action(binding, state, *, head, changes, monkeypatch):
    store, runner, poster = FakeStore(state), FakeRunner(), FakePoster()
    monkeypatch.setattr(A, "current_head", lambda b, *, get_json: head)
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: changes)
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: changes)
    action = PerProjectDigestAction(binding, store, lambda path: None, runner, poster)
    return action, store, runner, poster


NOW = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)


async def test_not_due_does_nothing(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState("old", NOW.timestamp()), head="new",
        changes=[Change("c", "x", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_first_run_branch_posts_window_and_sets_watermark(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState(None, None), head="H",
        changes=[Change("c1", "feat: a (#1)", 1)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", ["c1"], "H")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:H")]
    assert store.advanced == [("C0XXXXXXXXX", "H", NOW.timestamp())]


async def test_first_run_deploy_is_silent_but_sets_watermark(monkeypatch):
    action, store, runner, poster = _action(
        _binding("deploy", "cicd_prod.yml"), DigestState(None, None), head="D",
        changes=[Change("x", "y", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == []
    assert store.advanced == [("C0XXXXXXXXX", "D", NOW.timestamp())]


async def test_due_and_new_posts_range(monkeypatch):
    last_week = (NOW - timedelta(days=8)).timestamp()
    action, store, runner, poster = _action(
        _binding(), DigestState("old", last_week), head="new",
        changes=[Change("c2", "fix: b", None)], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", ["c2"], "new")]
    assert poster.posts == [("C0XXXXXXXXX", "digest:new")]
    assert store.advanced == [("C0XXXXXXXXX", "new", NOW.timestamp())]


async def test_due_but_no_new_ship_stays_quiet_without_advancing(monkeypatch):
    last_week = (NOW - timedelta(days=8)).timestamp()
    action, store, runner, poster = _action(
        _binding(), DigestState("samehead", last_week), head="samehead",
        changes=[], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_no_ship_signal_skips(monkeypatch):
    action, store, runner, poster = _action(
        _binding(), DigestState(None, None), head=None, changes=[], monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert store.advanced == [] and poster.posts == []
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_scheduler.py -v`
Expected: FAIL (`No module named 'babbla.digest.actions'`).

- [ ] **Step 3: Implement** — create `src/babbla/digest/actions.py`:

```python
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from babbla.digest.anchors import changes_between, changes_since, current_head
from babbla.digest.cadence import is_due

logger = logging.getLogger(__name__)

_PERIOD = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class PerProjectDigestAction:
    def __init__(self, binding, store, get_json, runner, poster) -> None:
        self._b = binding
        self._store = store
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self.label = f"digest:{binding.name}"

    async def maybe_run(self, now: datetime) -> None:
        b = self._b
        d = b.digest
        state = await self._store.get(b.channel_id)
        if not is_due(now, state.last_digest_at, d.cadence, d.tz):
            return
        head = current_head(b, get_json=self._get_json)
        if head is None:
            return  # no ship signal yet
        if state.watermark_sha is None:
            if d.anchor == "branch":
                cutoff = (now - _PERIOD[d.cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
            else:
                changes = []
            await self._emit(changes, head, now)
            return
        if head == state.watermark_sha:
            return  # due, but nothing new shipped — stay quiet, do not advance
        changes = changes_between(b.owner, b.repo, state.watermark_sha, head, get_json=self._get_json)
        await self._emit(changes, head, now)

    async def _emit(self, changes, head: str, now: datetime) -> None:
        if changes:
            text = await self._runner.summarize(self._b, changes, head)
            await self._poster.post(self._b.channel_id, text)
        await self._store.advance(self._b.channel_id, head, now.timestamp())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_scheduler.py -v`
Expected: PASS (6 passed — same behaviors as before the refactor).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_digest_scheduler.py
git commit -m "refactor: per-project digest becomes PerProjectDigestAction"
```

---

### Task 11: SharedDigestAction

**Files:**
- Modify: `src/babbla/digest/actions.py`
- Test: `tests/test_digest_shared.py`

**Interfaces:**
- Consumes: `head_for`, `changes_between`, `changes_since`, `is_due`, `_PERIOD` (this module); a `SharedDigestStateStore`-shaped store (`get(channel_id) -> SharedDigestState`, `advance(channel_id, heads, last_digest_at)`); a runner with `summarize_shared(context_binding, per_project_changes)`; a poster with `post`.
- Produces: `SharedDigestAction(subscription, by_name: dict[str, ProjectBinding], store, get_json, runner, poster)` with `label` and `async maybe_run(now)`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_digest_shared.py`:

```python
from datetime import datetime, timedelta, timezone
import babbla.digest.actions as A
from babbla.config import DigestConfig, ProjectBinding, Subscription, SubscriptionDigest
from babbla.digest.actions import SharedDigestAction
from babbla.session_store import SharedDigestState
from babbla.digest.anchors import Change

NOW = datetime(2026, 6, 18, 12, tzinfo=timezone.utc)
LAST_WEEK = (NOW - timedelta(days=8)).timestamp()


def _b(name, anchor="branch", wf=None, digest=True):
    d = DigestConfig("weekly", "UTC", anchor, wf) if digest else None
    return ProjectBinding(name, "o", name.lower(), "public", f"C_{name}", False, d)


def _sub(names):
    return Subscription("C900", tuple(names), SubscriptionDigest("weekly", "UTC"))


class FakeShared:
    def __init__(self, state): self._state = state; self.advanced = []
    async def get(self, channel_id): return self._state
    async def advance(self, channel_id, heads, last_digest_at):
        self.advanced.append((channel_id, dict(heads), last_digest_at))


class FakeRunner:
    def __init__(self): self.calls = []
    async def summarize_shared(self, context_binding, per_project_changes):
        self.calls.append((context_binding.name, {k: [c.sha for c in v] for k, v in per_project_changes.items()}))
        return "shared-digest"


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text)); return "ts"


def _action(sub, bindings, state, *, heads, changes_map, monkeypatch):
    by_name = {b.name: b for b in bindings}
    store, runner, poster = FakeShared(state), FakeRunner(), FakePoster()
    monkeypatch.setattr(A, "head_for", lambda o, r, anchor, wf, *, get_json: heads.get(r))
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: changes_map.get(r, []))
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: changes_map.get(r, []))
    action = SharedDigestAction(sub, by_name, store, lambda path: None, runner, poster)
    return action, store, runner, poster


async def test_not_due_does_nothing(monkeypatch):
    state = SharedDigestState({"mytv": "old"}, NOW.timestamp())   # same weekly bucket
    action, store, runner, poster = _action(
        _sub(["MyTV"]), [_b("MyTV")], state,
        heads={"mytv": "new"}, changes_map={"mytv": [Change("c", "x", None)]}, monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_first_run_bootstrap_posts_and_advances_all(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1", "stream": "H2"},
        changes_map={"mytv": [Change("a", "feat (#1)", 1)], "stream": [Change("b", "fix", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", {"MyTV": ["a"], "Stream": ["b"]})]
    assert poster.posts == [("C900", "shared-digest")]
    assert store.advanced == [("C900", {"MyTV": "H1", "Stream": "H2"}, NOW.timestamp())]


async def test_all_quiet_no_post_no_advance(monkeypatch):
    state = SharedDigestState({"mytv": "H1", "stream": "H2"}, LAST_WEEK)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1", "stream": "H2"}, changes_map={}, monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and store.advanced == []


async def test_some_shipped_posts_shippers_and_advances_all(monkeypatch):
    state = SharedDigestState({"mytv": "old", "stream": "H2"}, LAST_WEEK)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Stream"]), [_b("MyTV"), _b("Stream")], state,
        heads={"mytv": "H1new", "stream": "H2"},          # stream unchanged
        changes_map={"mytv": [Change("a", "feat", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [("MyTV", {"MyTV": ["a"]})]      # only the shipper
    assert poster.posts == [("C900", "shared-digest")]
    # advance-all: both watermarks move forward + the channel timer
    assert store.advanced == [("C900", {"MyTV": "H1new", "Stream": "H2"}, NOW.timestamp())]


async def test_project_without_digest_defaults_to_branch(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["Plain"]), [_b("Plain", digest=False)], state,
        heads={"plain": "HP"}, changes_map={"plain": [Change("p", "x", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [("C900", "shared-digest")]
    assert store.advanced == [("C900", {"Plain": "HP"}, NOW.timestamp())]


async def test_none_head_project_skipped(monkeypatch):
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Dead"]), [_b("MyTV"), _b("Dead", "deploy", "wf.yml")], state,
        heads={"mytv": "H1", "dead": None},               # Dead has no ship signal
        changes_map={"mytv": [Change("a", "x", None)]},
        monkeypatch=monkeypatch)
    await action.maybe_run(NOW)
    # Dead skipped: not advanced; MyTV posted + advanced
    assert store.advanced == [("C900", {"MyTV": "H1"}, NOW.timestamp())]


async def test_unknown_name_skipped_with_warning(monkeypatch, caplog):
    import logging
    state = SharedDigestState({}, None)
    action, store, runner, poster = _action(
        _sub(["MyTV", "Ghost"]), [_b("MyTV")], state,      # Ghost has no binding
        heads={"mytv": "H1"}, changes_map={"mytv": [Change("a", "x", None)]},
        monkeypatch=monkeypatch)
    with caplog.at_level(logging.WARNING, logger="babbla.digest.actions"):
        await action.maybe_run(NOW)
    assert store.advanced == [("C900", {"MyTV": "H1"}, NOW.timestamp())]
    assert any("Ghost" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_digest_shared.py -v`
Expected: FAIL (`cannot import name 'SharedDigestAction'`).

- [ ] **Step 3: Implement** — add `head_for` to the anchors import line in `src/babbla/digest/actions.py` (it becomes `from babbla.digest.anchors import changes_between, changes_since, current_head, head_for`), then append `SharedDigestAction`:

```python
class SharedDigestAction:
    def __init__(self, subscription, by_name, store, get_json, runner, poster) -> None:
        self._sub = subscription
        self._by_name = by_name
        self._store = store
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self.label = f"shared-digest:{subscription.channel_id}"

    async def maybe_run(self, now: datetime) -> None:
        sub = self._sub
        d = sub.digest
        state = await self._store.get(sub.channel_id)
        if not is_due(now, state.last_digest_at, d.cadence, d.tz):
            return
        heads: dict[str, str] = {}
        per_project_changes: dict[str, list] = {}
        for name in sub.project_names:
            b = self._by_name.get(name)
            if b is None:
                logger.warning("shared digest %s: no binding for project %r", sub.channel_id, name)
                continue
            anchor = b.digest.anchor if b.digest else "branch"
            deploy_workflow = b.digest.deploy_workflow if b.digest else None
            head = head_for(b.owner, b.repo, anchor, deploy_workflow, get_json=self._get_json)
            if head is None:
                continue  # no ship signal — do not advance this project
            heads[name] = head
            wm = state.watermarks.get(name)
            if wm is None:
                if anchor == "branch":
                    cutoff = (now - _PERIOD[d.cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                    changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
                else:
                    changes = []
            elif head == wm:
                changes = []
            else:
                changes = changes_between(b.owner, b.repo, wm, head, get_json=self._get_json)
            if changes:
                per_project_changes[name] = changes
        if not per_project_changes:
            return  # all quiet: no post, no advance
        context_binding = self._by_name[next(iter(per_project_changes))]
        text = await self._runner.summarize_shared(context_binding, per_project_changes)
        await self._poster.post(sub.channel_id, text)
        await self._store.advance(sub.channel_id, heads, now.timestamp())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_digest_shared.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_digest_shared.py
git commit -m "feat: SharedDigestAction (aggregated portfolio digest)"
```

---

### Task 12: QuizAction

**Files:**
- Modify: `src/babbla/digest/actions.py`
- Test: `tests/test_quiz.py`

**Interfaces:**
- Consumes: `is_due` (this module); an `ActionTimerStore`-shaped timer (`get(key) -> float | None`, `advance(key, ts)`); a `QuizRunner`-shaped runner (`generate(binding, count) -> str`); a poster with `post(channel_id, text, thread_ts=None) -> str`.
- Produces: `QuizAction(binding, timer, runner, poster, cadence, tz, count)` with `label` and `async maybe_run(now)`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_quiz.py`:

```python
from datetime import datetime, timedelta, timezone
from babbla.digest.actions import QuizAction

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)


class FakeTimer:
    def __init__(self, last): self._last = last; self.advanced = []
    async def get(self, key): return self._last
    async def advance(self, key, ts): self.advanced.append((key, ts))


class FakeQuizRunner:
    def __init__(self, text): self._text = text; self.calls = []
    async def generate(self, binding, count): self.calls.append((binding.name, count)); return self._text


class FakePoster:
    def __init__(self): self.posts = []
    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text, thread_ts)); return "TS1"


def _quiz_action(last, text, monkeypatch):
    timer, runner, poster = FakeTimer(last), FakeQuizRunner(text), FakePoster()
    action = QuizAction(_binding(), timer, runner, poster, "weekly", "UTC", 3)
    return action, timer, runner, poster


async def test_quiz_not_due_does_nothing(monkeypatch):
    action, timer, runner, poster = _quiz_action(NOW.timestamp(), "Q\n===ANSWERS===\nA", monkeypatch)
    await action.maybe_run(NOW)
    assert runner.calls == [] and poster.posts == [] and timer.advanced == []


async def test_quiz_due_posts_questions_then_answers_in_thread(monkeypatch):
    action, timer, runner, poster = _quiz_action(None, "Q1?\n===ANSWERS===\nA1", monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [
        ("C0XXXXXXXXX", "Q1?", None),
        ("C0XXXXXXXXX", "A1", "TS1"),       # answers threaded under the questions ts
    ]
    assert timer.advanced == [("quiz:MyTV", NOW.timestamp())]


async def test_quiz_without_delimiter_posts_questions_only(monkeypatch):
    action, timer, runner, poster = _quiz_action(None, "just questions, no answers", monkeypatch)
    await action.maybe_run(NOW)
    assert poster.posts == [("C0XXXXXXXXX", "just questions, no answers", None)]
    assert timer.advanced == [("quiz:MyTV", NOW.timestamp())]


async def test_quiz_same_bucket_second_run_not_due(monkeypatch):
    action, timer, runner, poster = _quiz_action((NOW - timedelta(hours=1)).timestamp(),
                                                 "Q\n===ANSWERS===\nA", monkeypatch)
    await action.maybe_run(NOW)                # same weekly bucket as 1h ago
    assert poster.posts == []
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_quiz.py -k "quiz_not_due or thread or delimiter or second_run" -v`
Expected: FAIL (`cannot import name 'QuizAction'`).

- [ ] **Step 3: Implement** — append `QuizAction` to `src/babbla/digest/actions.py`:

```python
class QuizAction:
    def __init__(self, binding, timer, runner, poster, cadence: str, tz: str, count: int) -> None:
        self._b = binding
        self._timer = timer
        self._runner = runner
        self._poster = poster
        self._cadence = cadence
        self._tz = tz
        self._count = count
        self._key = f"quiz:{binding.name}"
        self.label = self._key

    async def maybe_run(self, now: datetime) -> None:
        last = await self._timer.get(self._key)
        if not is_due(now, last, self._cadence, self._tz):
            return
        text = await self._runner.generate(self._b, self._count)
        questions, _, answers = text.partition("===ANSWERS===")
        ts = await self._poster.post(self._b.channel_id, questions.strip())
        if answers.strip():
            await self._poster.post(self._b.channel_id, answers.strip(), thread_ts=ts)
        await self._timer.advance(self._key, now.timestamp())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_quiz.py -v`
Expected: PASS (the runner test from Task 7 + 4 new action tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_quiz.py
git commit -m "feat: QuizAction (post questions + threaded answers)"
```

---

### Task 13: Wire ActionScheduler in app.py; remove DigestScheduler

**Files:**
- Modify: `src/babbla/app.py`
- Modify: `src/babbla/digest/scheduler.py` (remove the now-unused `DigestScheduler` class + its dead imports)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Config.digest_bindings()` / `digest_subscriptions()` / `quiz_bindings()`; the three actions; `ActionScheduler`; `DigestStateStore`, `SharedDigestStateStore`, `ActionTimerStore`; `DigestRunner`, `QuizRunner`, `SlackPoster`, `make_get_json`.
- Produces: `build_scheduler(*, config, secrets, db_path, client) -> ActionScheduler` whose `actions` reflect the config.

- [ ] **Step 1: Write the failing test** — append to `tests/test_app.py`:

```python
from babbla.digest.scheduler import ActionScheduler
from babbla.digest.actions import PerProjectDigestAction, SharedDigestAction, QuizAction
from babbla.app import build_scheduler
from babbla.config import load_config


def test_build_scheduler_assembles_actions(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n"
        "  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C123\n    dm: true\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "    quiz:\n      cadence: weekly\n      tz: UTC\n"
        "  - name: Stream\n    owner: Wkkkkk\n    repo: stream\n    visibility: internal\n"
        "    channel_id: C456\n    dm: false\n"
        "subscriptions:\n"
        "  - channel_id: C900\n    projects: [MyTV, Stream]\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert isinstance(sched, ActionScheduler)
    kinds = sorted(type(a).__name__ for a in sched._actions)
    assert kinds == ["PerProjectDigestAction", "QuizAction", "SharedDigestAction"]


def test_build_scheduler_inert_when_nothing_configured(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert sched._actions == ()
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_app.py -k build_scheduler -v`
Expected: FAIL (`build_scheduler` still returns a `DigestScheduler`; `sched._actions` doesn't exist).

- [ ] **Step 3: Implement** — in `src/babbla/app.py`:

Update the imports:

```python
from babbla.digest.actions import PerProjectDigestAction, QuizAction, SharedDigestAction
from babbla.digest.poster import SlackPoster
from babbla.digest.quiz import QuizRunner
from babbla.digest.runner import DigestRunner
from babbla.digest.scheduler import ActionScheduler
from babbla.session_store import (
    ActionTimerStore, DigestStateStore, LobbyThreadStore, SessionStore, SharedDigestStateStore,
)
```

Replace `build_scheduler`:

```python
def build_scheduler(*, config, secrets: Secrets, db_path: str, client) -> ActionScheduler:
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

Remove the now-unused `DigestScheduler` import line (`from babbla.digest.scheduler import DigestScheduler` if present) — the new import line replaces it.

In `src/babbla/digest/scheduler.py`, delete the entire `DigestScheduler` class and its now-unused module imports (`from babbla.digest.anchors import changes_between, changes_since, current_head`, `from babbla.digest.cadence import is_due`, `from datetime import timedelta`, and the `_PERIOD` constant). Keep `asyncio`, `logging`, `from datetime import datetime`, `from typing import Protocol`, and the `Action` + `ActionScheduler` definitions.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (full suite — `build_scheduler` tests pass; no remaining references to `DigestScheduler`).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/app.py src/babbla/digest/scheduler.py tests/test_app.py
git commit -m "feat: wire ActionScheduler; retire DigestScheduler"
```

---

### Task 14: Document subscription digest + project quiz in channels.yaml template

> **CONTROLLER-HANDLED — do not delegate to a subagent.** The operator's real Slack id lives UNSTAGED in the working-tree `config/channels.yaml`. This task adds a *commented* doc block to the committed template while keeping the operator's real id unstaged. Stage selectively and guard.

**Files:**
- Modify: `config/channels.yaml` (committed template only; working-tree real id stays unstaged)

**Interfaces:**
- Consumes: nothing. Pure documentation (all comments; no active keys added).

- [ ] **Step 1: Build the committed template content** — append this commented block to the end of the HEAD template version (all comments — no real ids, no active keys):

```yaml
# Scheduled actions (optional). Beyond a project's own `digest:` above, you can:
#  - give a SUBSCRIPTION an aggregated digest covering all its projects:
#      subscriptions:
#        - channel_id: C0PORTFOLIO
#          projects: [MyTV]
#          digest:
#            cadence: weekly       # daily | weekly
#            tz: Europe/Stockholm
#  - give a PROJECT a weekly read-only quiz posted to its channel (answers in a thread):
#      quiz:
#        cadence: weekly           # daily | weekly
#        tz: Europe/Stockholm
#        count: 3                  # number of questions (default 3)
```

Procedure (controller runs this; the real id is read from the working tree, never written into any committed file or this plan):

```bash
cd /Users/kunwu/Workspace/babbla
cp config/channels.yaml /tmp/cw.yaml                       # snapshot operator's real-id file
REAL_ID=$(grep -oE 'C[A-Z0-9]{6,}' /tmp/cw.yaml | grep -v '^C0PORTFOLIO$' | sort -u | head -1)
git show HEAD:config/channels.yaml > config/channels.yaml  # template base
cat >> config/channels.yaml <<'EOF'
# Scheduled actions (optional). Beyond a project's own `digest:` above, you can:
#  - give a SUBSCRIPTION an aggregated digest covering all its projects:
#      subscriptions:
#        - channel_id: C0PORTFOLIO
#          projects: [MyTV]
#          digest:
#            cadence: weekly       # daily | weekly
#            tz: Europe/Stockholm
#  - give a PROJECT a weekly read-only quiz posted to its channel (answers in a thread):
#      quiz:
#        cadence: weekly           # daily | weekly
#        tz: Europe/Stockholm
#        count: 3                  # number of questions (default 3)
EOF
git add config/channels.yaml
```

- [ ] **Step 2: Guard — assert the real id is absent from staged content**

```bash
if [ -n "$REAL_ID" ] && git diff --cached config/channels.yaml | grep -q "$REAL_ID"; then
  echo "REAL ID LEAK — aborting"; exit 1
fi
echo "staged content clean of real id"
```

Expected: `staged content clean of real id`.

- [ ] **Step 3: Restore the working tree to the operator's real-id file (+ doc block)**

```bash
cp /tmp/cw.yaml config/channels.yaml
cat >> config/channels.yaml <<'EOF'
# Scheduled actions (optional). Beyond a project's own `digest:` above, you can:
#  - give a SUBSCRIPTION an aggregated digest covering all its projects:
#      subscriptions:
#        - channel_id: C0PORTFOLIO
#          projects: [MyTV]
#          digest:
#            cadence: weekly       # daily | weekly
#            tz: Europe/Stockholm
#  - give a PROJECT a weekly read-only quiz posted to its channel (answers in a thread):
#      quiz:
#        cadence: weekly           # daily | weekly
#        tz: Europe/Stockholm
#        count: 3                  # number of questions (default 3)
EOF
rm -f /tmp/cw.yaml
```

- [ ] **Step 4: Commit the template doc + verify**

```bash
git commit -m "docs: document subscription digest + project quiz in channels.yaml template"
git show HEAD:config/channels.yaml | grep -c 'C0PORTFOLIO'        # expect: 1 (placeholder)
[ -n "$REAL_ID" ] && echo "real id in committed (expect 0): $(git show HEAD:config/channels.yaml | grep -c "$REAL_ID")"
git status --short config/channels.yaml                          # expect: " M" (real id unstaged)
```

After the commit, `git status` must still show `config/channels.yaml` modified+unstaged (operator's real id restored in the working tree).

---

## Notes for the executor

- After all tasks, the full suite should be green: `.venv/bin/python -m pytest -q` (expect the prior 188 passed / 2 skipped plus the new tests, 0 failures).
- Do not push to origin — the operator pushes manually after review.
- Read-only stance unchanged: the only new writes are Slack posts and the two new SQLite stores. No new repo write path; quiz/digest generation runs through `AgentRunner.run_ask`.
- `DigestScheduler` is fully replaced by `ActionScheduler` + actions in Task 13; confirm no remaining imports of `DigestScheduler` (`grep -rn DigestScheduler src tests`).
