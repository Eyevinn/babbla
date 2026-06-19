# Shared Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one Slack channel follow a *set* of projects (a "portfolio" channel), routing each question to the right project — reusing the existing Lobby routing layer, inert until configured.

**Architecture:** A new top-level `subscriptions:` block in `channels.yaml` maps a `channel_id` to project names. `Orchestrator.handle_ask` gains a top branch: a channel listed in `subscriptions:` routes among its subscribed subset of the catalog (via `lobby.route` + the injected classifier + sticky `LobbyThreadStore`); everything else takes today's single-project/DM path unchanged. Authorization is `Surface.CHANNEL` → always allow (channel = access).

**Tech Stack:** Python 3, `dataclasses`, PyYAML, `pytest` (`asyncio_mode=auto`), Claude Agent SDK (only via the already-built `classify_fn`; never called directly in tests).

## Global Constraints

- **Reuse one routing implementation.** Subscription routing MUST reuse `lobby.route`, the injected `classify_fn`, the startup `catalog`, and `LobbyThreadStore`. Do NOT add a second router or a new store.
- **Inert when unconfigured.** With no `subscriptions:` block, `handle_ask` behavior is byte-for-byte today's path — zero behavior change for the MyTV pilot.
- **Access rule.** A subscription-channel Ask is `Surface.CHANNEL` → always allow, including for `private` projects (channel membership = access). Keep the `authorize_ask` call as the explicit access decision point.
- **No pointer suffix** on subscription-channel answers (the asker is already in the right channel) — unlike `handle_lobby_ask`.
- **Deterministic tests only.** Inject fake `classify_fn`, fake runner, real on-disk stores in `tmp_path`. No network, no real model calls.
- **`ProjectBinding` is positional:** `ProjectBinding(name, owner, repo, visibility, channel_id, dm)`.
- **Run tests with** `.venv/bin/python -m pytest` (the bare `python`/`python3` lack pytest). Tests are `asyncio_mode=auto` — async test functions need no decorator.
- **Secrets hygiene for `config/channels.yaml`:** the repo is public OSS and the operator's real Slack ids live UNSTAGED in the working tree. Never `git add -A`; never stage real ids. Committed `channels.yaml` content uses placeholders / `null` / comments only. (Task 5 is controller-handled for this reason.)

---

### Task 1: `Subscription` config model + parsing + validation

**Files:**
- Modify: `src/babbla/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `ProjectBinding`, `Config`, `load_config` in `src/babbla/config.py`; the module-level `logger`.
- Produces:
  - `Subscription(channel_id: str, project_names: tuple[str, ...])` — frozen dataclass.
  - `Config.subscriptions: tuple[Subscription, ...]` (default `()`).
  - `Config.subscription_for(channel_id: str) -> Subscription | None`.
  - `load_config` parses a top-level `subscriptions:` list and validates it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
SUBS_FIXTURE = """
projects:
  - name: MyTV
    owner: Wkkkkk
    repo: MyTV
    visibility: public
    channel_id: C123
    dm: true
  - name: Stream
    owner: Wkkkkk
    repo: stream-starter
    visibility: internal
    channel_id: C456
    dm: false
subscriptions:
  - channel_id: C900
    projects: [MyTV, Stream]
"""


def test_subscriptions_parsed(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_FIXTURE))
    assert len(cfg.subscriptions) == 1
    sub = cfg.subscriptions[0]
    assert sub.channel_id == "C900"
    assert sub.project_names == ("MyTV", "Stream")


def test_subscription_for_hit_and_miss(tmp_path):
    cfg = load_config(_write(tmp_path, SUBS_FIXTURE))
    assert cfg.subscription_for("C900").project_names == ("MyTV", "Stream")
    assert cfg.subscription_for("CNOPE") is None


def test_subscriptions_absent_is_empty(tmp_path):
    cfg = load_config(_write(tmp_path, FIXTURE))
    assert cfg.subscriptions == ()


def test_subscription_unknown_project_raises(tmp_path):
    text = SUBS_FIXTURE.replace("projects: [MyTV, Stream]", "projects: [MyTV, Ghost]")
    with pytest.raises(ValueError, match="unknown project"):
        load_config(_write(tmp_path, text))


def test_subscription_empty_projects_raises(tmp_path):
    text = SUBS_FIXTURE.replace("projects: [MyTV, Stream]", "projects: []")
    with pytest.raises(ValueError, match="at least one project"):
        load_config(_write(tmp_path, text))


def test_subscription_duplicate_channel_raises(tmp_path):
    text = SUBS_FIXTURE + "  - channel_id: C900\n    projects: [MyTV]\n"
    with pytest.raises(ValueError, match="more than one subscription"):
        load_config(_write(tmp_path, text))


def test_subscription_collides_with_lobby_warns(tmp_path, caplog):
    text = SUBS_FIXTURE + "lobby_channel_id: C900\n"
    with caplog.at_level(logging.WARNING, logger="babbla.config"):
        cfg = load_config(_write(tmp_path, text))
    assert cfg.subscription_for("C900") is not None      # still loads
    assert any("shadowed" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -k subscription -v`
