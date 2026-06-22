# Onboarding Gate + Multi-Project Follow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unsubscribed DM users who ask a question get an onboarding redirect (follow a project first), and `follow A, B and C` / `unfollow X and Y` work in one message.

**Architecture:** Two independent, pure-logic changes to the DM subscription flow. (A) A `projects` *computed property* on `Command` splits the existing `arg` on commas, so the parser is untouched and the orchestrator's dispatch loops over names. (B) An onboarding gate in `handle_ask()` returns redirect text (no agent run) when a DM user has zero subscriptions, sitting after command classification (so "follow X" still works) and before the default binding.

**Tech Stack:** Python 3, pytest (async via pytest-asyncio, already configured), dataclasses.

## Global Constraints

- **Comma (`,`) is the canonical multi-follow delimiter.** Project names may be multi-word ("Stream Starter") so spaces are unsafe split points; names contain no commas. An optional space after a comma is tolerated (trim each piece).
- **Followable predicate is `is_open_tier`** (from `babbla.access`) — the same predicate the subscribe path already advertises with (orchestrator line 105). Do NOT re-implement it; do NOT name private projects in any advertised list.
- **Onboarding list example uses `mytv, babbla, agentic-engineering-kit`** — never "Stream Starter" or "Simulcast". (These come from config at runtime; tests pass controlled lists.)
- **Onboarding reply = prompt + bulleted list of followable projects** + a `follow a, b` usage example built from the first two followable names.
- **Best-effort multi-follow** (not all-or-nothing): subscribe the valid ones, report skipped (unknown / private) with reasons. Symmetric for unfollow.
- **Single-project results keep the existing single renderers** (`render_subscribed`, `render_unknown_project`, `decision.pointer`, `render_unsubscribed`). Only multi-project (>1 name) uses the new "many" renderers.
- **Back-compat:** when `self._personal_store is None`, behavior is unchanged (gate does not fire; falls to default DM binding).
- TDD throughout: failing test first, minimal impl, run, commit per task.

---

### Task 1: `Command.projects` computed property

**Files:**
- Modify: `src/babbla/personal.py:15-23` (the `Command` dataclass)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Command.projects -> tuple[str, ...]` — splits `self.arg` on `,`, trims, drops empties. `arg is None` → `()`. Used by Task 4's dispatch loop.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_personal.py` (near the other `parse_command` tests):

```python
def test_command_projects_splits_on_comma():
    assert personal.parse_command("subscribe mytv, babbla, agentic-engineering-kit").projects == (
        "mytv", "babbla", "agentic-engineering-kit"
    )


def test_command_projects_single_is_one_tuple():
    assert personal.parse_command("subscribe MyTV").projects == ("MyTV",)


def test_command_projects_multiword_single_name_survives():
    assert personal.parse_command("subscribe Stream Starter").projects == ("Stream Starter",)


def test_command_projects_tolerates_spacing_and_trailing_comma():
    assert personal.parse_command("subscribe a ,  b ,").projects == ("a", "b")


def test_command_projects_none_arg_is_empty():
    assert personal.Command("list").projects == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_personal.py::test_command_projects_splits_on_comma -v`
