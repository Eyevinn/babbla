# Personal Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an individual persist a set of project interests via a `/babbla` slash command, route their DM Asks among that set, and receive a Personal Digest by DM on a per-user cadence.

**Architecture:** A new per-user write store (`PersonalSubStore`) plus a per-user watermark store (`PersonalDigestStateStore`) in `session_store.py`; a pure command module `personal.py`; orchestrator gains `handle_command` and personal DM-ask routing (reusing the Lobby/Shared-Subscription router verbatim); a `PersonalDigestAction` slots into the existing `ActionScheduler`. Visibility (`public`/`internal` only) is enforced at subscribe-time, ask-time, and digest-send-time.

**Tech Stack:** Python 3.11+, `sqlite3` via `asyncio.to_thread`, `slack_bolt` (async), `pytest` (async tests run without decorators — anyio/asyncio auto mode), `PyYAML`, `zoneinfo`.

## Global Constraints

- **No new dependencies.** Use only what `pyproject.toml` already declares.
- **Cadence values:** config `default_cadence` ∈ `{daily, weekly}`; per-user cadence ∈ `{daily, weekly, off}`.
- **Visibility:** personal subscriptions cover `public`/`internal` only — enforced via `babbla.access.is_open_tier`, never a duplicated tier set.
- **Stores** follow the existing pattern in `src/babbla/session_store.py`: a sync `_..._sync` method wrapped by an `async` method via `asyncio.to_thread`; `sqlite3.connect(db_path, check_same_thread=False)`; `CREATE TABLE IF NOT EXISTS` in `__init__`; a `close()`.
- **Inert when unconfigured:** with no `personal_digest:` block and no user having run `/babbla subscribe`, behavior is byte-for-byte identical to today (DM Asks → the `dm:true` project; no digest; no network at startup for the plain pilot — this is an existing tested invariant in `test_app.py`).
- **Frozen dataclasses** for all new config/value types.
- **Commit after every task.** Commit messages end with the repo's two trailers (see existing history).

---

### Task 1: `is_open_tier` predicate in `access.py`

Extract the open-tier check so the command handler and the digest action share one definition instead of re-deriving `{"public","internal"}`.

**Files:**
- Modify: `src/babbla/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Produces: `is_open_tier(binding: ProjectBinding) -> bool`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_access.py`:

```python
from babbla.access import is_open_tier
from babbla.config import ProjectBinding


def _b(visibility):
    return ProjectBinding("P", "o", "r", visibility, "C1", False)


def test_is_open_tier_public_and_internal_true():
    assert is_open_tier(_b("public")) is True
    assert is_open_tier(_b("internal")) is True


def test_is_open_tier_private_false():
    assert is_open_tier(_b("private")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_access.py -q`
Expected: FAIL with `ImportError: cannot import name 'is_open_tier'`.

- [ ] **Step 3: Add the predicate and use it in `authorize_ask`**

In `src/babbla/access.py`, add after `_OPEN_TIERS`:

```python
def is_open_tier(binding: ProjectBinding) -> bool:
    return binding.visibility in _OPEN_TIERS
```

Then change the tier check inside `authorize_ask` from `if binding.visibility in _OPEN_TIERS:` to:

```python
    if is_open_tier(binding):
        return AccessDecision(allowed=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_access.py -q`
Expected: PASS (existing access tests still pass; two new ones pass).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/access.py tests/test_access.py
git commit -m "refactor: extract is_open_tier predicate in access"
```

---

### Task 2: `PersonalDigestConfig` in `config.py`

Parse an optional top-level `personal_digest:` block and document it in the config template.

**Files:**
- Modify: `src/babbla/config.py`
- Modify: `config/channels.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `PersonalDigestConfig(default_cadence: str, tz: str)` (frozen); `Config.personal_digest: PersonalDigestConfig | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (follow the file's existing `tmp_path` + `load_config` style):

```python
from babbla.config import PersonalDigestConfig


def _write(tmp_path, body):
    p = tmp_path / "channels.yaml"
    p.write_text(body)
    return p


_PROJECT = (
    "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n"
    "    visibility: public\n    channel_id: C1\n    dm: true\n"
)


def test_personal_digest_absent_is_none(tmp_path):
    from babbla.config import load_config
    cfg = load_config(_write(tmp_path, _PROJECT))
    assert cfg.personal_digest is None


def test_personal_digest_parses(tmp_path):
    from babbla.config import load_config
    body = _PROJECT + "personal_digest:\n  default_cadence: daily\n  tz: Europe/Stockholm\n"
    cfg = load_config(_write(tmp_path, body))
    assert cfg.personal_digest == PersonalDigestConfig(default_cadence="daily", tz="Europe/Stockholm")


def test_personal_digest_invalid_cadence_raises(tmp_path):
    import pytest
    from babbla.config import load_config
    body = _PROJECT + "personal_digest:\n  default_cadence: hourly\n  tz: UTC\n"
    with pytest.raises(ValueError, match="default_cadence"):
        load_config(_write(tmp_path, body))


def test_personal_digest_invalid_tz_raises(tmp_path):
    import pytest
    from babbla.config import load_config
    body = _PROJECT + "personal_digest:\n  default_cadence: weekly\n  tz: Mars/Phobos\n"
    with pytest.raises(ValueError, match="time zone"):
        load_config(_write(tmp_path, body))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL with `ImportError: cannot import name 'PersonalDigestConfig'`.

- [ ] **Step 3: Add the dataclass, parser, and wire into `Config`**

In `src/babbla/config.py`, add the dataclass near `SubscriptionDigest`:

```python
@dataclass(frozen=True)
class PersonalDigestConfig:
    default_cadence: str
    tz: str
```

Add the field to `Config` (after `subscriptions`):

```python
    personal_digest: "PersonalDigestConfig | None" = None
```

Add a parser near `_parse_digest`:

```python
def _parse_personal_digest(raw: dict | None) -> "PersonalDigestConfig | None":
    if not raw:
        return None
    cadence = str(raw.get("default_cadence", "weekly"))
    if cadence not in _CADENCES:
        raise ValueError(
            f"personal_digest.default_cadence must be one of daily|weekly, got {cadence!r}"
        )
    tz = str(raw.get("tz", "UTC"))
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"personal_digest.tz is not a valid time zone: {tz!r}") from exc
    return PersonalDigestConfig(default_cadence=cadence, tz=tz)
```

In `load_config`, before the `return Config(...)`, add:

```python
    personal_digest = _parse_personal_digest(raw.get("personal_digest"))
```

and pass it into the `Config(...)` call:

```python
    return Config(
        bindings=bindings,
        lobby_channel_id=lobby_channel_id,
        subscriptions=subscriptions,
        personal_digest=personal_digest,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Document the block in `config/channels.yaml`**

Append a commented example at the end of `config/channels.yaml` (no real values):

```yaml
# Personal Subscriptions (optional). When this block is present, subscribers
# receive a Personal Digest by DM on the cadence each user picks via
# `/babbla digest <daily|weekly|off>` (default below applies until they choose).
# Management (`/babbla subscribe|unsubscribe|list`) works even without this block;
# personal DM-ask routing and the digest require it.
# personal_digest:
#   default_cadence: weekly   # daily | weekly
#   tz: Europe/Stockholm
```

- [ ] **Step 6: Commit**

```bash
git add src/babbla/config.py tests/test_config.py config/channels.yaml
git commit -m "feat: parse optional personal_digest config block"
```

---

### Task 3: `PersonalSubStore` (interests + per-user cadence)

**Files:**
- Modify: `src/babbla/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Produces: `PersonalSubStore(db_path, time_fn=time.time)` with async methods:
  `add(user_id, project) -> None`, `remove(user_id, project) -> None`,
  `list_for(user_id) -> tuple[str, ...]`, `all_user_ids() -> tuple[str, ...]`,
  `get_cadence(user_id) -> str | None`, `set_cadence(user_id, cadence) -> None`, `close()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_store.py`:

```python
from babbla.session_store import PersonalSubStore


async def test_personal_sub_add_list_idempotent(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.add("U1", "MyTV")          # idempotent
    await s.add("U1", "Stream")
    assert await s.list_for("U1") == ("MyTV", "Stream")   # insertion order
    assert await s.list_for("U2") == ()
    s.close()


async def test_personal_sub_remove_idempotent(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.remove("U1", "MyTV")
    await s.remove("U1", "MyTV")       # no error on missing
    assert await s.list_for("U1") == ()
    s.close()


async def test_personal_sub_all_user_ids(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    await s.add("U1", "MyTV")
    await s.add("U2", "Stream")
    assert sorted(await s.all_user_ids()) == ["U1", "U2"]
    s.close()


async def test_personal_cadence_default_none_then_roundtrip(tmp_path):
    s = PersonalSubStore(str(tmp_path / "s.db"))
    assert await s.get_cadence("U1") is None
    await s.set_cadence("U1", "daily")
    assert await s.get_cadence("U1") == "daily"
    await s.set_cadence("U1", "off")
    assert await s.get_cadence("U1") == "off"
    s.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'PersonalSubStore'`.

- [ ] **Step 3: Implement the store**

Append to `src/babbla/session_store.py`:

```python
_PERSONAL_SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_subs (
    user_id      TEXT NOT NULL,
    project_name TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, project_name)
)
"""

_PERSONAL_PREFS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_prefs (
    user_id TEXT PRIMARY KEY,
    cadence TEXT NOT NULL
)
"""


class PersonalSubStore:
    """A user's persisted project interests + their personal-digest cadence."""

    def __init__(self, db_path: str, time_fn: Callable[[], float] = time.time) -> None:
        self._now = time_fn
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_PERSONAL_SUBS_SCHEMA)
        self._conn.execute(_PERSONAL_PREFS_SCHEMA)
        self._conn.commit()

    async def add(self, user_id: str, project: str) -> None:
        await asyncio.to_thread(self._add_sync, user_id, project)

    def _add_sync(self, user_id: str, project: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO personal_subs (user_id, project_name, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, project, self._now()),
        )
        self._conn.commit()

    async def remove(self, user_id: str, project: str) -> None:
        await asyncio.to_thread(self._remove_sync, user_id, project)

    def _remove_sync(self, user_id: str, project: str) -> None:
        self._conn.execute(
            "DELETE FROM personal_subs WHERE user_id = ? AND project_name = ?",
            (user_id, project),
        )
        self._conn.commit()

    async def list_for(self, user_id: str) -> tuple[str, ...]:
        return await asyncio.to_thread(self._list_for_sync, user_id)

    def _list_for_sync(self, user_id: str) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT project_name FROM personal_subs WHERE user_id = ? ORDER BY created_at, project_name",
            (user_id,),
        ).fetchall()
        return tuple(r[0] for r in rows)

    async def all_user_ids(self) -> tuple[str, ...]:
        return await asyncio.to_thread(self._all_user_ids_sync)

    def _all_user_ids_sync(self) -> tuple[str, ...]:
        rows = self._conn.execute("SELECT DISTINCT user_id FROM personal_subs").fetchall()
        return tuple(r[0] for r in rows)

    async def get_cadence(self, user_id: str) -> str | None:
        return await asyncio.to_thread(self._get_cadence_sync, user_id)

    def _get_cadence_sync(self, user_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT cadence FROM personal_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row[0] if row else None

    async def set_cadence(self, user_id: str, cadence: str) -> None:
        await asyncio.to_thread(self._set_cadence_sync, user_id, cadence)

    def _set_cadence_sync(self, user_id: str, cadence: str) -> None:
        self._conn.execute(
            "INSERT INTO personal_prefs (user_id, cadence) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET cadence = excluded.cadence",
            (user_id, cadence),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_session_store.py
git commit -m "feat: PersonalSubStore (per-user interests + cadence)"
```

---

### Task 4: `PersonalDigestStateStore` (per-user-per-project watermark)

A near-exact copy of `SharedDigestStateStore`, keyed by `user_id`, reusing the existing `SharedDigestState` dataclass.

