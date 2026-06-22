# Private Projects in Personal Subscriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user follow / DM-ask / topic-filter / receive-in-digest a **private** project, gated on a live check that they are a member of the project's bound private channel.

**Architecture:** `access.py` stays a pure decision module — it gains `authorize_personal(binding, *, is_member)`. The Slack membership lookup lives behind an injected async **membership oracle** (`babbla/membership.py`) that is called only for private bindings by the orchestrator (subscribe, topic, DM answer) and the personal-digest action. Fail-closed, TTL-cached, deny-by-default when no oracle is wired.

**Tech Stack:** Python 3.12, `pytest` (async tests run without explicit `@pytest.mark.asyncio` — see existing tests), `slack_sdk` (`AsyncWebClient`, `conversations_members`, `SlackApiError`).

## Global Constraints

- **Read-only preserved (ADR 0003):** no new agent tools; the only new external call is a Slack **read** (`conversations.members`). Copy verbatim from spec.
- **Fail closed:** any membership lookup error/timeout ⇒ `False` (deny / omit), logged, never raised to the user.
- **Open-tier never triggers a Slack call:** every call site short-circuits on `is_open_tier(binding)` before awaiting the oracle.
- **No private-name leakage:** discovery lists (`render_unknown_project`) advertise open-tier projects only.
- **No auto-unsubscribe:** subscription records are kept when membership is lost; rejoining restores access.
- **Slack scope required at deploy:** `groups:read` (read private-channel membership). Not a code change; note in handoff.
- **ProjectBinding field order** (for test fixtures): `ProjectBinding(name, owner, repo, visibility, channel_id, dm)`.
- Run the full suite with `python -m pytest -q` from the repo root (use the project venv).

---

### Task 1: Membership oracle (`babbla/membership.py`)

**Files:**
- Create: `src/babbla/membership.py`
- Test: `tests/test_membership.py`

**Interfaces:**
- Consumes: a Slack client exposing `await client.conversations_members(channel=, limit=, cursor=)` returning a mapping with `members` and `response_metadata.next_cursor`.
- Produces:
  - `async def deny_membership(user_id: str, channel_id: str | None) -> bool` — always `False`.
  - `def make_membership(client, *, ttl_seconds: float = 5.0, now_fn: Callable[[], float] = time.monotonic) -> MembershipFn` where `MembershipFn = Callable[[str, str | None], Awaitable[bool]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_membership.py`:

```python
from slack_sdk.errors import SlackApiError

from babbla.membership import deny_membership, make_membership


class FakeClient:
    def __init__(self, pages, *, error=False):
        # pages: list of (members_list, next_cursor) tuples
        self._pages = pages
        self.error = error
        self.calls = 0

    async def conversations_members(self, *, channel, limit=200, cursor=None):
        self.calls += 1
        if self.error:
            raise SlackApiError("boom", response={"ok": False, "error": "fetch_failed"})
        idx = 0 if cursor is None else int(cursor)
        members, next_cursor = self._pages[idx]
        meta = {"next_cursor": next_cursor or ""}
        return {"members": members, "response_metadata": meta}


async def test_member_present_first_page_true():
    client = FakeClient([(["U1", "U2"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is True


async def test_member_found_on_second_page_true():
    client = FakeClient([(["U2"], "1"), (["U1"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is True
    assert client.calls == 2  # paginated


async def test_non_member_false():
    client = FakeClient([(["U2", "U3"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is False


async def test_none_channel_returns_false_without_call():
    client = FakeClient([(["U1"], None)])
    is_member = make_membership(client)
    assert await is_member("U1", None) is False
    assert client.calls == 0


async def test_slack_error_fails_closed():
    client = FakeClient([], error=True)
    is_member = make_membership(client)
    assert await is_member("U1", "C1") is False


async def test_ttl_cache_hit_avoids_second_call_then_expires():
    client = FakeClient([(["U1"], None)])
    t = {"v": 1000.0}
    is_member = make_membership(client, ttl_seconds=5.0, now_fn=lambda: t["v"])
    assert await is_member("U1", "C1") is True
    assert await is_member("U1", "C1") is True
    assert client.calls == 1            # served from cache
    t["v"] = 1006.0                     # advance past ttl
    assert await is_member("U1", "C1") is True
    assert client.calls == 2            # re-fetched after expiry


async def test_deny_membership_always_false():
    assert await deny_membership("U1", "C1") is False
    assert await deny_membership("U1", None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_membership.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.membership'`.