Expected: FAIL — `AttributeError: 'Command' object has no attribute 'projects'`

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/personal.py`, add a property to the `Command` dataclass (after the fields, inside the class):

```python
@dataclass(frozen=True)
class Command:
    verb: str               # subscribe | unsubscribe | list | digest | help
                            # | topic-add | topic-remove | topic-list
    arg: str | None = None  # project name(s) (sub/unsub) or cadence (digest)
    project: str | None = None
    name: str | None = None
    description: str | None = None

    @property
    def projects(self) -> tuple[str, ...]:
        """Comma-separated project names from `arg` (sub/unsubscribe).

        Comma is the delimiter because names may be multi-word but never
        contain commas. A single name yields a 1-tuple, preserving
        single-follow behavior.
        """
        if self.arg is None:
            return ()
        return tuple(p.strip() for p in self.arg.split(",") if p.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal.py -k command_projects -v`
Expected: 5 PASS. Then `python -m pytest tests/test_personal.py -v` — all still green (parser untouched).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): Command.projects splits arg on comma for multi-follow"
```

---

### Task 2: Classifier emits comma-delimited multi form

**Files:**
- Modify: `src/babbla/personal.py` `make_intent_fn` system prompt (the multi-line string ~lines 90-128)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: `Command.projects` (Task 1).
- Produces: nothing new — verifies `classify_intent` routes a comma-canonical reply (`subscribe A, B, C`) through `parse_command` into a multi-`projects` `Command`. The prompt text itself is not asserted (it's LLM input); the test pins the parse path the prompt now targets.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_personal.py` (near the other `classify_intent` tests; `_intent` helper already exists in that file):

```python
async def test_classify_intent_subscribe_multiple_comma_form():
    cmd = await personal.classify_intent(
        "follow A, B and C", ["A", "B", "C"], _intent("subscribe A, B, C")
    )
    assert cmd.verb == "subscribe"
    assert cmd.projects == ("A", "B", "C")


async def test_classify_intent_unsubscribe_multiple_comma_form():
    cmd = await personal.classify_intent(
        "unfollow X and Y", ["X", "Y"], _intent("unsubscribe X, Y")
    )
    assert cmd.verb == "unsubscribe"
    assert cmd.projects == ("X", "Y")
```

- [ ] **Step 2: Run test to verify it passes parse but to confirm baseline**

Run: `python -m pytest tests/test_personal.py -k "multiple_comma_form" -v`
Expected: PASS already — `parse_command` + the Task 1 property handle the comma form, and `_command_line` accepts the `subscribe`/`unsubscribe` verb. (These tests guard against regression; the prompt change in Step 3 makes the LLM actually emit this form.)

> Note: this task has no red phase in the parser because Task 1 already enables the comma form. The deliverable is the prompt change (Step 3); the tests lock the contract the prompt targets.

- [ ] **Step 3: Update the classifier system prompt**

In `src/babbla/personal.py` `make_intent_fn`, update the output-grammar and examples in `system_prompt` so the model emits a comma-delimited canonical form for multiple projects. Change the two grammar lines and add multi examples:

Find:
```python
            "  subscribe <project name>\n"
            "  unsubscribe <project name>\n"
```
Replace with:
```python
            "  subscribe <project name>[, <project name>, ...]\n"
            "  unsubscribe <project name>[, <project name>, ...]\n"
```

Then find the mapping examples block:
```python
            "  'follow MyTV' / 'subscribe me to MyTV' / 'add MyTV'        -> subscribe MyTV\n"
            "  'stop following MyTV' / 'drop MyTV' / 'mute MyTV'          -> unsubscribe MyTV\n"
```
Replace with (adds the multi examples; single examples remain valid):
```python
            "  'follow MyTV' / 'subscribe me to MyTV' / 'add MyTV'        -> subscribe MyTV\n"
            "  'follow A, B and C' / 'subscribe me to A, B, C'           -> subscribe A, B, C\n"
            "  'stop following MyTV' / 'drop MyTV' / 'mute MyTV'          -> unsubscribe MyTV\n"
            "  'unfollow X and Y' / 'drop X and Y'                       -> unsubscribe X, Y\n"
```

Then, just before the `"Copy a project name EXACTLY as written in the list. "` sentence, insert:
```python
            "When the user names MULTIPLE projects, separate them with COMMAS in your "
            "output (e.g. `subscribe A, B, C`) — commas are the delimiter because names "
            "may contain spaces. "
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal.py -v`
Expected: all PASS (incl. the two new multi tests and all existing `classify_intent` tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): teach classifier comma-delimited multi-follow form"
```

---

### Task 3: Multi-result render helpers

**Files:**
- Modify: `src/babbla/personal.py` (add two render functions, near `render_subscribed`/`render_unsubscribed`)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 4):
  - `render_subscribed_many(subscribed: list[str], skipped: list[tuple[str, str]]) -> str` — `skipped` items are `(name, reason)` with `reason ∈ {"private", "unknown"}`.
  - `render_unsubscribed_many(removed: list[str], skipped: list[tuple[str, str]]) -> str` — `skipped` reasons `∈ {"unknown", "not following"}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_personal.py`:

```python
def test_render_subscribed_many_successes_and_skips():
    out = personal.render_subscribed_many(
        ["mytv", "babbla"], [("Secret", "private"), ("Foo", "unknown")]
    )
    assert "mytv" in out and "babbla" in out
    assert "✅" in out
    assert "Secret" in out and "private" in out
    assert "Foo" in out and "don't know" in out.lower()


def test_render_subscribed_many_all_skipped_has_no_success_line():
    out = personal.render_subscribed_many([], [("Foo", "unknown")])
    assert "✅" not in out
    assert "Foo" in out


def test_render_unsubscribed_many_successes_and_skips():
    out = personal.render_unsubscribed_many(
        ["mytv"], [("Foo", "unknown"), ("babbla", "not following")]
    )
    assert "mytv" in out
    assert "Foo" in out and "don't know" in out.lower()
    assert "babbla" in out and "not following" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_personal.py -k "render_subscribed_many or render_unsubscribed_many" -v`
Expected: FAIL — `AttributeError: module 'babbla.personal' has no attribute 'render_subscribed_many'`

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/personal.py`, after `render_unsubscribed` (around line 149), add:

```python
_SKIP_REASONS = {
    "private": "private",
    "unknown": "don't know that one",
    "not following": "not following that one",
}


def _and_join(names: Sequence[str]) -> str:
    """'a' / 'a and b' / 'a, b and c' with each name *emphasised*."""
    marked = [f"*{n}*" for n in names]
    if len(marked) <= 1:
        return marked[0] if marked else ""
    return ", ".join(marked[:-1]) + " and " + marked[-1]


def _skip_clause(skipped: Sequence[tuple[str, str]]) -> str:
    parts = [f'"{name}" ({_SKIP_REASONS.get(reason, reason)})' for name, reason in skipped]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"⚠️ Skipped {joined}."


def render_subscribed_many(subscribed: Sequence[str], skipped: Sequence[tuple[str, str]]) -> str:
    lines = []
    if subscribed:
        lines.append(f"✅ Subscribed to {_and_join(subscribed)}.")
    skip = _skip_clause(skipped)
    if skip:
        lines.append(skip)
    return "\n".join(lines) if lines else "Nothing to do."


def render_unsubscribed_many(removed: Sequence[str], skipped: Sequence[tuple[str, str]]) -> str:
    lines = []
    if removed:
        lines.append(f"Unsubscribed from {_and_join(removed)}.")
    skip = _skip_clause(skipped)
    if skip:
        lines.append(skip)
    return "\n".join(lines) if lines else "Nothing to do."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal.py -k "render_subscribed_many or render_unsubscribed_many" -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): render helpers for multi-follow successes and skips"
```

---

### Task 4: Multi-follow/unfollow dispatch loop

**Files:**
- Modify: `src/babbla/orchestrator.py` `_dispatch_command` (subscribe branch ~lines 100-111; unsubscribe ~lines 112-114)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Command.projects` (Task 1), `render_subscribed_many` / `render_unsubscribed_many` (Task 3), existing `_authorize_personal`, `is_open_tier`, `personal_store.add/remove/list_for`.
- Produces: multi-project subscribe/unsubscribe behavior. Single-project (`len(cmd.projects) <= 1`) path is byte-unchanged (existing renderers).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_orchestrator.py` (the file already has `_config_two()`, `psub`, `store` fixtures and a member-oracle pattern; `_config_two` = public "MyTV" + private "Secret"):

```python
async def test_dispatch_subscribe_many_partitions_valid_unknown_private(store, psub):
    # default (deny) oracle: "Secret" is private and the user is not a member
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    reply = await orch.handle_command("U1", "subscribe MyTV, Secret, Ghost")
    assert await psub.list_for("U1") == ("MyTV",)          # only the valid open-tier one added
    assert "MyTV" in reply
    assert "Secret" in reply and "private" in reply
    assert "Ghost" in reply and "don't know" in reply.lower()


async def test_dispatch_subscribe_many_dedupes_already_followed(store, psub):
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    reply = await orch.handle_command("U1", "subscribe MyTV, MyTV")
    assert await psub.list_for("U1") == ("MyTV",)          # no duplicate row


async def test_dispatch_subscribe_many_private_allowed_for_member(store, psub):
    async def member(uid, cid):
        return True
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub,
                        membership=member)
    reply = await orch.handle_command("U1", "subscribe MyTV, Secret")
    assert set(await psub.list_for("U1")) == {"MyTV", "Secret"}
    assert "MyTV" in reply and "Secret" in reply


async def test_dispatch_unsubscribe_many(store, psub):
    # MyTV: followed → removed. Secret: exists in config but not followed →
    # "not following". Ghost: no binding → "unknown".
    orch = Orchestrator(_config_two(), FakeRunner(), store, personal_store=psub)
    await psub.add("U1", "MyTV")
    reply = await orch.handle_command("U1", "unsubscribe MyTV, Secret, Ghost")
    assert await psub.list_for("U1") == ()                 # MyTV removed
    assert "MyTV" in reply
    assert "Ghost" in reply and "don't know" in reply.lower()
    assert "Secret" in reply and "not following" in reply.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator.py -k "subscribe_many or unsubscribe_many" -v`
Expected: FAIL — current single-project dispatch treats `"MyTV, Secret, Ghost"` as one unknown project name.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/orchestrator.py`, replace the `subscribe` branch and the `unsubscribe` tail of `_dispatch_command` (lines 100-114) with:

```python
        if cmd.verb == "subscribe":
            if len(cmd.projects) > 1:
                return await self._subscribe_many(user_id, cmd.projects)
            name = cmd.projects[0] if cmd.projects else cmd.arg
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                # Advertise only open-tier projects — never name a private one.
                return personal.render_unknown_project(
                    [b.name for b in self._config.bindings if is_open_tier(b)]
                )
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                return decision.pointer
            await self._personal_store.add(user_id, binding.name)
            return personal.render_subscribed(binding.name)
        # unsubscribe
        if len(cmd.projects) > 1:
            return await self._unsubscribe_many(user_id, cmd.projects)
        await self._personal_store.remove(user_id, cmd.projects[0] if cmd.projects else cmd.arg)
        return personal.render_unsubscribed(cmd.projects[0] if cmd.projects else cmd.arg)
```

Then add the two helpers as methods on `Orchestrator` (e.g. directly after `_dispatch_command`):

```python
    async def _subscribe_many(self, user_id: str, names) -> str:
        followed = set(await self._personal_store.list_for(user_id))
        subscribed: list[str] = []
        skipped: list[tuple[str, str]] = []
        for name in names:
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                skipped.append((name, "unknown"))
                continue
            decision = await self._authorize_personal(user_id, binding)
            if not decision.allowed:
                skipped.append((binding.name, "private"))
                continue
            if binding.name in followed:
                continue                       # already followed — dedupe silently
            await self._personal_store.add(user_id, binding.name)
            followed.add(binding.name)
            subscribed.append(binding.name)
        return personal.render_subscribed_many(subscribed, skipped)

    async def _unsubscribe_many(self, user_id: str, names) -> str:
        followed = set(await self._personal_store.list_for(user_id))
        removed: list[str] = []
        skipped: list[tuple[str, str]] = []
        for name in names:
            binding = next((b for b in self._config.bindings if b.name == name), None)
            if binding is None:
                skipped.append((name, "unknown"))
                continue
            if binding.name not in followed:
                skipped.append((binding.name, "not following"))
                continue
            await self._personal_store.remove(user_id, binding.name)
            followed.discard(binding.name)
            removed.append(binding.name)
        return personal.render_unsubscribed_many(removed, skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -k "subscribe_many or unsubscribe_many" -v`
Expected: all PASS. Then `python -m pytest tests/test_orchestrator.py -v` — existing single-project dispatch tests still green.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): best-effort multi-follow/unfollow dispatch loop"
```

---

### Task 5: Onboarding render function

**Files:**
- Modify: `src/babbla/personal.py` (add `render_no_subscriptions`)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 6): `render_no_subscriptions(followable_names: Sequence[str]) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_personal.py`:

```python
def test_render_no_subscriptions_lists_followable_and_example():
    out = personal.render_no_subscriptions(["mytv", "babbla", "agentic-engineering-kit"])
    assert "follow" in out.lower()
    assert "• mytv" in out
    assert "• babbla" in out
    assert "• agentic-engineering-kit" in out
    assert "follow mytv, babbla" in out          # teaches comma multi-syntax, first two names


def test_render_no_subscriptions_single_followable_example():
    out = personal.render_no_subscriptions(["mytv"])
    assert "• mytv" in out
    assert "follow mytv" in out


def test_render_no_subscriptions_empty_is_graceful():
    out = personal.render_no_subscriptions([])
    assert "•" not in out
    assert "follow" not in out.lower() or "aren't any" in out.lower()
    assert "aren't any" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_personal.py -k render_no_subscriptions -v`
Expected: FAIL — `AttributeError: ... has no attribute 'render_no_subscriptions'`

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/personal.py`, add (near the other render functions):

```python
def render_no_subscriptions(followable_names: Sequence[str]) -> str:
    """Onboarding redirect for a DM user who follows nothing yet."""
    if not followable_names:
        return "There aren't any projects available to follow yet."
    bullets = "\n".join(f"• {n}" for n in followable_names)
    example = ", ".join(followable_names[:2])
    return (
        "I don't have any projects to look into for you yet — follow one first "
        "and I'll answer your questions about it.\n\n"
        "Projects you can follow:\n"
        f"{bullets}\n\n"
        f"Just say: `follow {example}`"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_personal.py -k render_no_subscriptions -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): render_no_subscriptions onboarding redirect"
```

---

### Task 6: Onboarding gate in `handle_ask`

**Files:**
- Modify: `src/babbla/orchestrator.py` `handle_ask` (the DM personal-ask block, lines 133-139)
- Test: `tests/test_orchestrator.py` (add new tests; **rewrite** `test_dm_empty_subs_falls_back_to_dm_true` at line 369)

**Interfaces:**
- Consumes: `render_no_subscriptions` (Task 5), `is_open_tier`, `personal_store.list_for`.
- Produces: gate behavior. Fires only when `is_dm` and `personal_store is not None` and `user_id is not None` and `list_for(user_id)` is empty → returns `CitedAnswer(text=render_no_subscriptions(...), session_id=None)`, no runner call.

- [ ] **Step 1: Write the failing tests + rewrite the obsolete one**

First, **rewrite** the existing test (currently at `tests/test_orchestrator.py:369`) — the old fall-back-to-`for_dm` behavior is now intentionally dead for DM Q&A:

```python
async def test_dm_empty_subs_hits_onboarding_gate(store, psub):
    # Unsubscribed DM question → onboarding redirect, no agent run.
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub, catalog=_catalog_two())
    runner = orch._runner
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert ans.session_id is None
    assert runner.calls == []                       # default DM binding NOT reached
    assert "follow" in ans.text.lower()
    assert "MyTV" in ans.text                        # CONFIG's open-tier project advertised
```

Then add new gate tests:

```python
async def test_dm_unsubscribed_follow_command_still_works(store, psub):
    # Command classification precedes the gate, so "follow MyTV" subscribes.
    runner = FakeRunner()
    orch = Orchestrator(CONFIG, runner, store, personal_store=psub,
                        intent_fn=_intent_fn("subscribe MyTV"))
    ans = await orch.handle_ask(
        text="follow MyTV", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1",
    )
    assert await psub.list_for("U1") == ("MyTV",)
    assert runner.calls == []
    assert "MyTV" in ans.text


async def test_dm_subscribed_question_unchanged(store, psub):
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub,
                        catalog=_catalog_two(), lobby_store=_FakeLobbyStore())
    await psub.add("U1", "MyTV")
    ans = await orch.handle_ask(text="why HLS", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert ans.text == "answer to why HLS"           # routed to the Q&A agent as before


async def test_channel_ask_never_hits_onboarding_gate(store, psub):
    # is_dm False → gate does not apply; normal channel Ask runs.
    orch = Orchestrator(CONFIG, FakeRunner(), store, personal_store=psub)
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="C123", is_dm=False, user_id="U1")
    assert ans.text == "answer to q"


