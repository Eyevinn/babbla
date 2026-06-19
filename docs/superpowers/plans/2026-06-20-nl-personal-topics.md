# Natural-Language Personal Topics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user create, list, and remove per-project thematic filters ("topics") on their Personal Digest, entirely in natural language in a DM.

**Architecture:** Topics persist in a new `personal_topics` table on the existing `PersonalSubStore`. The existing DM intent classifier (`personal.py`) gains topic intents and auto-expands a bare topic name into a steering description. `Orchestrator._dispatch_command` validates (known + open-tier + followed) and writes. `PersonalDigestAction` loads the user's topics and threads a per-project topic map into `summarize_shared`, which filters each project's section to the union of its topics; a folded-in empty-post guard skips a blank DM while still advancing the watermark.

**Tech Stack:** Python 3.14, `sqlite3` (+ `asyncio.to_thread`), `claude_agent_sdk`, `pytest` / `pytest-asyncio`.

## Global Constraints

- Read-only toward all projects (ADR 0003): topics are per-user state Babbla writes about itself only. No new write toward any repo.
- No new top-level config and no `config/channels.yaml` change. No new Slack command or event — NL-in-DM only.
- Per-user state lives in **one** store (`PersonalSubStore`); do not add a second store class.
- Topic identity is `(user_id, project_name, name)` with `name` normalized (`str.strip().casefold()`).
- Union filtering: a project section includes changes relevant to ANY of its topics; a followed project with no topic is unfiltered (unchanged behavior).
- When `topics_by_project` is absent/empty AND `topic` is None, `summarize_shared` must produce a byte-for-byte identical prompt to today (no regression for existing shared/personal callers).
- All tests deterministic: injected fakes (classifier `intent_fn`, runner, poster, stores). No network, no real model.
- Work on branch `feat/nl-personal-topics` (already created; the spec commit `ed91be4` is its first commit).
- Run the full suite with `source .venv/bin/activate` first; baseline is **319 passed, 2 skipped**.

---

### Task 1: `personal_topics` table + topic methods on `PersonalSubStore`

**Files:**
- Modify: `src/babbla/session_store.py` (add schema constant near `_PERSONAL_PREFS_SCHEMA:277`; add methods to `PersonalSubStore` class, before `close` at `:348`)
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (on `PersonalSubStore`):
  - `async add_topic(user_id: str, project: str, name: str, description: str) -> None` — upsert; `name` normalized; re-adding an existing `(user_id, project, name)` updates `description`.
  - `async remove_topic(user_id: str, project: str, name: str) -> None` — delete by normalized `name`; idempotent.
  - `async topics_for(user_id: str) -> dict[str, tuple[tuple[str, str], ...]]` — `{project_name: ((name, description), …)}`, ordered by `created_at, project_name, name`; `{}` when none.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_store.py`:

```python
from babbla.session_store import PersonalSubStore


async def test_personal_topics_add_list_remove(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "security", "auth, secrets, CVEs")
    await s.add_topic("U1", "MyTV", "playback", "HLS, player, buffering")
    await s.add_topic("U1", "Babbla", "lobby", "routing, classifier")
    topics = await s.topics_for("U1")
    assert topics == {
        "MyTV": (("security", "auth, secrets, CVEs"), ("playback", "HLS, player, buffering")),
        "Babbla": (("lobby", "routing, classifier"),),
    }
    await s.remove_topic("U1", "MyTV", "security")
    assert (await s.topics_for("U1"))["MyTV"] == (("playback", "HLS, player, buffering"),)
    s.close()


async def test_personal_topics_readd_updates_description_and_normalizes(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "Security", "first")
    await s.add_topic("U1", "MyTV", "  security ", "second")   # same identity, normalized
    topics = await s.topics_for("U1")
    assert topics == {"MyTV": (("security", "second"),)}        # one row, updated desc
    s.close()