- [ ] **Step 3: Write the implementation**

Create `src/babbla/membership.py`:

```python
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

MembershipFn = Callable[[str, "str | None"], Awaitable[bool]]


async def deny_membership(user_id: str, channel_id: str | None) -> bool:
    """Fail-closed default: nobody is a member unless a real oracle is wired."""
    return False


def make_membership(
    client,
    *,
    ttl_seconds: float = 5.0,
    now_fn: Callable[[], float] = time.monotonic,
) -> MembershipFn:
    """Build an async `(user_id, channel_id) -> bool` membership oracle.

    Backed by Slack `conversations.members`. Fail-closed on any error.
    Results (positive and negative) are cached per (channel, user) for
    `ttl_seconds` to absorb bursts within a single thread/turn.
    """
    cache: dict[tuple[str, str], tuple[bool, float]] = {}

    async def is_member(user_id: str, channel_id: str | None) -> bool:
        if not channel_id:
            return False
        key = (channel_id, user_id)
        now = now_fn()
        hit = cache.get(key)
        if hit is not None and hit[1] > now:
            return hit[0]
        try:
            found = await _lookup(client, channel_id, user_id)
        except SlackApiError as exc:
            logger.warning(
                "membership lookup failed (%s in %s): %s", user_id, channel_id, exc
            )
            found = False
        except Exception:  # transport / timeout — fail closed
            logger.exception("membership lookup error (%s in %s)", user_id, channel_id)
            found = False
        cache[key] = (found, now + ttl_seconds)
        return found

    return is_member


async def _lookup(client, channel_id: str, user_id: str) -> bool:
    cursor: str | None = None
    while True:
        resp = await client.conversations_members(
            channel=channel_id, limit=200, cursor=cursor
        )
        if user_id in (resp.get("members") or []):
            return True
        cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_membership.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/membership.py tests/test_membership.py
git commit -m "feat(membership): fail-closed, TTL-cached Slack channel-membership oracle"
```

---

### Task 2: Pure `authorize_personal` decision (`access.py`)

**Files:**
- Modify: `src/babbla/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `ProjectBinding`, `is_open_tier`, `AccessDecision`, `_pointer` (all already in `access.py`).
- Produces: `def authorize_personal(binding: ProjectBinding, *, is_member: bool) -> AccessDecision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_access.py`:

```python
from babbla.access import authorize_personal


def test_authorize_personal_open_tier_allows_ignoring_membership():
    d = authorize_personal(_b("public"), is_member=False)
    assert d.allowed is True
    assert d.pointer is None


def test_authorize_personal_private_member_allows():
    d = authorize_personal(_binding("private", "C123"), is_member=True)
    assert d.allowed is True


def test_authorize_personal_private_non_member_denies_with_pointer():
    d = authorize_personal(_binding("private", "C123"), is_member=False)
    assert d.allowed is False
    assert d.reason is not None
    assert "<#C123>" in d.pointer