async def test_dm_no_personal_store_unchanged(store):
    # personal_store None → back-compat: falls to default DM binding, runs agent.
    orch = Orchestrator(CONFIG, FakeRunner(), store)
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert ans.text == "answer to q"


async def test_dm_onboarding_gate_empty_followable_is_graceful(store, psub):
    # A config whose only binding is private → no followable names → graceful variant.
    priv_only = Config(bindings=(ProjectBinding("Secret", "o", "secret", "private", "C2", True),))
    orch = Orchestrator(priv_only, FakeRunner(), store, personal_store=psub)
    ans = await orch.handle_ask(text="q", thread_ts="t1", channel_id="D1", is_dm=True, user_id="U1")
    assert orch._runner.calls == []
    assert "aren't any" in ans.text.lower()
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python -m pytest tests/test_orchestrator.py -k "onboarding_gate or empty_subs_hits or unsubscribed_follow_command" -v`
Expected: `test_dm_empty_subs_hits_onboarding_gate` and `test_dm_onboarding_gate_empty_followable_is_graceful` FAIL (runner IS currently called); the `follow_command` test may already pass (intent precedes). The gate code does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `src/babbla/orchestrator.py` `handle_ask`, replace the current Check-2 block (lines 133-139):

```python
        if is_dm and self._personal_store is not None and user_id is not None and self._catalog:
            names = await self._personal_store.list_for(user_id)
            entries = subscriptions.entries_for(self._catalog, names) if names else ()
            if entries:
                return await self._handle_personal_ask(
                    text=text, thread_ts=thread_ts, entries=entries, user_id=user_id
                )