Expected: FAIL (`Config` has no attribute `subscriptions` / `subscription_for`; `Subscription` import-time absent → these tests error/fail).

- [ ] **Step 3: Implement the model, accessor, parsing, and validation**

In `src/babbla/config.py`, add the `Subscription` dataclass after `ProjectBinding`:

```python
@dataclass(frozen=True)
class Subscription:
    channel_id: str
    project_names: tuple[str, ...]
```

Extend `Config` (add the field and accessor; keep existing fields/methods):

```python
@dataclass(frozen=True)
class Config:
    bindings: tuple[ProjectBinding, ...]
    lobby_channel_id: str | None = None
    subscriptions: tuple[Subscription, ...] = ()

    def for_channel(self, channel_id: str) -> ProjectBinding | None:
        for b in self.bindings:
            if b.channel_id is not None and b.channel_id == channel_id:
                return b
        return None

    def for_dm(self) -> ProjectBinding | None:
        for b in self.bindings:
            if b.dm:
                return b
        return None

    def subscription_for(self, channel_id: str) -> Subscription | None:
        for s in self.subscriptions:
            if s.channel_id == channel_id:
                return s
        return None

    def digest_bindings(self) -> tuple[ProjectBinding, ...]:
        return tuple(b for b in self.bindings if b.digest is not None and b.channel_id)
```

Add a parsing helper above `load_config`:

```python
def _parse_subscriptions(raw_subs, known_names: set[str]) -> tuple[Subscription, ...]:
    subscriptions: list[Subscription] = []
    seen_channels: set[str] = set()
    for raw_sub in raw_subs or []:
        channel_id = raw_sub.get("channel_id")
        if not channel_id:
            raise ValueError("channels.yaml: each subscription requires a channel_id")
        names = tuple(raw_sub.get("projects") or ())
        if not names:
            raise ValueError(
                f"channels.yaml: subscription for {channel_id} must list at least one project"
            )
        for n in names:
            if n not in known_names:
                raise ValueError(
                    f"channels.yaml: subscription for {channel_id} references unknown project {n!r}"
                )
        if channel_id in seen_channels:
            raise ValueError(
                f"channels.yaml: channel_id {channel_id} appears in more than one subscription"
            )
        seen_channels.add(channel_id)
        subscriptions.append(Subscription(channel_id=channel_id, project_names=names))
    return tuple(subscriptions)
```

In `load_config`, after the existing `dm > 1` check, before `return Config(...)`, replace the final lines with:

```python
    if sum(1 for b in bindings if b.dm) > 1:
        raise ValueError("channels.yaml: exactly one project may set dm: true in the pilot")
    lobby_channel_id = raw.get("lobby_channel_id")
    subscriptions = _parse_subscriptions(raw.get("subscriptions"), {b.name for b in bindings})
    for sub in subscriptions:
        if lobby_channel_id is not None and sub.channel_id == lobby_channel_id:
            logger.warning(
                "channels.yaml: channel_id %r is both the lobby channel and a subscription; "
                "the lobby dispatch wins, so the subscription is shadowed.",
                sub.channel_id,
            )
    return Config(
        bindings=bindings,
        lobby_channel_id=lobby_channel_id,
        subscriptions=subscriptions,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (all existing config tests plus the seven new ones).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/config.py tests/test_config.py
git commit -m "feat: Subscription config model + parsing/validation"
```