def test_authorize_personal_private_no_channel_denies_even_if_member():
    d = authorize_personal(_binding("private", None), is_member=True)
    assert d.allowed is False
    assert d.pointer is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_access.py -q`
Expected: FAIL — `ImportError: cannot import name 'authorize_personal'`.

- [ ] **Step 3: Write the implementation**

Append to `src/babbla/access.py` (after `authorize_ask`):

```python
def authorize_personal(binding: ProjectBinding, *, is_member: bool) -> AccessDecision:
    """Authorize a project on a *personal* surface (DM answer / personal digest /
    subscribe / topic). Open-tier is always allowed. A private project is allowed
    only when the caller has confirmed live channel membership AND the binding has
    a channel to belong to. Otherwise deny with the 0007 channel pointer.
    """
    if is_open_tier(binding):
        return AccessDecision(allowed=True)
    if is_member and binding.channel_id:
        return AccessDecision(allowed=True)
    return AccessDecision(
        allowed=False,
        reason=f"{binding.name} is private; user is not a channel member",
        pointer=_pointer(binding),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_access.py -q`
Expected: PASS (all access tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/access.py tests/test_access.py
git commit -m "feat(access): pure authorize_personal — private allowed iff channel member"
```

---

### Task 3: Orchestrator membership gate (subscribe, topic, DM answer)

**Files:**
- Modify: `src/babbla/orchestrator.py`
- Modify: `src/babbla/personal.py` (remove now-unused `render_private_refused`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `authorize_personal` (Task 2), `deny_membership` / `MembershipFn` (Task 1), `is_open_tier`.
- Produces: `Orchestrator(..., membership: MembershipFn = deny_membership)` keyword arg; an internal `async def _authorize_personal(self, user_id, binding) -> AccessDecision` helper used by all three gates.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py` (note: `_config_two`, `psub`, `store`, `FakeRunner` already exist in this file):

```python
from babbla.membership import deny_membership  # noqa: E402


def _member_oracle(is_member, recorder=None):
    async def fn(user_id, channel_id):
        if recorder is not None:
            recorder.append((user_id, channel_id))
        return is_member
    return fn


async def test_subscribe_private_allowed_for_member(store, psub):
    orch = Orchestrator(
        _config_two(), FakeRunner(), store,
        personal_store=psub, membership=_member_oracle(True),
    )
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert "Secret" in reply
    assert await psub.list_for("U1") == ("Secret",)


async def test_subscribe_private_denied_for_non_member(store, psub):
    rec = []
    orch = Orchestrator(
        _config_two(), FakeRunner(), store,
        personal_store=psub, membership=_member_oracle(False, rec),
    )
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert "<#C2>" in reply                      # 0007 pointer, not "doesn't exist"
    assert await psub.list_for("U1") == ()
    assert rec == [("U1", "C2")]                 # oracle consulted for private


async def test_subscribe_public_never_calls_oracle(store, psub):
    rec = []
    orch = Orchestrator(
        _config_two(), FakeRunner(), store,
        personal_store=psub, membership=_member_oracle(True, rec),
    )
    await orch.handle_command("U1", "subscribe MyTV")
    assert rec == []                             # open-tier short-circuits


async def test_default_oracle_denies_private(store, psub):
    # No membership injected -> deny_membership default.
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert await psub.list_for("U1") == ()
    assert "<#C2>" in reply
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -k "private_allowed or non_member or never_calls or default_oracle" -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'membership'`.

- [ ] **Step 3: Write the implementation**

In `src/babbla/orchestrator.py`:

(a) Update imports — change the access import line and add membership:

```python
from babbla.access import Surface, authorize_ask, authorize_personal, is_open_tier
from babbla.membership import deny_membership
```

(b) Add the `membership` kwarg to `__init__` (in the keyword-only block) and store it:

```python
    def __init__(
        self, config: Config, runner, store, *,
        catalog=(), classify_fn=None, lobby_store=None,
        personal_store=None, personal_default_cadence: str = "weekly",
        intent_fn=None, membership=deny_membership,
    ) -> None:
```

and in the body, alongside the other assignments:

```python
        self._membership = membership
```

(c) Add the shared helper (place it just above `_dispatch_command`):

```python
    async def _authorize_personal(self, user_id: str, binding) -> "AccessDecision":
        # Open-tier short-circuits BEFORE any Slack call.
        if is_open_tier(binding):
            return authorize_personal(binding, is_member=True)
        member = await self._membership(user_id, binding.channel_id)
        return authorize_personal(binding, is_member=member)
```

Add `AccessDecision` to the access import for the annotation:

```python
from babbla.access import AccessDecision, Surface, authorize_ask, authorize_personal, is_open_tier
```

(d) Replace the **topic-add/remove** private refusal. Change:

```python
            if not is_open_tier(binding):
                return personal.render_private_refused(binding.name)
```
to:
```python
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                return decision.pointer
```

(e) Replace the **subscribe** private refusal. Change:

```python
            if not is_open_tier(binding):
                return personal.render_private_refused(binding.name)
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
```
to:
```python
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                return decision.pointer
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
```

(f) Plumb `user_id` into the **DM answer** path. In `handle_ask`, change the personal-ask call:

```python
                return await self._handle_personal_ask(
                    text=text, thread_ts=thread_ts, entries=entries, user_id=user_id
                )
```

Change `_handle_personal_ask` signature and its private gate:

```python
    async def _handle_personal_ask(self, *, text: str, thread_ts: str, entries, user_id: str) -> CitedAnswer:
        async with self._lock_for(thread_ts):
            try:
                entry = await self._resolve_subscription(text, thread_ts, entries)
                if entry is None:
                    return CitedAnswer(
                        text=subscriptions.subscription_clarify(entries), session_id=None
                    )
                decision = await self._authorize_personal(user_id, entry.binding)
                if not decision.allowed:
                    return CitedAnswer(text=decision.pointer, session_id=None)
```

(Leave the rest of `_handle_personal_ask` unchanged.)

In `src/babbla/personal.py`, delete the now-unused `render_private_refused` function (lines defining it):

```python
def render_private_refused(name: str) -> str:
    return (
        f"🔒 *{name}* is private — personal subscriptions only cover "
        "public/internal projects."
    )
```

- [ ] **Step 4: Update the existing private-refused test**

In `tests/test_orchestrator.py`, the existing `test_handle_command_subscribe_private_refused` now relies on the default deny oracle; make its intent explicit by asserting the pointer. Replace its body with:

```python
async def test_handle_command_subscribe_private_refused(store, psub):
    # Default (deny) oracle: a non-member cannot follow a private project.
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe Secret")
    assert "<#C2>" in reply
    assert await psub.list_for("U1") == ()
```

- [ ] **Step 5: Run the orchestrator + personal suites**

Run: `python -m pytest tests/test_orchestrator.py tests/test_personal.py -q`
Expected: PASS. If any other test referenced `render_private_refused`, update it to assert the pointer (`<#...>`) instead.

- [ ] **Step 6: Commit**

```bash
git add src/babbla/orchestrator.py src/babbla/personal.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): membership-gate private projects on subscribe/topic/DM"
```

---

### Task 4: Personal-digest membership filter (`PersonalDigestAction`)

**Files:**
- Modify: `src/babbla/digest/actions.py`
- Test: `tests/test_personal_digest.py`

**Interfaces:**
- Consumes: `deny_membership` (Task 1), `is_open_tier` (already imported in `actions.py`).
- Produces: `PersonalDigestAction(..., membership=deny_membership)` — new trailing keyword arg on `__init__`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_personal_digest.py` (reusing `MYTV`, `SECRET`, `BY_NAME`, `FakeRunner`, `FakePoster`, `_get_json_with_commits`, `NOW`, and `PersonalSubStore`/`PersonalDigestStateStore` already imported there):

```python
from babbla.membership import deny_membership


def _oracle(is_member):
    async def fn(user_id, channel_id):
        return is_member
    return fn


async def _run(tmp_path, *, follow, membership):
    subs = PersonalSubStore(str(tmp_path / "p.db"))
    state = PersonalDigestStateStore(str(tmp_path / "d.db"))
    for name in follow:
        await subs.add("U1", name)
    runner = FakeRunner()
    poster = FakePoster()
    get_json = _get_json_with_commits("HEAD1", [{"sha": "HEAD1", "commit": {"message": "x"}}])
    action = PersonalDigestAction(
        subs, state, BY_NAME, get_json, runner, poster, "weekly", "UTC",
        membership=membership,
    )
    await action.maybe_run(NOW)
    subs.close()
    state.close()
    return runner, poster


async def test_digest_includes_private_for_member(tmp_path):
    runner, poster = await _run(tmp_path, follow=["Secret"], membership=_oracle(True))
    assert poster.posts, "member should receive a digest for the private project"


async def test_digest_omits_private_for_non_member(tmp_path):
    runner, poster = await _run(tmp_path, follow=["Secret"], membership=_oracle(False))
    assert poster.posts == [], "non-member must not receive private content"


async def test_digest_default_oracle_omits_private(tmp_path):
    runner, poster = await _run(tmp_path, follow=["Secret"], membership=deny_membership)
    assert poster.posts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_personal_digest.py -k "private_for_member or private_for_non_member or default_oracle" -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'membership'`.

- [ ] **Step 3: Write the implementation**

In `src/babbla/digest/actions.py`:

(a) Add the import near the top:

```python
from babbla.membership import deny_membership
```

(b) Add `membership` to `PersonalDigestAction.__init__` and store it:

```python
    def __init__(self, personal_store, state_store, by_name, get_json, runner, poster,
                 default_cadence: str, tz: str, membership=deny_membership) -> None:
        self._subs = personal_store
        self._state = state_store
        self._by_name = by_name
        self._get_json = get_json
        self._runner = runner
        self._poster = poster
        self._default_cadence = default_cadence
        self._tz = tz
        self._membership = membership
        self.label = "personal-digest"
```

(c) Replace the binding filter in `_maybe_run_user`. Change:

```python
        names = await self._subs.list_for(user_id)
        bindings = [
            self._by_name[n] for n in names
            if n in self._by_name and is_open_tier(self._by_name[n])
        ]
```
to:
```python
        names = await self._subs.list_for(user_id)
        bindings = []
        for n in names:
            b = self._by_name.get(n)
            if b is None:
                continue
            if is_open_tier(b) or await self._membership(user_id, b.channel_id):
                bindings.append(b)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal_digest.py -q`
Expected: PASS. The existing `test_private_project_filtered_at_send_time` still passes — it builds `PersonalDigestAction` without a `membership` arg, so the default `deny_membership` keeps `Secret` filtered out.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_personal_digest.py
git commit -m "feat(digest): include private projects in personal digest iff channel member"
```

---

### Task 5: Wire the live oracle in `app.py`

**Files:**
- Modify: `src/babbla/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `make_membership` (Task 1), `Orchestrator(membership=)` (Task 3), `PersonalDigestAction(membership=)` (Task 4).
- Produces: `build_orchestrator(..., client=None)` accepts an optional Slack client and wires a real oracle when present; `build_scheduler` builds a real oracle from its `client` and passes it to `PersonalDigestAction`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py` (it already imports `build_orchestrator`, `load_secrets`, and defines `ENV`; add the `deny_membership` import at the top of the file):

```python
from babbla.membership import deny_membership


def test_build_orchestrator_without_client_uses_deny_default(tmp_path):
    # With no Slack client, private stays locked: the orchestrator's oracle is deny_membership.
    cfg = tmp_path / "channels.yaml"
    cfg.write_text(
        "projects:\n  - name: MyTV\n    owner: Wkkkkk\n    repo: MyTV\n"
        "    visibility: public\n    channel_id: C123\n    dm: true\n"
    )
    orch = build_orchestrator(
        config_path=str(cfg), db_path=str(tmp_path / "s.db"), secrets=load_secrets(ENV)
    )
    assert orch._membership is deny_membership
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -k deny_default -q`
Expected: FAIL — `build_orchestrator()` has no `client` handling yet, or `_membership` is unset → AttributeError, or signature mismatch. (If it already passes because `deny_membership` is the `__init__` default, that's acceptable — proceed; the real value of this task is Step 3 wiring.)

- [ ] **Step 3: Write the implementation**

In `src/babbla/app.py`:

(a) Add imports:

```python
from babbla.membership import make_membership
```

(b) Give `build_orchestrator` an optional `client` and wire the oracle. Change the signature:

```python
def build_orchestrator(*, config_path: str, db_path: str, secrets: Secrets, get_json=None, client=None) -> Orchestrator:
```

Build the oracle once, after `personal_store` is created:

```python
    membership = make_membership(client) if client is not None else deny_membership
```

Add `from babbla.membership import deny_membership, make_membership` (combine the import). Pass `membership=membership` into **both** `Orchestrator(...)` return paths in this function:

```python
        return Orchestrator(
            config, runner, store,
            personal_store=personal_store, personal_default_cadence=default_cadence,
            intent_fn=intent_fn, membership=membership,
        )
```
and
```python
    return Orchestrator(
        config, runner, store,
        catalog=catalog,
        classify_fn=make_classify_fn(_sdk_query, secrets.classifier),
        lobby_store=LobbyThreadStore(db_path),
        personal_store=personal_store,
        personal_default_cadence=default_cadence,
        intent_fn=intent_fn,
        membership=membership,
    )
```

(c) In `build_scheduler`, build the oracle from its `client` and pass it to `PersonalDigestAction`. Find the `PersonalDigestAction(...)` construction and add `membership`:

```python
        membership = make_membership(client)
        actions.append(
            PersonalDigestAction(
                personal_store, personal_state, by_name, get_json, digest_runner, poster,
                config.personal_digest.default_cadence, config.personal_digest.tz,
                membership=membership,
            )
        )
```

(d) **Reorder the run assembly** so `app.client` exists before `build_orchestrator`. In the run/main function, the current order is:

```python
    orchestrator = build_orchestrator(config_path=config_path, db_path=db_path, secrets=secrets)
    ...
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    register_handlers(...)
    scheduler = build_scheduler(config=config, secrets=secrets, db_path=db_path, client=app.client)
```

Change to create `app` first, then pass its client into `build_orchestrator`:

```python
    app = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"])
    orchestrator = build_orchestrator(
        config_path=config_path, db_path=db_path, secrets=secrets, client=app.client
    )
    register_handlers(...)
    scheduler = build_scheduler(config=config, secrets=secrets, db_path=db_path, client=app.client)
```

(Keep `register_handlers(...)` arguments exactly as they were.)

- [ ] **Step 4: Run the app + full suite**

Run: `python -m pytest tests/test_app.py -q && python -m pytest -q`
Expected: PASS across the whole suite.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/app.py tests/test_app.py
git commit -m "feat(app): wire live channel-membership oracle into asks and personal digest"
```

---

### Task 6: Final verification + ADR/scope note

**Files:**
- Modify: none (verification) — optionally `docs/adr/0017-*.md` status note if needed.

- [ ] **Step 1: Full suite green**

Run: `python -m pytest -q`
Expected: PASS (suite count ≥ prior baseline + the new tests).

- [ ] **Step 2: Grep for leftover references**

Run: `grep -rn "render_private_refused" src/ tests/`
Expected: no matches (the function and all callers are gone).

- [ ] **Step 3: Confirm the deploy note**

Confirm `groups:read` is listed as a required Slack scope in the deploy docs (`DEPLOY.md` / scopes section). If absent, add a one-line note: "`groups:read` — read private-channel membership for private personal subscriptions (ADR 0017)."

- [ ] **Step 4: Commit (if docs changed)**

```bash
git add -A
git commit -m "docs: note groups:read scope for private personal subscriptions"
```

---

## Self-Review

**Spec coverage:**
- Membership oracle (fail-closed, TTL, groups:read) → Task 1. ✓
- Pure `authorize_personal` truth table → Task 2. ✓
- Orchestrator subscribe / topic / DM gates + remove `render_private_refused` → Task 3. ✓
- `PersonalDigestAction` per-user filter → Task 4. ✓
- `app.py` wiring + deny-by-default stub + ordering fix → Task 5. ✓
- Read-only preserved / discovery non-leak / no auto-unsubscribe → enforced by reusing `_pointer` + open-tier-only advertising (unchanged) and covered by Task 3 tests. ✓
- `groups:read` deploy note → Task 6. ✓

**Placeholder scan:** No TBD/TODO. Every test and implementation step shows complete code. Task 5's test reuses the verified `ENV` / `load_secrets` / `channels.yaml` pattern already in `tests/test_app.py`.

**Type consistency:** `MembershipFn = Callable[[str, str | None], Awaitable[bool]]` used consistently; `make_membership` / `deny_membership` names match across Tasks 1, 3, 4, 5; `authorize_personal(binding, *, is_member: bool)` signature identical in Tasks 2 and 3; `PersonalDigestAction(..., membership=...)` and `Orchestrator(..., membership=...)` keyword names match their construction sites.