```

with (compute `names` once; gate on empty, route on non-empty):

```python
        if is_dm and self._personal_store is not None and user_id is not None:
            names = await self._personal_store.list_for(user_id)
            if not names:
                # Onboarding gate: an unsubscribed DM user is redirected to follow
                # a project first — no agent run, no default-binding Q&A.
                followable = [b.name for b in self._config.bindings if is_open_tier(b)]
                return CitedAnswer(text=personal.render_no_subscriptions(followable), session_id=None)
            if self._catalog:
                entries = subscriptions.entries_for(self._catalog, names)
                if entries:
                    return await self._handle_personal_ask(
                        text=text, thread_ts=thread_ts, entries=entries, user_id=user_id
                    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: all PASS, including the rewritten and new gate tests.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green. If any other test exercised an empty-subscription DM with a `personal_store` and expected a `for_dm` fallback, update it the same way (search: `is_dm=True` + `personal_store` + asserts a runner call with empty subs).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): onboarding gate redirects unsubscribed DM askers"
```

---

## Self-Review notes

- **Spec coverage:** Change 1 (gate) → Tasks 5-6. Change 2 (multi-follow) → Tasks 1-4. Onboarding message format, empty-list variant, comma delimiter, best-effort partition, single-name legacy renderers, `is_open_tier` reuse, back-compat (`personal_store is None`) — all have tasks/tests.
- **`for_dm()` dead-path:** With the gate, `for_dm()` is unreachable for DM Q&A when personal subs are enabled (empty→gate, non-empty→personal ask). It remains the back-compat path when `personal_store is None`. No code removed (Task 6's `test_dm_no_personal_store_unchanged` pins this). The only DM consumer of `_resolve` is `handle_ask`.
- **Type consistency:** `Command.projects -> tuple[str, ...]` (Task 1) consumed in Task 4; `render_*_many(list, list[tuple[str,str]])` (Task 3) consumed in Task 4; `render_no_subscriptions(Sequence[str])` (Task 5) consumed in Task 6.
- **No private-name leak:** every advertised list uses `is_open_tier` (onboarding gate, single-unknown render). Multi-follow skip of a private project names it only because the user typed it themselves (already known to them).