async def test_personal_topics_isolated_per_user(tmp_path):
    s = PersonalSubStore(str(tmp_path / "t.db"))
    await s.add_topic("U1", "MyTV", "security", "x")
    assert await s.topics_for("U2") == {}
    s.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_store.py -k personal_topics -v`
Expected: FAIL — `AttributeError: 'PersonalSubStore' object has no attribute 'add_topic'`.

- [ ] **Step 3: Add the schema constant**

In `src/babbla/session_store.py`, after `_PERSONAL_PREFS_SCHEMA` (ends `:277`):

```python
_PERSONAL_TOPICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_topics (
    user_id      TEXT NOT NULL,
    project_name TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, project_name, name)
)
"""
```

- [ ] **Step 4: Create the table in `__init__`**

In `PersonalSubStore.__init__` (`:283-288`), add after the `_PERSONAL_PREFS_SCHEMA` execute line:

```python
        self._conn.execute(_PERSONAL_TOPICS_SCHEMA)
```

(Keep the single `self._conn.commit()` that follows.)

- [ ] **Step 5: Add the methods**

In `PersonalSubStore`, immediately before `def close` (`:348`):

```python
    @staticmethod
    def _norm(name: str) -> str:
        return (name or "").strip().casefold()

    async def add_topic(self, user_id: str, project: str, name: str, description: str) -> None:
        await asyncio.to_thread(self._add_topic_sync, user_id, project, name, description)

    def _add_topic_sync(self, user_id: str, project: str, name: str, description: str) -> None:
        self._conn.execute(
            "INSERT INTO personal_topics (user_id, project_name, name, description, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, project_name, name) DO UPDATE SET description = excluded.description",
            (user_id, project, self._norm(name), description, self._now()),
        )
        self._conn.commit()

    async def remove_topic(self, user_id: str, project: str, name: str) -> None:
        await asyncio.to_thread(self._remove_topic_sync, user_id, project, name)

    def _remove_topic_sync(self, user_id: str, project: str, name: str) -> None:
        self._conn.execute(
            "DELETE FROM personal_topics WHERE user_id = ? AND project_name = ? AND name = ?",
            (user_id, project, self._norm(name)),
        )
        self._conn.commit()

    async def topics_for(self, user_id: str) -> dict:
        return await asyncio.to_thread(self._topics_for_sync, user_id)

    def _topics_for_sync(self, user_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT project_name, name, description FROM personal_topics "
            "WHERE user_id = ? ORDER BY created_at, project_name, name",
            (user_id,),
        ).fetchall()
        out: dict[str, tuple] = {}
        for project, name, description in rows:
            out[project] = out.get(project, ()) + ((name, description),)
        return out
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_session_store.py -k personal_topics -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add src/babbla/session_store.py tests/test_session_store.py
git commit -m "feat(store): personal_topics table + add/remove/list on PersonalSubStore"
```

---

### Task 2: Topic intents in the command grammar (`personal.py`)

**Files:**
- Modify: `src/babbla/personal.py` (`Command` dataclass `:16-19`; `_MGMT_VERBS` `:13`; `parse_command` `:22-37`)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Command` gains optional fields `project: str | None = None`, `name: str | None = None`, `description: str | None = None`.
  - `parse_command` recognizes three internal lines (emitted by the classifier, never typed by users):
    - `topic add <project> | <name> | <description>` → `Command("topic-add", project=…, name=…, description=…)`
    - `topic remove <project> | <name>` → `Command("topic-remove", project=…, name=…)`
    - `topic list` → `Command("topic-list")`
    - any malformed `topic …` → `Command("help")`
  - `_MGMT_VERBS` includes `"topic"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_personal.py`:

```python
from babbla.personal import parse_command


def test_parse_topic_add():
    cmd = parse_command("topic add MyTV | security | auth, secrets, CVEs")
    assert cmd.verb == "topic-add"
    assert cmd.project == "MyTV"
    assert cmd.name == "security"
    assert cmd.description == "auth, secrets, CVEs"


def test_parse_topic_add_multiword_project_and_desc():
    cmd = parse_command("topic add Agentic Kit | rag | retrieval, embeddings | extra")
    assert cmd.verb == "topic-add"
    assert cmd.project == "Agentic Kit"
    assert cmd.name == "rag"
    assert cmd.description == "retrieval, embeddings"   # only first 3 pipe fields used


def test_parse_topic_remove():
    cmd = parse_command("topic remove MyTV | security")
    assert cmd.verb == "topic-remove"
    assert cmd.project == "MyTV"
    assert cmd.name == "security"


def test_parse_topic_list():
    assert parse_command("topic list").verb == "topic-list"


def test_parse_topic_malformed_is_help():
    assert parse_command("topic add MyTV | security").verb == "help"   # missing description
    assert parse_command("topic remove MyTV").verb == "help"           # missing name
    assert parse_command("topic wat").verb == "help"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal.py -k topic -v`
Expected: FAIL — `topic add …` currently returns `Command("help")` (unknown verb), so `cmd.verb == "topic-add"` fails / `cmd.project` is `None`.

- [ ] **Step 3: Extend the `Command` dataclass**

In `src/babbla/personal.py`, replace the dataclass (`:16-19`):

```python
@dataclass(frozen=True)
class Command:
    verb: str               # subscribe | unsubscribe | list | digest | help
                            # | topic-add | topic-remove | topic-list
    arg: str | None = None  # project name (sub/unsub) or cadence (digest)
    project: str | None = None
    name: str | None = None
    description: str | None = None
```

- [ ] **Step 4: Add `topic` to `_MGMT_VERBS`**

Replace `:13`:

```python
_MGMT_VERBS = {"subscribe", "unsubscribe", "list", "subscriptions", "digest", "topic"}
```

- [ ] **Step 5: Handle the `topic` verb in `parse_command`**

In `parse_command`, insert before the final `return Command("help")` (`:37`):

```python
    if verb == "topic":
        body = text.split(None, 2)            # ["topic", sub, "rest..."]
        sub = body[1].lower() if len(body) > 1 else ""
        rest = body[2] if len(body) > 2 else ""
        if sub == "list":
            return Command("topic-list")
        parts = [p.strip() for p in rest.split("|")]
        if sub == "add" and len(parts) >= 3 and all(parts[:3]):
            return Command("topic-add", project=parts[0], name=parts[1], description=parts[2])
        if sub == "remove" and len(parts) >= 2 and all(parts[:2]):
            return Command("topic-remove", project=parts[0], name=parts[1])
        return Command("help")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal.py -k topic -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): parse topic add/remove/list intents"
```

---

### Task 3: Topic reply renderers + classifier prompt (`personal.py`)

**Files:**
- Modify: `src/babbla/personal.py` (add renderers after `render_help` `:148-155`; extend `make_intent_fn` system prompt `:75-97`)
- Test: `tests/test_personal.py`

**Interfaces:**
- Consumes: `Command` from Task 2; `classify_intent` (`:40`) unchanged.
- Produces:
  - `render_topic_added(project: str, name: str, description: str) -> str`
  - `render_topic_removed(project: str, name: str) -> str`
  - `render_topic_list(topics_by_project: dict) -> str`
  - `render_topic_needs_follow(project: str) -> str`
  - `make_intent_fn`'s prompt now also emits the three `topic …` lines and auto-expands a bare topic name into a description.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_personal.py`:

```python
import asyncio
from babbla.personal import (
    render_topic_added, render_topic_removed, render_topic_list,
    render_topic_needs_follow, classify_intent,
)


def test_render_topic_added_shows_name_and_description():
    out = render_topic_added("MyTV", "security", "auth, secrets, CVEs")
    assert "security" in out and "MyTV" in out and "auth, secrets, CVEs" in out


def test_render_topic_list_empty_and_grouped():
    assert "no digest topics" in render_topic_list({}).lower()
    grouped = render_topic_list({"MyTV": (("security", "x"), ("playback", "y"))})
    assert "MyTV" in grouped and "security" in grouped and "playback" in grouped


def test_render_topic_needs_follow():
    out = render_topic_needs_follow("MyTV")
    assert "MyTV" in out and "follow" in out.lower()


def test_classify_intent_maps_topic_add():
    async def fake_intent_fn(text, names):
        return "topic add MyTV | security | auth, secrets, CVEs"
    cmd = asyncio.run(classify_intent("only show me security in MyTV", ["MyTV"], fake_intent_fn))
    assert cmd.verb == "topic-add" and cmd.name == "security"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal.py -k "render_topic or classify_intent_maps_topic" -v`
Expected: FAIL — `ImportError: cannot import name 'render_topic_added'`.

- [ ] **Step 3: Add the renderers**

In `src/babbla/personal.py`, after `render_help` (`:155`):

```python
def render_topic_added(project: str, name: str, description: str) -> str:
    return (
        f"✅ Added topic *{name}* to *{project}* — your digest's *{project}* section will "
        f"now include only changes about _{description}_.\n"
        f"Restate it to refine the description, or say \"remove the {name} topic from "
        f"{project}\" to drop it."
    )


def render_topic_removed(project: str, name: str) -> str:
    return f"Removed topic *{name}* from *{project}*."


def render_topic_list(topics_by_project: dict) -> str:
    if not topics_by_project:
        return (
            "You have no digest topics. In a DM, say something like "
            "\"only show me security changes in MyTV\" and I'll add one."
        )
    lines = []
    for project, topics in topics_by_project.items():
        labels = ", ".join(f"*{n}*" for n, _ in topics)
        lines.append(f"• *{project}*: {labels}")
    return "Your digest topics (your digest is filtered to these per project):\n" + "\n".join(lines)


def render_topic_needs_follow(project: str) -> str:
    return (
        f"You're not following *{project}* yet, so it isn't in your digest. "
        f"Follow it first (e.g. \"subscribe to {project}\"), then add a topic."
    )
```

- [ ] **Step 4: Extend the classifier system prompt**

In `make_intent_fn` (`:75-97`), replace the `system_prompt = (...)` block so the output menu and examples include topics. Use exactly:

```python
        system_prompt = (
            "Classify a single Slack DM and output ONE line, nothing else — no "
            "explanation, no reasoning, no tools, no backticks. The user is either "
            "(a) MANAGING their personal project subscriptions, (b) MANAGING their "
            "personal digest TOPICS (thematic filters on a followed project), or (c) "
            "asking a question about a project. Output EXACTLY one of:\n"
            "  subscribe <project name>\n"
            "  unsubscribe <project name>\n"
            "  list\n"
            "  digest daily   (or: digest weekly | digest off)\n"
            "  topic add <project name> | <topic name> | <description>\n"
            "  topic remove <project name> | <topic name>\n"
            "  topic list\n"
            "  NONE\n\n"
            "Map the user's wording, e.g.:\n"
            "  'follow MyTV' / 'subscribe me to MyTV' / 'add MyTV'        -> subscribe MyTV\n"
            "  'stop following MyTV' / 'drop MyTV' / 'mute MyTV'          -> unsubscribe MyTV\n"
            "  'what am I following?' / 'my subs'                         -> list\n"
            "  'send my digest daily' / 'pause my digest'                -> digest daily|weekly|off\n"
            "  'only show me security in MyTV' / 'filter MyTV to security'\n"
            "        -> topic add MyTV | security | auth, secrets, access control, CVEs, dependency security bumps\n"
            "  'stop filtering MyTV to security' / 'remove the security topic from MyTV'\n"
            "        -> topic remove MyTV | security\n"
            "  'what topics do I have' / 'my filters'                     -> topic list\n"
            "  'how does the digest work?' / 'what's in MyTV?' / 'hi'     -> NONE\n\n"
            "For `topic add`, ALWAYS supply a useful <description>: expand the user's short "
            "topic name into a comma-separated phrase of the concepts it should match, so the "
            "digest can filter on it. Use the user's own description verbatim if they gave one. "
            "Copy a project name EXACTLY as written in the list. A 'what/show/list my "
            "subscriptions' question is `list`; 'what topics do I have' is `topic list`. If the "
            "message is about digest FREQUENCY (daily/weekly/off), it is a `digest` command. "
            "Anything that is a question ABOUT a project's code/history, a greeting, or unclear "
            "is NONE. When genuinely unsure, output NONE.\n\nProjects:\n" + listing
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal.py -v`
Expected: PASS (all, including the existing subscription tests — unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/personal.py tests/test_personal.py
git commit -m "feat(personal): topic reply renderers + classifier topic intents"
```

---

### Task 4: Dispatch topic commands (`orchestrator.py`)

**Files:**
- Modify: `src/babbla/orchestrator.py` (`_dispatch_command` `:60-83`)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `Command` (Task 2), renderers (Task 3), `PersonalSubStore.add_topic/remove_topic/topics_for/list_for` (Task 1), existing `is_open_tier`, `self._config.bindings`, `self._personal_store`.
- Produces: `_dispatch_command` returns reply text for `topic-list` / `topic-add` / `topic-remove`. No signature change.

- [ ] **Step 1: Write the failing tests**

In `tests/test_orchestrator.py`, follow the existing fake/fixture style (a `PersonalSubStore` on a tmp db, a `Config` with bindings, an `Orchestrator` with `personal_store=`). Add:

```python
async def test_dispatch_topic_add_to_followed_project(orch_with_personal):
    orch, store = orch_with_personal                      # MyTV is public, in catalog
    await store.add("U1", "MyTV")                          # following it
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="MyTV", name="security", description="auth, secrets"))
    assert "security" in reply and "auth, secrets" in reply
    assert (await store.topics_for("U1")) == {"MyTV": (("security", "auth, secrets"),)}


async def test_dispatch_topic_add_requires_following(orch_with_personal):
    orch, store = orch_with_personal                      # not following MyTV
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="MyTV", name="security", description="x"))
    assert "follow" in reply.lower()
    assert await store.topics_for("U1") == {}              # nothing written


async def test_dispatch_topic_add_unknown_project(orch_with_personal):
    orch, store = orch_with_personal
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="Nope", name="x", description="y"))
    assert "don't know that project" in reply.lower()
    assert await store.topics_for("U1") == {}


async def test_dispatch_topic_add_private_refused(orch_with_personal_private):
    orch, store = orch_with_personal_private              # "Secret" is private
    reply = await orch._dispatch_command("U1", personal.Command(
        "topic-add", project="Secret", name="x", description="y"))
    assert "private" in reply.lower()
    assert await store.topics_for("U1") == {}


async def test_dispatch_topic_remove_and_list(orch_with_personal):
    orch, store = orch_with_personal
    await store.add("U1", "MyTV")
    await store.add_topic("U1", "MyTV", "security", "auth")
    listed = await orch._dispatch_command("U1", personal.Command("topic-list"))
    assert "MyTV" in listed and "security" in listed
    await orch._dispatch_command("U1", personal.Command("topic-remove", project="MyTV", name="security"))
    assert await store.topics_for("U1") == {}
```

If `orch_with_personal` / `orch_with_personal_private` fixtures do not already exist, add them mirroring the existing personal-subscription orchestrator tests (a `Config` with a public `MyTV` binding — and a private `Secret` binding for the private fixture — plus `Orchestrator(..., personal_store=PersonalSubStore(tmp))`). Import `from babbla import personal`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_orchestrator.py -k topic -v`
Expected: FAIL — `_dispatch_command` falls through to the `unsubscribe` tail (`cmd.arg` is `None`) and returns the wrong reply / writes nothing.

- [ ] **Step 3: Add the topic cases**

In `src/babbla/orchestrator.py`, in `_dispatch_command`, insert after the `digest` block (`:69`) and before the `subscribe` block:

```python
        if cmd.verb == "topic-list":
            topics = await self._personal_store.topics_for(user_id)
            return personal.render_topic_list(topics)
        if cmd.verb in ("topic-add", "topic-remove"):
            binding = next((b for b in self._config.bindings if b.name == cmd.project), None)
            if binding is None:
                return personal.render_unknown_project(
                    [b.name for b in self._config.bindings if is_open_tier(b)]
                )
            if not is_open_tier(binding):
                return personal.render_private_refused(binding.name)
            if cmd.verb == "topic-remove":
                await self._personal_store.remove_topic(user_id, binding.name, cmd.name)
                return personal.render_topic_removed(binding.name, cmd.name)
            followed = await self._personal_store.list_for(user_id)
            if binding.name not in followed:
                return personal.render_topic_needs_follow(binding.name)
            description = cmd.description or cmd.name
            await self._personal_store.add_topic(user_id, binding.name, cmd.name, description)
            return personal.render_topic_added(binding.name, cmd.name, description)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_orchestrator.py -k topic -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): dispatch topic add/remove/list with follow-first + visibility checks"
```

---

### Task 5: Per-project topic filtering in `summarize_shared` (`runner.py`)

**Files:**
- Modify: `src/babbla/digest/runner.py` (`summarize_shared` `:50-74`)
- Test: `tests/test_digest_shared.py` (runner-level test; add a small fake agent)

**Interfaces:**
- Consumes: existing `Change`, `_facts`, `_topic_preamble`, `NOTHING_RELEVANT`.
- Produces: `summarize_shared(context_binding, per_project_changes, topic=None, slugs=None, topics_by_project=None)`. When `topics_by_project` is empty/None AND `topic` is None, the prompt is identical to today. When a project has topics, its section carries an "include only the union; else omit section" instruction; if the model replies exactly `NOTHING_RELEVANT`, returns `""`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_digest_shared.py` (reuse or add a minimal fake agent runner that records the prompt and returns a canned answer):

```python
from babbla.digest.runner import DigestRunner
from babbla.digest.anchors import Change


class _RecordingAgent:
    def __init__(self, reply="ok"):
        self.reply = reply
        self.prompt = None
    async def run_ask(self, prompt, binding, _none, system_prompt=None):
        self.prompt = prompt
        class _A: text = self.reply
        return _A()


def _binding():
    from babbla.config import ProjectBinding
    return ProjectBinding("MyTV", "o", "MyTV", "public", "C1", False)


async def test_summarize_shared_no_topics_prompt_has_no_filter_instruction():
    agent = _RecordingAgent()
    changes = {"MyTV": [Change(sha="a" * 40, subject="x", pr_number=None)]}
    await DigestRunner(agent).summarize_shared(_binding(), changes)
    assert "Include ONLY changes relevant" not in agent.prompt


async def test_summarize_shared_per_project_topic_adds_union_instruction():
    agent = _RecordingAgent()
    changes = {"MyTV": [Change(sha="a" * 40, subject="x", pr_number=None)]}
    topics = {"MyTV": (("security", "auth, CVEs"), ("perf", "latency"))}
    await DigestRunner(agent).summarize_shared(_binding(), changes, topics_by_project=topics)
    assert "Include ONLY changes relevant to ANY of these topics" in agent.prompt
    assert "security (auth, CVEs)" in agent.prompt and "perf (latency)" in agent.prompt


async def test_summarize_shared_nothing_relevant_returns_empty():
    agent = _RecordingAgent(reply="NOTHING_RELEVANT")
    changes = {"MyTV": [Change(sha="a" * 40, subject="x", pr_number=None)]}
    out = await DigestRunner(agent).summarize_shared(
        _binding(), changes, topics_by_project={"MyTV": (("security", "x"),)})
    assert out == ""
```

(`Change` is `@dataclass(frozen=True)` with fields `sha: str`, `subject: str`, `pr_number: int | None` — verified against `src/babbla/digest/anchors.py:16-20`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest_shared.py -k summarize_shared -v`
Expected: FAIL — `summarize_shared() got an unexpected keyword argument 'topics_by_project'`.

- [ ] **Step 3: Rewrite `summarize_shared`**

In `src/babbla/digest/runner.py`, replace `summarize_shared` (`:50-74`) with:

```python
    async def summarize_shared(
        self, context_binding: ProjectBinding, per_project_changes: dict[str, list[Change]],
        topic: Topic | None = None, slugs: dict[str, str] | None = None,
        topics_by_project: dict | None = None,
    ) -> str:
        slugs = slugs or {}
        topics_by_project = topics_by_project or {}
        section_parts = []
        for name, changes in per_project_changes.items():
            heading = f"## {name} ({slugs[name]})" if name in slugs else f"## {name}"
            tlist = topics_by_project.get(name)
            if tlist:
                topic_line = "; ".join(f"{tn} ({td})" for tn, td in tlist)
                instr = (
                    f"\n[Include ONLY changes relevant to ANY of these topics: {topic_line}. "
                    "If none of this project's changes are relevant, omit this section entirely.]"
                )
            else:
                instr = ""
            section_parts.append(f"{heading}{instr}\n{_facts(changes)}")
        sections = "\n\n".join(section_parts)
        preamble = _topic_preamble(topic) if topic else ""
        if topics_by_project:
            preamble += (
                "Some sections below are scoped to per-project topics. If, after applying those "
                f"filters, NO section has any relevant content, reply with exactly {NOTHING_RELEVANT}.\n\n"
            )
        prompt = preamble + (
            "Write ONE concise Slack digest of what shipped across several projects this period. "
            "Lead with a short cross-project headline, then a section per project. Summarize at a "
            "reader-friendly altitude, group related work, and CITE commits by SHA and PRs by number "
            "as GitHub links (use the owner/repo in each section heading). Keep it short and "
            "Slack-friendly.\n\n"
            f"{sections}"
        )
        answer = await self._agent.run_ask(
            prompt, context_binding, None, system_prompt=DIGEST_SYSTEM_PROMPT
        )
        if (topic or topics_by_project) and answer.text.strip() == NOTHING_RELEVANT:
            return ""
        return answer.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_digest_shared.py -v`
Expected: PASS (new tests + the existing shared-digest tests, whose prompt is unchanged when no topics are passed).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/runner.py tests/test_digest_shared.py
git commit -m "feat(digest): per-project topic union filtering in summarize_shared"
```

---

### Task 6: Wire topics into `PersonalDigestAction` + empty-post guard (`actions.py`)

**Files:**
- Modify: `src/babbla/digest/actions.py` (`PersonalDigestAction._maybe_run_user` `:135-179`)
- Test: `tests/test_personal_digest.py`

**Interfaces:**
- Consumes: `PersonalSubStore.topics_for` (Task 1, available as `self._subs`), `summarize_shared(topics_by_project=…)` (Task 5).
- Produces: the personal digest filters each project section by the user's topics; an empty/`NOTHING_RELEVANT` summary skips the DM but still advances the watermark.

- [ ] **Step 1: Update the test fakes and write the failing tests**

In `tests/test_personal_digest.py`, update `FakeRunner` to accept the new kwarg and let a test control its output:

```python
class FakeRunner:
    def __init__(self, text="digest text"):
        self.text = text
        self.last_topics = None
    async def summarize_shared(self, binding, per_project_changes, slugs=None, topics_by_project=None):
        self.last_topics = topics_by_project
        return self.text
```

Update existing instantiations that rely on the old positional/kwargs (they pass `FakeRunner()` — still valid). Then add:

```python
async def test_personal_digest_passes_user_topics_to_runner(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add_topic("U1", "MyTV", "security", "auth, CVEs")
    runner = FakeRunner()
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  runner, poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert runner.last_topics == {"MyTV": (("security", "auth, CVEs"),)}
    subs.close(); state.close()


async def test_personal_digest_empty_summary_skips_post_but_advances(tmp_path):
    subs, state = await _store_pair(tmp_path)
    await subs.add("U1", "MyTV")
    await subs.add_topic("U1", "MyTV", "i18n", "translations")   # nothing matches → runner returns ""
    poster = FakePoster()
    action = PersonalDigestAction(subs, state, BY_NAME,
                                  _get_json_with_commits("sha1", [{"sha": "sha1"}]),
                                  FakeRunner(text=""), poster, "weekly", "UTC")
    await action.maybe_run(NOW)
    assert poster.posts == []                                    # no blank DM
    assert (await state.get("U1")).watermarks.get("MyTV") == "sha1"   # watermark advanced
    subs.close(); state.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal_digest.py -k "topics or empty_summary" -v`
Expected: FAIL — `last_topics` is `None` (topics not passed), and the empty-summary test posts a blank message (no guard yet).

- [ ] **Step 3: Thread topics + add the empty-post guard**

In `src/babbla/digest/actions.py`, `PersonalDigestAction._maybe_run_user`, replace the tail (`:171-179`, from `context_binding = …` onward):

```python
        context_binding = self._by_name[next(iter(per_project_changes))]
        slugs = {
            n: f"{self._by_name[n].owner}/{self._by_name[n].repo}"
            for n in per_project_changes if n in self._by_name
        }
        topics_by_project = await self._subs.topics_for(user_id)
        text = await self._runner.summarize_shared(
            context_binding, per_project_changes, slugs=slugs, topics_by_project=topics_by_project
        )
        if text.strip():
            dm_channel = await self._poster.open_dm(user_id)
            await self._poster.post(
                dm_channel, text, blocks=delete_button_blocks(text, owner_id=user_id)
            )
        await self._state.advance(user_id, heads, now.timestamp())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && python -m pytest tests/test_personal_digest.py -v`
Expected: PASS — including the existing tests. Note `test_one_user_failure_does_not_abort_others` still opens DMs because `FakeRunner` returns non-empty `"digest text"`.

- [ ] **Step 5: Run the FULL suite**

Run: `source .venv/bin/activate && python -m pytest -q`
Expected: all green (baseline 319 passed + the new tests; 2 skipped).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/digest/actions.py tests/test_personal_digest.py
git commit -m "feat(digest): personal digest applies user topics + skips empty DM (watermark still advances)"
```

---

## Self-Review

**Spec coverage:**
- Store / `personal_topics` (A1) → Task 1. ✓
- NL-only, internal classifier contract, `|` grammar → Tasks 2–3. ✓
- Auto-expand description → Task 3 (classifier prompt) + Task 4 (`description = cmd.description or cmd.name` fallback). ✓
- Dispatch: follow-first / unknown / private; confirm-back → Task 4. ✓
- B1 union filtering in `summarize_shared`; untopiced project unchanged; no-regression when absent → Task 5. ✓
- Personal digest threads topics; empty-post guard (watermark advances) → Task 6. ✓
- Multiple topics per project, per-user isolation, name normalization → Task 1 tests. ✓
- "All sections filtered → no DM, advance"; "mixed → kept only" → Task 6 (empty) + Task 5 (per-section omit). ✓
- Out of scope (slash command, channel/shared NL, AND-semantics, cascade-delete, rename) → not implemented. ✓

**Placeholder scan:** none — every code/test step carries full content.

**Type consistency:** `Command` fields (`project`/`name`/`description`) defined in Task 2, used in Tasks 3–4. `add_topic/remove_topic/topics_for` defined in Task 1, used in Tasks 4 & 6. `summarize_shared(topics_by_project=…)` defined in Task 5, called in Task 6. `topics_for` return shape `{project: ((name, desc), …)}` consistent across Tasks 1, 4, 5, 6. Verb strings `topic-add` / `topic-remove` / `topic-list` consistent across Tasks 2–4.

**Verified pre-write:** `PersonalSubStore` connect/`to_thread` pattern and `PersonalDigestAction` field `self._subs`; `Change(sha, subject, pr_number)`; `summarize_shared` current signature; `_dispatch_command` insertion point. No remaining unknowns.