**Files:**
- Modify: `src/babbla/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: `SharedDigestState` (already defined in `session_store.py`).
- Produces: `PersonalDigestStateStore(db_path)` with
  `get(user_id) -> SharedDigestState`, `advance(user_id, heads: dict[str,str], last_digest_at: float) -> None`, `close()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_store.py`:

```python
from babbla.session_store import PersonalDigestStateStore


async def test_personal_digest_state_empty(tmp_path):
    s = PersonalDigestStateStore(str(tmp_path / "s.db"))
    state = await s.get("U1")
    assert state.watermarks == {} and state.last_digest_at is None
    s.close()


async def test_personal_digest_state_advance_roundtrip(tmp_path):
    s = PersonalDigestStateStore(str(tmp_path / "s.db"))
    await s.advance("U1", {"MyTV": "sha1", "Stream": "sha2"}, 1000.0)
    state = await s.get("U1")
    assert state.watermarks == {"MyTV": "sha1", "Stream": "sha2"}
    assert state.last_digest_at == 1000.0
    # isolation between users
    assert (await s.get("U2")).watermarks == {}
    s.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'PersonalDigestStateStore'`.

- [ ] **Step 3: Implement the store**

Append to `src/babbla/session_store.py`:

```python
_PERSONAL_DIGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_digest_state (
    user_id        TEXT NOT NULL,
    project_name   TEXT NOT NULL,
    watermark_sha  TEXT,
    last_digest_at REAL,
    PRIMARY KEY (user_id, project_name)
)
"""


class PersonalDigestStateStore:
    """Per-user-per-project digest watermark; mirrors SharedDigestStateStore."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(_PERSONAL_DIGEST_SCHEMA)
        self._conn.commit()

    async def get(self, user_id: str) -> SharedDigestState:
        return await asyncio.to_thread(self._get_sync, user_id)

    def _get_sync(self, user_id: str) -> SharedDigestState:
        rows = self._conn.execute(
            "SELECT project_name, watermark_sha, last_digest_at FROM personal_digest_state "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        watermarks = {r[0]: r[1] for r in rows}
        last = max((r[2] for r in rows if r[2] is not None), default=None)
        return SharedDigestState(watermarks=watermarks, last_digest_at=last)

    async def advance(self, user_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        await asyncio.to_thread(self._advance_sync, user_id, heads, last_digest_at)

    def _advance_sync(self, user_id: str, heads: dict[str, str], last_digest_at: float) -> None:
        for project_name, head in heads.items():
            self._conn.execute(
                "INSERT INTO personal_digest_state (user_id, project_name, watermark_sha, last_digest_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(user_id, project_name) DO UPDATE SET "
                "watermark_sha = excluded.watermark_sha, last_digest_at = excluded.last_digest_at",
                (user_id, project_name, head, last_digest_at),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/session_store.py tests/test_session_store.py
git commit -m "feat: PersonalDigestStateStore (per-user watermark)"
```

---

### Task 5: `personal.py` — command parsing + reply renderers

Pure module: no I/O, fully unit-testable.

**Files:**
- Create: `src/babbla/personal.py`
- Test: `tests/test_personal.py`

**Interfaces:**
- Produces: `Command(verb: str, arg: str | None = None)` (frozen); `parse_command(text: str) -> Command`;
  renderers `render_subscribed(name)`, `render_unsubscribed(name)`, `render_unknown_project(available: Sequence[str])`,
  `render_private_refused(name)`, `render_list(names: Sequence[str], cadence: str)`, `render_digest_set(cadence)`, `render_help()`.
- `verb` is one of `"subscribe" | "unsubscribe" | "list" | "digest" | "help"`. For `subscribe`/`unsubscribe`, `arg` is the project name. For `digest`, `arg` is a cadence in `{daily, weekly, off}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_personal.py`:

```python
from babbla import personal
from babbla.personal import Command


def test_parse_empty_is_list():
    assert personal.parse_command("") == Command("list")
    assert personal.parse_command("   ") == Command("list")


def test_parse_subscribe_and_unsubscribe():
    assert personal.parse_command("subscribe MyTV") == Command("subscribe", "MyTV")
    assert personal.parse_command("unsubscribe MyTV") == Command("unsubscribe", "MyTV")


def test_parse_subscribe_without_arg_is_help():
    assert personal.parse_command("subscribe") == Command("help")


def test_parse_list_aliases():
    assert personal.parse_command("list") == Command("list")
    assert personal.parse_command("subscriptions") == Command("list")


def test_parse_digest_valid_and_invalid():
    assert personal.parse_command("digest daily") == Command("digest", "daily")
    assert personal.parse_command("digest off") == Command("digest", "off")
    assert personal.parse_command("digest hourly") == Command("help")
    assert personal.parse_command("digest") == Command("help")


def test_parse_unknown_is_help():
    assert personal.parse_command("frobnicate") == Command("help")


def test_parse_is_case_insensitive_on_verb():
    assert personal.parse_command("SUBSCRIBE MyTV") == Command("subscribe", "MyTV")


def test_render_list_with_and_without_subs():
    assert "MyTV" in personal.render_list(["MyTV"], "weekly")
    assert "weekly" in personal.render_list(["MyTV"], "weekly")
    assert "paused" in personal.render_list(["MyTV"], "off")
    assert "subscribe" in personal.render_list([], "weekly").lower()


def test_render_private_and_unknown():
    assert "private" in personal.render_private_refused("Secret").lower()
    assert "MyTV" in personal.render_unknown_project(["MyTV", "Stream"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_personal.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'babbla.personal'`.

- [ ] **Step 3: Implement `personal.py`**

Create `src/babbla/personal.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

_CADENCES = {"daily", "weekly", "off"}


@dataclass(frozen=True)
class Command:
    verb: str               # subscribe | unsubscribe | list | digest | help
    arg: str | None = None  # project name (sub/unsub) or cadence (digest)


def parse_command(text: str) -> Command:
    tokens = (text or "").split()
    if not tokens:
        return Command("list")
    verb = tokens[0].lower()
    if verb in ("subscribe", "unsubscribe"):
        if len(tokens) < 2:
            return Command("help")
        return Command(verb, tokens[1])
    if verb in ("list", "subscriptions"):
        return Command("list")
    if verb == "digest":
        if len(tokens) >= 2 and tokens[1].lower() in _CADENCES:
            return Command("digest", tokens[1].lower())
        return Command("help")
    return Command("help")


def render_subscribed(name: str) -> str:
    return (
        f"✅ Subscribed to *{name}*. I'll route your DM questions to it and "
        "include it in your personal digest."
    )


def render_unsubscribed(name: str) -> str:
    return f"Unsubscribed from *{name}*."


def render_unknown_project(available: Sequence[str]) -> str:
    listing = ", ".join(f"*{n}*" for n in available) or "(none yet)"
    return f"🤔 I don't know that project. I can follow: {listing}."


def render_private_refused(name: str) -> str:
    return (
        f"🔒 *{name}* is private — personal subscriptions only cover "
        "public/internal projects."
    )


def render_list(names: Sequence[str], cadence: str) -> str:
    if not names:
        return "You don't follow any projects yet. Use `/babbla subscribe <project>` to start."
    listing = ", ".join(f"*{n}*" for n in names)
    cad = "paused" if cadence == "off" else cadence
    return f"You follow: {listing}.\nPersonal digest: *{cad}*."


def render_digest_set(cadence: str) -> str:
    if cadence == "off":
        return "Personal digest *paused*. Your subscriptions are kept for Asks."
    return f"Personal digest set to *{cadence}*."


def render_help() -> str:
    return (
        "*Personal subscriptions* — manage what I follow for you:\n"
        "• `/babbla subscribe <project>` — follow a project\n"
        "• `/babbla unsubscribe <project>` — stop following\n"
        "• `/babbla list` — show your projects and digest cadence\n"
        "• `/babbla digest daily|weekly|off` — set your personal-digest cadence"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat: personal command parsing + reply renderers"
```

---

### Task 6: Orchestrator `handle_command`

Map a parsed `Command` to store writes and return the reply text. Enforces visibility check #1 (subscribe-time).

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `PersonalSubStore` (Task 3), `personal` module (Task 5), `is_open_tier` (Task 1).
- Produces: `Orchestrator.__init__` gains `personal_store=None, personal_default_cadence="weekly"`;
  `handle_command(user_id: str, text: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py`:

```python
from babbla.session_store import PersonalSubStore


def _config_two():
    pub = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", True)
    priv = ProjectBinding("Secret", "o", "secret", "private", "C2", False)
    return Config(bindings=(pub, priv))


@pytest.fixture
def psub(tmp_path):
    s = PersonalSubStore(str(tmp_path / "p.db"))
    yield s
    s.close()


async def test_handle_command_subscribe_known(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe MyTV")
    assert "MyTV" in reply
    assert await psub.list_for("U1") == ("MyTV",)


async def test_handle_command_subscribe_unknown_writes_nothing(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Ghost")
    assert "don't know" in reply.lower()
    assert await psub.list_for("U1") == ()


async def test_handle_command_subscribe_private_refused(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert "private" in reply.lower()
    assert await psub.list_for("U1") == ()


async def test_handle_command_unsubscribe(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    await orch.handle_command("U1", "unsubscribe MyTV")
    assert await psub.list_for("U1") == ()


async def test_handle_command_digest_sets_cadence(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "digest daily")
    assert "daily" in reply
    assert await psub.get_cadence("U1") == "daily"


async def test_handle_command_list_shows_default_cadence(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store,
                        personal_store=psub, personal_default_cadence="weekly")
    await psub.add("U1", "MyTV")
    reply = await orch.handle_command("U1", "list")
    assert "MyTV" in reply and "weekly" in reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: FAIL (`TypeError` on unexpected `personal_store` kwarg, or `AttributeError: handle_command`).

- [ ] **Step 3: Implement constructor params and `handle_command`**

In `src/babbla/orchestrator.py`, update the imports at the top:

```python
from babbla.access import Surface, authorize_ask, is_open_tier
from babbla.agent_runner import CitedAnswer
from babbla import lobby, personal, subscriptions
from babbla.config import Config, ProjectBinding
```

Update `__init__` signature and body:

```python
    def __init__(
        self, config: Config, runner, store, *,
        catalog=(), classify_fn=None, lobby_store=None,
        personal_store=None, personal_default_cadence: str = "weekly",
    ) -> None:
        self._config = config
        self._runner = runner
        self._store = store
        self._catalog = catalog
        self._classify_fn = classify_fn
        self._lobby_store = lobby_store
        self._personal_store = personal_store
        self._personal_default_cadence = personal_default_cadence
        self._locks: dict[str, asyncio.Lock] = {}
```

Add the method (e.g. after `_resolve`):

```python
    async def handle_command(self, user_id: str, text: str) -> str:
        cmd = personal.parse_command(text)
        if cmd.verb == "help":
            return personal.render_help()
        if cmd.verb == "list":
            names = await self._personal_store.list_for(user_id)
            cadence = await self._personal_store.get_cadence(user_id) or self._personal_default_cadence
            return personal.render_list(names, cadence)
        if cmd.verb == "digest":
            await self._personal_store.set_cadence(user_id, cmd.arg)
            return personal.render_digest_set(cmd.arg)
        if cmd.verb == "subscribe":
            binding = next((b for b in self._config.bindings if b.name == cmd.arg), None)
            if binding is None:
                return personal.render_unknown_project([b.name for b in self._config.bindings])
            if not is_open_tier(binding):
                return personal.render_private_refused(binding.name)
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
        # unsubscribe
        await self._personal_store.remove(user_id, cmd.arg)
        return personal.render_unsubscribed(cmd.arg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: PASS (existing orchestrator tests still pass — the new kwargs default to `None`/`"weekly"`).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator handle_command for personal subscriptions"
```

---

### Task 7: Personal DM-ask routing in `handle_ask`

Route a DM Ask among the user's subscribed set (reusing `_resolve_subscription`); empty set falls back to today's `dm:true` path. Enforces visibility check #2 (`Surface.DM`).

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `subscriptions.entries_for`, `self._resolve_subscription`, `self._lobby_store`, `authorize_ask(_, Surface.DM)`.
- Produces: `handle_ask(..., user_id: str | None = None)`; private helper `_handle_personal_ask(*, text, thread_ts, entries)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py` (a small fake classifier + catalog, mirroring the subscription tests):

```python
from babbla.lobby import CatalogEntry


def _catalog_two():
    pub = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", True)
    other = ProjectBinding("Stream", "o", "stream", "internal", "C2", False)
    return (CatalogEntry(pub, None), CatalogEntry(other, None))


async def test_dm_empty_subs_falls_back_to_dm_true(store, psub):
    # CONFIG has the single dm:true MyTV binding (module-level in this file)
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub, catalog=_catalog_two())
    runner = orch._runner
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert runner.calls[0][1].name == "MyTV"   # fell back to dm:true project
    assert ans.text == "answer to q"


async def test_dm_size1_answers_directly_no_classifier(store, psub):
    classifier_calls = []
    async def classify_fn(text, catalog):
        classifier_calls.append(text)
        return "Stream"
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub,
                        catalog=_catalog_two(), classify_fn=classify_fn,
                        lobby_store=_FakeLobbyStore())
    await psub.add("U1", "Stream")
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert orch._runner.calls[0][1].name == "Stream"
    assert classifier_calls == []              # size-1 shortcut: no routing call
    assert ans.text.endswith("answer to q")    # no pointer suffix


async def test_dm_two_subs_routes_via_classifier(store, psub):
    async def classify_fn(text, catalog):
        return "Stream"
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub,
                        catalog=_catalog_two(), classify_fn=classify_fn,
                        lobby_store=_FakeLobbyStore())
    await psub.add("U1", "MyTV")
    await psub.add("U1", "Stream")
    await orch.handle_ask(text="why HLS", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert orch._runner.calls[0][1].name == "Stream"
```

Add this fake near the top of the test file if not already present:

```python
class _FakeLobbyStore:
    def __init__(self):
        self._d = {}
    async def get(self, thread_ts):
        return self._d.get(thread_ts)
    async def put(self, thread_ts, project):
        self._d[thread_ts] = project
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: FAIL (`handle_ask` got an unexpected keyword `user_id`).

- [ ] **Step 3: Implement the routing branch and helper**

In `handle_ask`, change the signature and add the personal branch:

```python
    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool,
        user_id: str | None = None,
    ) -> CitedAnswer:
        if not is_dm:
            sub = self._config.subscription_for(channel_id)
            if sub is not None:
                return await self._handle_subscription_ask(
                    text=text, thread_ts=thread_ts, sub=sub
                )
        elif self._personal_store is not None and user_id is not None and self._catalog:
            names = await self._personal_store.list_for(user_id)
            entries = subscriptions.entries_for(self._catalog, names) if names else ()
            if entries:
                return await self._handle_personal_ask(
                    text=text, thread_ts=thread_ts, entries=entries
                )
        binding = self._resolve(channel_id, is_dm)
        surface = Surface.DM if is_dm else Surface.CHANNEL
        decision = authorize_ask(binding, surface)
        if not decision.allowed:
            return CitedAnswer(text=decision.pointer, session_id=None)
        try:
            async with self._lock_for(thread_ts):
                resume_session_id = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, binding, resume_session_id)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
            return answer
        finally:
            self._release_lock(thread_ts)
```

Add the helper (next to `_handle_subscription_ask`):

```python
    async def _handle_personal_ask(self, *, text: str, thread_ts: str, entries) -> CitedAnswer:
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_subscription(text, thread_ts, entries)
                if entry is None:
                    return CitedAnswer(
                        text=subscriptions.subscription_clarify(entries), session_id=None
                    )
                decision = authorize_ask(entry.binding, Surface.DM)   # denies private (flip-after-subscribe)
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
                await self._lobby_store.put(thread_ts, entry.binding.name)
                resume = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, entry.binding, resume)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return answer                                          # no pointer suffix — already in a DM
            finally:
                self._release_lock(thread_ts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -q`
Expected: PASS (existing DM test `test_dm_resolves_via_for_dm` still passes — it passes no `user_id`, so the personal branch is skipped).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: personal DM-ask routing among a user's subscribed set"
```

---

### Task 8: Slack adapter — thread `user_id` + register the `/babbla` command

**Files:**
- Modify: `src/babbla/slack_adapter.py`
- Test: `tests/test_slack_adapter.py`

**Interfaces:**
- Consumes: `orchestrator.handle_ask(..., user_id=...)`, `orchestrator.handle_command(user_id, text) -> str`.
- Produces: `process_ask(..., user_id: str | None = None)`; a `@app.command("/babbla")` handler registered by `register_handlers`.

- [ ] **Step 1: Update existing fakes, then write the failing tests**

In `tests/test_slack_adapter.py`, update `FakeOrch.handle_ask` to accept `user_id` and add a `handle_command`; add a `command` decorator to `FakeApp`:

```python
class FakeOrch:
    def __init__(self, answer=None, exc=None):
        self.answer = answer
        self.exc = exc
        self.calls = []
        self.command_calls = []

    async def handle_ask(self, *, text, thread_ts, channel_id, is_dm, user_id=None):
        self.calls.append({"text": text, "thread_ts": thread_ts, "channel_id": channel_id,
                           "is_dm": is_dm, "user_id": user_id})
        if self.exc:
            raise self.exc
        return self.answer

    async def handle_command(self, user_id, text):
        self.command_calls.append((user_id, text))
        return "command-reply"
```

Update **both** `FakeApp` definitions in this file (there are two) to add a `command` decorator:

```python
    def command(self, name):
        def deco(fn):
            self.handlers[("command", name)] = fn
            return fn
        return deco
```

Add these tests:

```python
async def test_dm_message_passes_user_id():
    app = FakeApp()
    client = FakeClient()
    orch = FakeOrch(answer=CitedAnswer(text="ok", session_id="s1"))
    register_handlers(app, orch)
    event = {"text": "q", "channel": "D1", "ts": "t2", "channel_type": "im", "user": "U7"}
    await app.handlers["message"](event=event, client=client)
    await asyncio.sleep(0)
    assert orch.calls[0]["user_id"] == "U7"
    assert orch.calls[0]["is_dm"] is True


async def test_babbla_command_acks_and_responds():
    app = FakeApp()
    orch = FakeOrch()
    register_handlers(app, orch)
    acked = []
    responded = []
    async def ack():
        acked.append(True)
    async def respond(text):
        responded.append(text)
    await app.handlers[("command", "/babbla")](
        ack=ack, command={"user_id": "U7", "text": "subscribe MyTV"}, respond=respond
    )
    assert acked == [True]
    assert orch.command_calls == [("U7", "subscribe MyTV")]
    assert responded == ["command-reply"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_slack_adapter.py -q`
Expected: FAIL (`KeyError: ('command', '/babbla')` / `process_ask` doesn't pass `user_id`).

- [ ] **Step 3: Thread `user_id` and register the command**

In `src/babbla/slack_adapter.py`, update `process_ask`:

```python
async def process_ask(
    *,
    text: str,
    channel: str,
    thread_ts: str,
    is_dm: bool,
    client,
    orchestrator: Orchestrator,
    user_id: str | None = None,
) -> None:
    placeholder = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=PLACEHOLDER)
    ts = placeholder["ts"]
    try:
        answer = await orchestrator.handle_ask(
            text=text, thread_ts=thread_ts, channel_id=channel, is_dm=is_dm, user_id=user_id
        )
        await client.chat_update(channel=channel, ts=ts, text=answer.text)
    except Exception:
        logger.exception("Ask failed for thread %s in channel %s", thread_ts, channel)
        await client.chat_update(channel=channel, ts=ts, text=ERROR_TEXT)
```

In the `message` handler inside `register_handlers`, pass the user id:

```python
    @app.event("message")
    async def _on_message(event, client):
        if event.get("channel_type") != "im" or event.get("bot_id"):
            return
        thread_ts = event.get("thread_ts") or event["ts"]
        _spawn(
            process_ask(
                text=(event.get("text") or "").strip(),
                channel=event["channel"],
                thread_ts=thread_ts,
                is_dm=True,
                client=client,
                orchestrator=orchestrator,
                user_id=event.get("user"),
            )
        )
```

Add the command handler at the end of `register_handlers` (after the `message` handler):

```python
    @app.command("/babbla")
    async def _on_command(ack, command, respond):
        await ack()
        try:
            reply = await orchestrator.handle_command(command["user_id"], command.get("text", ""))
        except Exception:
            logger.exception("/babbla command failed for user %s", command.get("user_id"))
            reply = "⚠️ Couldn't update your subscriptions right now — please try again shortly."
        await respond(reply)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_slack_adapter.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/slack_adapter.py tests/test_slack_adapter.py
git commit -m "feat: thread user_id into DM asks + register /babbla command"
```

---

### Task 9: `SlackPoster.open_dm`

**Files:**
- Modify: `src/babbla/digest/poster.py`
- Test: `tests/test_digest_runner_poster.py`

**Interfaces:**
- Produces: `SlackPoster.open_dm(user_id: str) -> str` (returns the DM channel id).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_digest_runner_poster.py`:

```python
from babbla.digest.poster import SlackPoster


async def test_open_dm_returns_channel_id():
    class FakeClient:
        def __init__(self):
            self.opened = None
        async def conversations_open(self, *, users):
            self.opened = users
            return {"channel": {"id": "D123"}}
    client = FakeClient()
    poster = SlackPoster(client)
    assert await poster.open_dm("U7") == "D123"
    assert client.opened == "U7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_digest_runner_poster.py -q`
Expected: FAIL with `AttributeError: 'SlackPoster' object has no attribute 'open_dm'`.

- [ ] **Step 3: Implement `open_dm`**

Add to `SlackPoster` in `src/babbla/digest/poster.py`:

```python
    async def open_dm(self, user_id: str) -> str:
        resp = await self._client.conversations_open(users=user_id)
        return resp["channel"]["id"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_digest_runner_poster.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/poster.py tests/test_digest_runner_poster.py
git commit -m "feat: SlackPoster.open_dm (resolve a user's DM channel)"
```

---

### Task 10: `PersonalDigestAction`

Per-user fan-out mirroring `SharedDigestAction`, delivered by DM, filtered to open-tier projects at send time (visibility check #3).

**Files:**
- Modify: `src/babbla/digest/actions.py`
- Test: `tests/test_personal_digest.py`

**Interfaces:**
- Consumes: `PersonalSubStore`, `PersonalDigestStateStore`, `is_open_tier`, `is_due`, `head_for`, `changes_since`, `changes_between`, `_PERIOD`, `runner.summarize_shared`, `poster.open_dm`, `poster.post`.
- Produces: `PersonalDigestAction(personal_store, state_store, by_name, get_json, runner, poster, default_cadence, tz)` with `label = "personal-digest"` and `async maybe_run(now)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_personal_digest.py`:

```python
from datetime import datetime, timezone

from babbla.config import ProjectBinding
from babbla.digest.actions import PersonalDigestAction
from babbla.session_store import PersonalSubStore, PersonalDigestStateStore

NOW = datetime(2026, 6, 19, 12, tzinfo=timezone.utc)

MYTV = ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False)
SECRET = ProjectBinding("Secret", "o", "secret", "private", "C2", False)
BY_NAME = {"MyTV": MYTV, "Secret": SECRET}


class FakeRunner:
    async def summarize_shared(self, binding, per_project_changes):
        return "digest text"


class FakePoster:
    def __init__(self, fail_open=False):
        self.posts = []
        self.opened = []
        self.fail_open = fail_open
    async def open_dm(self, user_id):
        self.opened.append(user_id)
        if self.fail_open:
            raise RuntimeError("cannot open dm")
        return f"D-{user_id}"
    async def post(self, channel_id, text, thread_ts=None):
        self.posts.append((channel_id, text))
        return "ts-1"


def _get_json_with_commits(head_sha, commits):
    # Returns head for branch anchor (the default repo HEAD) and a commit list.
    def get_json(path):
        if path.endswith("/commits") or "/commits?" in path or "since=" in path:
            return commits
        if "/commits/" in path:
            return {"sha": head_sha}
        # default branch ref / repo head
        return {"commit": {"sha": head_sha}, "object": {"sha": head_sha}, "default_branch": "main"}
    return get_json


async def _store_pair(tmp_path):
    subs = PersonalSubStore(str(tmp_path / "p.db"))
    state = PersonalDigestStateStore(str(tmp_path / "p.db"))
    return subs, state


async def test_no_subscribers_is_noop(tmp_path):
    subs, state = await _store_pair(tmp_path)
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", []), FakeRunner(), poster,
                                  "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []
    subs.close(); state.close()


async def test_paused_user_skipped(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.set_cadence("U1", "off")
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []
    subs.close(); state.close()


async def test_private_project_filtered_at_send_time(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "Secret")        # private — must never be summarized to a DM
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []              # no changes gathered → no DM
    subs.close(); state.close()


async def test_one_user_failure_does_not_abort_others(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add("U2", "MyTV")
    poster = FakePoster(fail_open=True)    # open_dm raises for everyone
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(), poster, "weekly", "UTC")
    await action.maybe_run(NOW)            # must not raise
    assert sorted(poster.opened) == ["U1", "U2"]   # both attempted
    subs.close(); state.close()
```

> **Note for the implementer:** the exact `get_json` shape depends on the anchor helpers in
> `src/babbla/digest/anchors.py`. Before writing the action, open `anchors.py` and confirm the
> argument/return contract of `head_for(owner, repo, anchor, deploy_workflow, *, get_json)`,
> `changes_since(owner, repo, cutoff, *, get_json)`, and `changes_between(owner, repo, base, head, *, get_json)`.
> Adjust the `_get_json_with_commits` fake above so `head_for` returns `"sha1"` and `changes_since`
> returns a non-empty list — match how `tests/test_digest_shared.py` fakes them (copy that file's
> fake `get_json` if it differs from the sketch above). The behavioral assertions
> (paused skip, private filtered, failure isolation, no-subscribers no-op) are what matter.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_personal_digest.py -q`
Expected: FAIL with `ImportError: cannot import name 'PersonalDigestAction'`.

- [ ] **Step 3: Implement the action**

In `src/babbla/digest/actions.py`, add the import at the top:

```python
from babbla.access import is_open_tier
```

Append the class:

```python
class PersonalDigestAction:
    def __init__(self, personal_store, state_store, by_name, get_json, runner, poster,
                 default_cadence: str, tz: str) -> None:
        self._subs = personal_store
        self._state = state_store
        self._by_name = by_name
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self._default_cadence = default_cadence
        self._tz = tz
        self.label = "personal-digest"

    async def maybe_run(self, now: datetime) -> None:
        for user_id in await self._subs.all_user_ids():
            try:
                await self._maybe_run_user(user_id, now)
            except Exception:  # one user's failure must not abort the rest
                logger.exception("personal digest failed for user %s", user_id)

    async def _maybe_run_user(self, user_id: str, now: datetime) -> None:
        cadence = await self._subs.get_cadence(user_id) or self._default_cadence
        if cadence == "off":
            return
        state = await self._state.get(user_id)
        if not is_due(now, state.last_digest_at, cadence, self._tz):
            return
        names = await self._subs.list_for(user_id)
        bindings = [
            self._by_name[n] for n in names
            if n in self._by_name and is_open_tier(self._by_name[n])
        ]
        heads: dict[str, str] = {}
        per_project_changes: dict[str, list] = {}
        for b in bindings:
            anchor = b.digest.anchor if b.digest else "branch"
            deploy_workflow = b.digest.deploy_workflow if b.digest else None
            head = head_for(b.owner, b.repo, anchor, deploy_workflow, get_json=self._get_json)
            if head is None:
                continue
            heads[b.name] = head
            wm = state.watermarks.get(b.name)
            if wm is None:
                if anchor == "branch":
                    cutoff = (now - _PERIOD[cadence]).strftime("%Y-%m-%dT%H:%M:%SZ")
                    changes = changes_since(b.owner, b.repo, cutoff, get_json=self._get_json)
                else:
                    changes = []
            elif head == wm:
                changes = []
            else:
                changes = changes_between(b.owner, b.repo, wm, head, get_json=self._get_json)
            if changes:
                per_project_changes[b.name] = changes
        if not per_project_changes:
            return
        context_binding = self._by_name[next(iter(per_project_changes))]
        text = await self._runner.summarize_shared(context_binding, per_project_changes)
        dm_channel = await self._poster.open_dm(user_id)
        await self._poster.post(dm_channel, text)
        await self._state.advance(user_id, heads, now.timestamp())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal_digest.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_personal_digest.py
git commit -m "feat: PersonalDigestAction (per-user DM digest, open-tier only)"
```

---

### Task 11: Wire into `app.py` + document the `/babbla` command

Always create `PersonalSubStore` and register `/babbla` (cheap, no network). Build the catalog when lobby **or** subscriptions **or** `personal_digest` is configured (preserving the plain pilot's no-network-at-startup invariant). Schedule `PersonalDigestAction` only when `personal_digest` is set.

**Files:**
- Modify: `src/babbla/app.py`
- Modify: `docs/DEPLOY.md`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `PersonalSubStore`, `PersonalDigestStateStore`, `PersonalDigestAction`, `Orchestrator(personal_store=..., personal_default_cadence=...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_build_orchestrator_always_has_personal_store(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert orch._personal_store is not None
    assert orch._catalog == ()            # plain pilot: still no catalog, no network


def test_build_orchestrator_personal_digest_builds_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "personal_digest:\n  default_cadence: weekly\n  tz: UTC\n"
    )
    calls = []
    def fake_get_json(path):
        calls.append(path)
        return {"description": "desc"}
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"),
        secrets=load_secrets(ENV), get_json=fake_get_json,
    )
    assert calls == ["/repos/Wkkkkk/MyTV"]
    assert len(orch._catalog) == 1
    assert orch._personal_store is not None


def test_build_scheduler_includes_personal_digest(tmp_path):
    cfg_path = tmp_path / "channels.yaml"
    cfg_path.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "personal_digest:\n  default_cadence: weekly\n  tz: UTC\n"
    )
    config = load_config(cfg_path)
    sched = build_scheduler(
        config=config, secrets=load_secrets(ENV), db_path=str(tmp_path / "s.db"), client=object()
    )
    assert "PersonalDigestAction" in [type(a).__name__ for a in sched._actions]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_app.py -q`
Expected: FAIL (`orch._personal_store` is `None` / no `PersonalDigestAction`).

- [ ] **Step 3: Update `build_orchestrator` and `build_scheduler`**

In `src/babbla/app.py`, extend the store imports:

```python
from babbla.session_store import (
    ActionTimerStore, DigestStateStore, LobbyThreadStore, PersonalDigestStateStore,
    PersonalSubStore, SessionStore, SharedDigestStateStore,
)
from babbla.digest.actions import (
    PerProjectDigestAction, PersonalDigestAction, QuizAction, SharedDigestAction,
)
```

Replace `build_orchestrator` with:

```python
def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets, get_json=None) -> Orchestrator:
    config = load_config(config_path)
    runner = AgentRunner(secrets)
    store = SessionStore(db_path)
    personal_store = PersonalSubStore(db_path)
    default_cadence = config.personal_digest.default_cadence if config.personal_digest else "weekly"
    if config.lobby_channel_id is None and not config.subscriptions and config.personal_digest is None:
        return Orchestrator(
            config, runner, store,
            personal_store=personal_store, personal_default_cadence=default_cadence,
        )
    reader = get_json or make_get_json(secrets.github_token)
    catalog = build_catalog([b for b in config.bindings], reader)
    return Orchestrator(
        config, runner, store,
        catalog=catalog,
        classify_fn=make_classify_fn(_sdk_query, secrets.model),
        lobby_store=LobbyThreadStore(db_path),
        personal_store=personal_store,
        personal_default_cadence=default_cadence,
    )
```

In `build_scheduler`, after the existing `for b in config.quiz_bindings(): ...` loop and before `return ActionScheduler(...)`, add:

```python
    if config.personal_digest is not None:
        personal_store = PersonalSubStore(db_path)
        personal_state = PersonalDigestStateStore(db_path)
        actions.append(
            PersonalDigestAction(
                personal_store, personal_state, by_name, get_json, digest_runner, poster,
                config.personal_digest.default_cadence, config.personal_digest.tz,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_app.py -q`
Expected: PASS (existing `test_build_orchestrator_without_lobby_has_empty_catalog` still passes — no `personal_digest` → still no catalog).

- [ ] **Step 5: Document the slash command in `docs/DEPLOY.md`**

Add a short subsection to `docs/DEPLOY.md` under the Slack-app setup notes:

```markdown
### Personal subscriptions (`/babbla`)

To enable the personal-subscription command, add a slash command to the Slack app manifest:

- **Command:** `/babbla`
- **Usage hint:** `subscribe <project> | unsubscribe <project> | list | digest daily|weekly|off`
- **Should escape channels/users:** no
- Requires the `commands` OAuth scope.

Management (`subscribe`/`unsubscribe`/`list`) works as soon as the command is registered.
The Personal Digest (delivered by DM) additionally requires a `personal_digest:` block in
`config/channels.yaml` (see the commented example there).
```

- [ ] **Step 6: Run the full suite + commit**

Run: `python -m pytest -q`
Expected: PASS (whole suite green).

```bash
git add src/babbla/app.py docs/DEPLOY.md tests/test_app.py
git commit -m "feat: wire personal subscriptions (store, command, digest action)"
```

---

## Self-Review

**1. Spec coverage**

| Spec element | Task |
| --- | --- |
| First user-driven write store | Tasks 3, 4 |
| `/babbla` umbrella command (subscribe/unsubscribe/list/digest/help) | Tasks 5, 6, 8 |
| Command vs Ask separation (distinct slash event) | Task 8 |
| Per-user interest store + cadence | Task 3 |
| Per-user-per-project watermark | Task 4 |
| Personal DM-ask routing (≥2 route, 1 direct, empty → `dm:true`) | Task 7 |
| Visibility check #1 (subscribe-time) | Tasks 1, 6 |
| Visibility check #2 (ask-time, `Surface.DM`) | Task 7 |
| Visibility check #3 (digest send-time filter) | Tasks 1, 10 |
| Personal Digest action (per-user cadence, DM delivery, all-quiet silence, failure isolation) | Task 10 |
| `SlackPoster.open_dm` | Task 9 |
| `personal_digest` config block + validation | Task 2 |
| Wiring + inert-when-unconfigured invariant | Task 11 |
| Docs: `channels.yaml` example + Slack manifest note | Tasks 2, 11 |

No spec element is unmapped.

**2. Placeholder scan:** No "TBD"/"implement later". Task 10's implementer note is a *verification* instruction (confirm the `anchors.py` contract against `test_digest_shared.py`), not a placeholder — the action code and behavioral tests are complete; only the GitHub-JSON fake may need shape-matching to the existing anchor helpers.

**3. Type consistency:** `PersonalSubStore` methods (`add`/`remove`/`list_for`/`all_user_ids`/`get_cadence`/`set_cadence`) are used identically in Tasks 6, 7, 10. `PersonalDigestStateStore.get/advance` return/accept the shared `SharedDigestState` shape, consumed consistently in Task 10. `handle_ask(..., user_id=None)` and `handle_command(user_id, text)` signatures match between Tasks 6/7 (orchestrator) and Task 8 (adapter callers). `Command(verb, arg)` shape matches between Task 5 (definition) and Task 6 (consumption). `is_open_tier` (Task 1) is used in Tasks 6 and 10. `SlackPoster.open_dm` (Task 9) is used in Task 10.