---

### Task 2: `subscriptions.py` reply/filter helpers

**Files:**
- Create: `src/babbla/subscriptions.py`
- Test: `tests/test_subscriptions.py`

**Interfaces:**
- Consumes: `CatalogEntry` from `src/babbla/lobby.py` (`CatalogEntry.binding.name`).
- Produces:
  - `entries_for(catalog: Sequence[CatalogEntry], names: Sequence[str]) -> tuple[CatalogEntry, ...]` — order-preserving by `names`, skips names absent from the catalog.
  - `subscription_clarify(entries: Sequence[CatalogEntry]) -> str` — "which project?" text listing the subscribed project names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_subscriptions.py`:

```python
from babbla.config import ProjectBinding
from babbla.lobby import CatalogEntry
from babbla import subscriptions

A = CatalogEntry(ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False), None)
B = CatalogEntry(ProjectBinding("Stream", "o", "stream", "internal", "C2", False), None)
CATALOG = (A, B)


def test_entries_for_filters_and_orders_by_names():
    assert subscriptions.entries_for(CATALOG, ["Stream", "MyTV"]) == (B, A)


def test_entries_for_skips_unknown_names():
    assert subscriptions.entries_for(CATALOG, ["MyTV", "Ghost"]) == (A,)


def test_entries_for_empty_names_is_empty():
    assert subscriptions.entries_for(CATALOG, []) == ()


def test_subscription_clarify_lists_multiple():
    msg = subscriptions.subscription_clarify((A, B))
    assert "MyTV" in msg and "Stream" in msg


def test_subscription_clarify_single():
    msg = subscriptions.subscription_clarify((A,))
    assert "MyTV" in msg
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'babbla.subscriptions'`).

- [ ] **Step 3: Implement the helpers**

Create `src/babbla/subscriptions.py`:

```python
from __future__ import annotations

from typing import Sequence

from babbla.lobby import CatalogEntry


def entries_for(
    catalog: Sequence[CatalogEntry], names: Sequence[str]
) -> tuple[CatalogEntry, ...]:
    """Catalog entries for the given project names, in the order given.

    A name with no matching catalog entry is silently skipped — config
    validation guarantees the name exists as a binding, so this only guards a
    partially-built catalog.
    """
    by_name = {e.binding.name: e for e in catalog}
    return tuple(by_name[n] for n in names if n in by_name)


def subscription_clarify(entries: Sequence[CatalogEntry]) -> str:
    """The 'which project?' reply listing a channel's subscribed projects."""
    listing = ", ".join(f"*{e.binding.name}*" for e in entries)
    return (
        "🤔 I'm not sure which project you mean. This channel follows: "
        + listing
        + ".\nMention the project name and I'll dig in."
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_subscriptions.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/subscriptions.py tests/test_subscriptions.py
git commit -m "feat: subscription reply/filter helpers"
```

---

### Task 3: Orchestrator subscription routing branch

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Config.subscription_for` (Task 1); `subscriptions.entries_for` / `subscriptions.subscription_clarify` (Task 2); existing `lobby.route`, `authorize_ask`, `Surface.CHANNEL`, `self._catalog`, `self._classify_fn`, `self._lobby_store`, `self._store`, `self._runner`, `self._lock_for`, `self._release_lock`.
- Produces: a subscription branch at the top of `handle_ask`; `_handle_subscription_ask` and `_resolve_subscription` helpers.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py` (the file already imports `CatalogEntry`, `LobbyThreadStore`, defines `PUB`/`PRIV`/`CATALOG`, `_classify_returning`, and the `store` fixture):

```python
from babbla.config import Subscription


def _sub_orch(bindings, subs, runner, store, classify, lobby_store):
    return Orchestrator(
        Config(bindings=bindings, subscriptions=subs),
        runner,
        store,
        catalog=CATALOG,
        classify_fn=classify,
        lobby_store=lobby_store,
    )


SUBS_TWO = (Subscription("C900", ("MyTV", "Secret")),)


async def test_subscription_routes_runs_and_persists_no_suffix(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("MyTV"), lobby_store)
    ans = await orch.handle_ask(text="how does playback work?", thread_ts="ts", channel_id="C900", is_dm=False)
    assert ans.text == "answer to how does playback work?"   # NO pointer suffix
    assert "↪" not in ans.text
    assert runner.calls[0][1].name == "MyTV"
    assert await lobby_store.get("ts") == "MyTV"              # sticky persisted
    assert await store.get_session("ts") == "sess-1"          # session persisted
    lobby_store.close()


async def test_subscription_sticky_skips_routing(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    await lobby_store.put("ts", "MyTV")
    recorder = []
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("Secret", recorder=recorder), lobby_store)
    await orch.handle_ask(text="follow up", thread_ts="ts", channel_id="C900", is_dm=False)
    assert recorder == []                                     # classifier NOT called on sticky hit
    assert runner.calls[0][1].name == "MyTV"
    lobby_store.close()


async def test_subscription_no_match_clarifies(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("NONE"), lobby_store)
    ans = await orch.handle_ask(text="ambiguous", thread_ts="ts", channel_id="C900", is_dm=False)
    assert "MyTV" in ans.text and "Secret" in ans.text       # lists subscribed projects
    assert runner.calls == []                                 # no agent run
    assert await lobby_store.get("ts") is None                # nothing persisted
    assert await store.get_session("ts") is None
    lobby_store.close()


async def test_subscription_size_one_skips_classifier(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    recorder = []
    subs = (Subscription("C901", ("MyTV",)),)
    orch = _sub_orch((PUB, PRIV), subs, runner, store, _classify_returning("NONE", recorder=recorder), lobby_store)
    ans = await orch.handle_ask(text="anything", thread_ts="ts", channel_id="C901", is_dm=False)
    assert recorder == []                                     # no classifier call for size-1
    assert runner.calls[0][1].name == "MyTV"
    assert ans.text == "answer to anything"
    lobby_store.close()


async def test_subscription_private_project_is_answered(store, tmp_path):
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    orch = _sub_orch((PUB, PRIV), SUBS_TWO, runner, store, _classify_returning("Secret"), lobby_store)
    ans = await orch.handle_ask(text="how does Secret work?", thread_ts="ts", channel_id="C900", is_dm=False)
    assert ans.text == "answer to how does Secret work?"     # channel = access
    assert runner.calls[0][1].name == "Secret"
    lobby_store.close()


async def test_non_subscription_channel_unchanged(store, tmp_path):
    # A channel NOT in subscriptions takes the existing single-project path; router untouched.
    runner = FakeRunner()
    lobby_store = LobbyThreadStore(str(tmp_path / "l.db"))
    recorder = []
    single = ProjectBinding("MyTV", "Wkkkkk", "MyTV", "public", "C123", False)
    orch = _sub_orch((single,), SUBS_TWO, runner, store, _classify_returning("MyTV", recorder=recorder), lobby_store)
    ans = await orch.handle_ask(text="q", thread_ts="ts", channel_id="C123", is_dm=False)
    assert ans.text == "answer to q"
    assert recorder == []                                     # subscription router not engaged
    assert runner.calls[0][1].name == "MyTV"
    lobby_store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -k subscription -v`
Expected: FAIL (`handle_ask` has no subscription branch → an ask in `C900` raises `UnknownSurfaceError` because no binding has `channel_id == "C900"`).

- [ ] **Step 3: Implement the branch**

In `src/babbla/orchestrator.py`, add the import alongside the existing `from babbla import lobby`:

```python
from babbla import lobby, subscriptions
```

Add the subscription branch at the very top of `handle_ask` (before `binding = self._resolve(...)`):

```python
    async def handle_ask(
        self, *, text: str, thread_ts: str, channel_id: str, is_dm: bool
    ) -> CitedAnswer:
        if not is_dm:
            sub = self._config.subscription_for(channel_id)
            if sub is not None:
                return await self._handle_subscription_ask(
                    text=text, thread_ts=thread_ts, sub=sub
                )
        binding = self._resolve(channel_id, is_dm)
        surface = Surface.DM if is_dm else Surface.CHANNEL
        decision = authorize_ask(binding, surface)
        if not decision.allowed:
            # Pre-flight deny: no model call, no session write.
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

Add the two helpers (place them after `handle_ask`, before `_resolve_lobby`):

```python
    async def _resolve_subscription(self, text: str, thread_ts: str, entries):
        if len(entries) == 1:
            return entries[0]                       # deterministic: no classifier call
        sticky = await self._lobby_store.get(thread_ts)
        if sticky is not None:
            for entry in entries:
                if entry.binding.name == sticky:
                    return entry
            # sticky project no longer in this subscription → re-route
        return await lobby.route(text, entries, self._classify_fn)

    async def _handle_subscription_ask(self, *, text: str, thread_ts: str, sub) -> CitedAnswer:
        entries = subscriptions.entries_for(self._catalog, sub.project_names)
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_subscription(text, thread_ts, entries)
                if entry is None:
                    return CitedAnswer(
                        text=subscriptions.subscription_clarify(entries), session_id=None
                    )
                decision = authorize_ask(entry.binding, Surface.CHANNEL)  # channel = access
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
                await self._lobby_store.put(thread_ts, entry.binding.name)
                resume = await self._store.get_session(thread_ts)
                answer = await self._runner.run_ask(text, entry.binding, resume)
                if answer.session_id:
                    await self._store.put_session(thread_ts, answer.session_id)
                return answer       # no pointer suffix — the asker is already home
            finally:
                self._release_lock(thread_ts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (all existing orchestrator tests plus the six new ones).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: subscription routing branch in handle_ask"
```

---

### Task 4: Wire routing machinery when subscriptions are configured

**Files:**
- Modify: `src/babbla/app.py:49`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Config.subscriptions` (Task 1); existing `build_orchestrator` (`src/babbla/app.py:45-58`).
- Produces: `build_orchestrator` builds the catalog / `classify_fn` / `LobbyThreadStore` when `lobby_channel_id` is set OR `config.subscriptions` is non-empty.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_build_orchestrator_with_subscriptions_builds_catalog(tmp_path):
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
        "subscriptions:\n  - channel_id: C900\n    projects: [MyTV]\n"
    )
    calls = []

    def fake_get_json(path):
        calls.append(path)
        return {"description": "desc"}

    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"),
        secrets=load_secrets(ENV), get_json=fake_get_json,
    )
    assert calls == ["/repos/Wkkkkk/MyTV"]            # catalog built even without a lobby
    assert len(orch._catalog) == 1
    assert orch._lobby_store is not None
    assert orch._classify_fn is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py::test_build_orchestrator_with_subscriptions_builds_catalog -v`
Expected: FAIL (`build_orchestrator` early-returns the inert orchestrator because `lobby_channel_id is None`; `orch._catalog == ()`, `orch._lobby_store is None`).

- [ ] **Step 3: Generalize the wiring condition**

In `src/babbla/app.py`, change the early-return guard inside `build_orchestrator` from:

```python
    if config.lobby_channel_id is None:
        return Orchestrator(config, runner, store)
```

to:

```python
    if config.lobby_channel_id is None and not config.subscriptions:
        return Orchestrator(config, runner, store)
```

(The rest of `build_orchestrator` is unchanged — it already builds `catalog`, `classify_fn`, and `LobbyThreadStore`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -v`
Expected: PASS (existing app tests — including `test_build_orchestrator_without_lobby_has_empty_catalog`, which has no subscriptions and stays inert — plus the new one).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/app.py tests/test_app.py
git commit -m "feat: wire routing machinery when subscriptions configured"
```

---

### Task 5: Document the `subscriptions:` block in the channels.yaml template

> **CONTROLLER-HANDLED — do not delegate to a subagent.** The operator's real Slack ids live UNSTAGED in the working-tree `config/channels.yaml`. This task adds a *commented* doc block to the **committed template** while keeping the operator's real ids unstaged. Stage selectively and guard.

**Files:**
- Modify: `config/channels.yaml` (committed template only; working-tree real ids stay unstaged)

**Interfaces:**
- Consumes: nothing. Pure documentation; the doc block is entirely comments (inert — no `subscriptions:` key is actually set in the committed template).

- [ ] **Step 1: Build the committed template content**

Take the current committed template (`git show HEAD:config/channels.yaml`) and append this commented block at the end (all comments — no real ids, no active key):

```yaml
# Shared Subscriptions: let ONE channel follow a SET of projects (a "portfolio"
# channel). A question there is routed to the right project; today's 1:1
# single-project channels (channel_id on a project above) keep working unchanged.
# Omit this block entirely for the 1:1 default. Example:
# subscriptions:
#   - channel_id: C0PORTFOLIO   # the portfolio channel's Slack id
#     projects: [MyTV]          # names from `projects:` above; >= 1, all must exist
```

Write that combined content to a temp file, stage **only** that version, then restore the working tree to its real-id state. Concretely (controller runs this; the real id is read from the working tree, never written into any committed file or this plan):

```bash
# 1. Snapshot the operator's real working-tree file
cp config/channels.yaml /tmp/channels.working.yaml
# 2. Produce the committed template = HEAD content + the doc block above
git show HEAD:config/channels.yaml > config/channels.yaml
cat >> config/channels.yaml <<'EOF'
# Shared Subscriptions: let ONE channel follow a SET of projects (a "portfolio"
# channel). A question there is routed to the right project; today's 1:1
# single-project channels (channel_id on a project above) keep working unchanged.
# Omit this block entirely for the 1:1 default. Example:
# subscriptions:
#   - channel_id: C0PORTFOLIO   # the portfolio channel's Slack id
#     projects: [MyTV]          # names from `projects:` above; >= 1, all must exist
EOF
# 3. Stage that template version
git add config/channels.yaml
```

- [ ] **Step 2: Guard — assert no real id leaked into the staged content**

```bash
# REAL_ID is read from the working-tree snapshot, never hardcoded here.
# Extract the operator's real channel_id(s)/lobby id from the snapshot and assert
# each is ABSENT from the staged diff. Fail loudly if any is present.
git diff --cached config/channels.yaml | grep -nE 'C0[A-Z0-9]{6,}' && {
  echo "REAL ID LEAK in staged channels.yaml — aborting"; exit 1; } || echo "staged content clean"
```

Expected: `staged content clean` (the staged template contains only the `C0PORTFOLIO` / `C0LOBBY`-style placeholders and `null`, no operator id). If the grep matches a *placeholder* only, visually confirm it is a placeholder, not the operator's real id from the snapshot.

- [ ] **Step 3: Restore the working tree to the operator's real-id file**

```bash
cp /tmp/channels.working.yaml config/channels.yaml
rm -f /tmp/channels.working.yaml
git status --short config/channels.yaml   # expect: "MM" or " M" — real id present, UNSTAGED
```

- [ ] **Step 4: Commit the template doc**

```bash
git commit -m "docs: document subscriptions block in channels.yaml template"
git show --stat HEAD          # confirm only config/channels.yaml changed
git show HEAD:config/channels.yaml | grep -c 'C0PORTFOLIO'   # expect: 1 (placeholder present)
```

After the commit, `git status` must still show `config/channels.yaml` as modified+unstaged (the operator's real id restored in the working tree).

---

## Notes for the executor

- After all tasks, the full suite should be green: `.venv/bin/python -m pytest -q` (expect the prior 168 passed / 2 skipped plus the ~18 new tests, 0 failures).
- Do not push to origin — the operator pushes manually after review.
- The only state Babbla writes remains the existing stores (`SessionStore`, `LobbyThreadStore`, `DigestStateStore`); this slice adds no new store and no new write path.
