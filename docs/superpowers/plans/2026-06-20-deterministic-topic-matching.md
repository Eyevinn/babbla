# Hybrid Deterministic Topic Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a per-project digest `topic:` carry optional PR-label and changed-file-path signals that *guarantee* a matching change is included, layered additively on top of today's LLM description scoping.

**Architecture:** Signals ride the existing digest pipeline. `Topic` gains `labels`/`paths`; `Change` gains optional `labels`/`paths` populated by a new enrichment step; the runner marks deterministically-matched changes `✓` and tells the model they MUST be included; the per-project digest action enriches before summarizing. A topic with no signals behaves byte-for-byte as today.

**Tech Stack:** Python 3, `dataclasses`, `fnmatch`, GitHub REST via the injected `get_json`, pytest (async tests).

## Global Constraints

- **Read-only by construction (ADR 0003):** enrichment is GET-only (`/pulls/{n}`, `/pulls/{n}/files`). The only writes remain the Slack post + watermark advance. No new write paths.
- **Graceful degradation / no pollution:** signals are *additive* — they guarantee inclusion, never gate. A topic with neither `labels:` nor `paths:` must produce identical behavior to the existing LLM-only Topics slice.
- **Scope:** per-project digest `topic:` only (`DigestConfig.topic`). Personal-digest deterministic matching is **deferred** — do not touch `summarize_shared` / `PersonalDigestAction` / `topics_by_project`.
- **Determinism in tests:** fake `get_json` returns canned JSON; fake agent captures the prompt; fixed `now`; `tmp_path` stores. No network, no real model.
- **Commit style:** end commit messages with the two trailers used across this repo:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UVQAuRWnVZAmjT98i8SGKk
  ```
- **Full suite command:** `python -m pytest -q` (excludes nothing local; live-integration tests are already opt-in).

## File Structure

- **Modify** `src/babbla/config.py` — `Topic.labels`/`paths` + `has_signals` property; `_parse_topic` parses optional list fields; small `_parse_str_list` helper.
- **Modify** `src/babbla/digest/anchors.py` — add optional `labels`/`paths` fields to the `Change` dataclass (back-compatible defaults).
- **Create** `src/babbla/digest/topics_match.py` — `enrich_changes`, `matches_topic`, and the GET helpers `_pr_labels`/`_pr_files`/`_path_match`. (Kept out of `anchors.py` so the matching logic is a focused unit and matches the `test_topics_match.py` name.)
- **Modify** `src/babbla/digest/runner.py` — `_facts(changes, topic=None)` marks `✓`; `_topic_preamble` appends the must-include rule only when `topic.has_signals`.
- **Modify** `src/babbla/digest/actions.py` — `PerProjectDigestAction._emit` enriches before summarizing.
- **Modify** `config/channels.yaml` — extend the commented `topic:` example with `labels:`/`paths:`.
- **Modify** `tests/test_config_digest.py` — parse coverage.
- **Create** `tests/test_topics_match.py` — enrichment + matching.
- **Modify** `tests/test_digest_runner_poster.py` — `✓` marking + rule in the prompt.
- **Modify** `tests/test_digest_scheduler.py` — action enriches before summarize.

---

### Task 1: `Topic.labels`/`paths` + parsing

**Files:**
- Modify: `src/babbla/config.py:16-19` (the `Topic` dataclass) and `src/babbla/config.py:153-160` (`_parse_topic`)
- Test: `tests/test_config_digest.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Topic(name: str, description: str, labels: tuple[str, ...] = (), paths: tuple[str, ...] = ())`
  - `Topic.has_signals -> bool` (property): `bool(self.labels or self.paths)`
  - `_parse_topic` now reads optional `labels:`/`paths:` lists; a non-list value raises `ValueError("{label}: topic.labels must be a list of strings")` (and the `topic.paths` analogue).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config_digest.py` (the existing `_write_cfg` helper and `_PROJECT_WITH_TOPIC` are already in this file):

```python
_PROJECT_WITH_SIGNALS = (
    "projects:\n"
    "  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
    "    channel_id: C1\n    dm: false\n"
    "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
    "      topic:\n        name: security\n        description: auth, secrets, CVEs\n"
    "        labels: [security, area/auth]\n"
    "        paths: ['src/babbla/access.py', 'src/babbla/**']\n"
)


def test_digest_topic_parses_labels_and_paths(tmp_path):
    from babbla.config import load_config, Topic
    cfg = load_config(_write_cfg(tmp_path, _PROJECT_WITH_SIGNALS))
    topic = cfg.bindings[0].digest.topic
    assert topic == Topic(
        name="security", description="auth, secrets, CVEs",
        labels=("security", "area/auth"),
        paths=("src/babbla/access.py", "src/babbla/**"),
    )
    assert topic.has_signals is True


def test_digest_topic_without_signals_has_empty_tuples(tmp_path):
    from babbla.config import load_config
    cfg = load_config(_write_cfg(tmp_path, _PROJECT_WITH_TOPIC))
    topic = cfg.bindings[0].digest.topic
    assert topic.labels == () and topic.paths == ()
    assert topic.has_signals is False


def test_digest_topic_labels_must_be_a_list(tmp_path):
    import pytest
    from babbla.config import load_config
    body = (
        "projects:\n  - name: MyTV\n    owner: o\n    repo: MyTV\n    visibility: public\n"
        "    channel_id: C1\n    dm: false\n"
        "    digest:\n      cadence: weekly\n      tz: UTC\n      anchor: branch\n"
        "      topic:\n        name: security\n        description: auth\n"
        "        labels: not-a-list\n"
    )
    with pytest.raises(ValueError, match="topic.labels must be a list of strings"):
        load_config(_write_cfg(tmp_path, body))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_digest.py -q -k "signals or labels_and_paths or empty_tuples or labels_must"`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'labels'` (Topic has no such field yet).

- [ ] **Step 3: Add the fields to `Topic`**

Replace the `Topic` dataclass at `src/babbla/config.py:16-19`:

```python
@dataclass(frozen=True)
class Topic:
    name: str
    description: str
    labels: tuple[str, ...] = ()   # PR labels that mark a change as in-topic
    paths: tuple[str, ...] = ()    # glob patterns over changed file paths

    @property
    def has_signals(self) -> bool:
        return bool(self.labels or self.paths)
```

- [ ] **Step 4: Parse the optional lists**

Replace `_parse_topic` at `src/babbla/config.py:153-160` and add the helper just above it:

```python
def _parse_str_list(label: str, field: str, raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{label}: {field} must be a list of strings")
    return tuple(str(x) for x in raw)


def _parse_topic(label: str, raw: dict | None) -> "Topic | None":
    if not raw:
        return None
    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not name or not description:
        raise ValueError(f"{label}: topic requires both name and description")
    labels = _parse_str_list(label, "topic.labels", raw.get("labels"))
    paths = _parse_str_list(label, "topic.paths", raw.get("paths"))
    return Topic(name=name, description=description, labels=labels, paths=paths)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config_digest.py -q`
Expected: PASS (new tests + all existing topic tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/config.py tests/test_config_digest.py
git commit -m "feat: topic labels/paths signals + has_signals on the config

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UVQAuRWnVZAmjT98i8SGKk"
```

---

### Task 2: deterministic enrichment + matching

**Files:**
- Modify: `src/babbla/digest/anchors.py:16-21` (the `Change` dataclass)
- Create: `src/babbla/digest/topics_match.py`
- Test: `tests/test_topics_match.py` (new)

**Interfaces:**
- Consumes: `Topic` (with `labels`/`paths`/`has_signals`) from Task 1; `Change` from `anchors.py`.
- Produces (importable from `babbla.digest.topics_match`):
  - `enrich_changes(owner: str, repo: str, changes: list[Change], topic: Topic, *, get_json) -> list[Change]` — no-op (returns input list unchanged, **zero** `get_json` calls) when `not topic.has_signals`; otherwise returns new `Change`s with `labels`/`paths` populated for PR-backed changes; each PR fetched at most once per call.
  - `matches_topic(change: Change, topic: Topic) -> bool` — pure; True iff a label overlaps or a path glob matches.
  - `Change` now has `labels: tuple[str, ...] = ()` and `paths: tuple[str, ...] = ()`.

- [ ] **Step 1: Add fields to `Change`**

Replace the `Change` dataclass at `src/babbla/digest/anchors.py:16-21`:

```python
@dataclass(frozen=True)
class Change:
    sha: str
    subject: str
    pr_number: int | None
    labels: tuple[str, ...] = ()   # populated only by enrichment
    paths: tuple[str, ...] = ()    # changed file paths, populated only by enrichment
```

(Defaults keep every existing `Change(...)` call and equality check intact — no other edit needed in `anchors.py`.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_topics_match.py`:

```python
from babbla.config import Topic
from babbla.digest.anchors import Change
from babbla.digest.topics_match import enrich_changes, matches_topic


def _fake(routes):
    """get_json fake: returns the first route whose prefix matches; records calls."""
    calls = []

    def get_json(path):
        calls.append(path)
        for prefix, value in routes.items():
            if path.startswith(prefix):
                return value
        return None

    get_json.calls = calls
    return get_json


def test_enrich_is_noop_without_signals():
    topic = Topic("t", "d")  # no labels/paths
    gj = _fake({})
    changes = [Change("s1", "x", 1)]
    out = enrich_changes("o", "r", changes, topic, get_json=gj)
    assert out is changes          # untouched
    assert gj.calls == []          # never fetched


def test_enrich_populates_labels_and_matches():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/42": {"labels": [{"name": "security"}, {"name": "x"}]}})
    out = enrich_changes("o", "r", [Change("s1", "feat (#42)", 42)], topic, get_json=gj)
    assert out[0].labels == ("security", "x")
    assert matches_topic(out[0], topic) is True


def test_enrich_populates_paths_and_glob_matches():
    topic = Topic("area", "d", paths=("src/babbla/**",))
    gj = _fake({"/repos/o/r/pulls/7/files": [
        {"filename": "src/babbla/digest/runner.py"},
        {"filename": "README.md"},
    ]})
    out = enrich_changes("o", "r", [Change("s1", "feat (#7)", 7)], topic, get_json=gj)
    assert out[0].paths == ("src/babbla/digest/runner.py", "README.md")
    assert matches_topic(out[0], topic) is True


def test_prless_change_is_never_enriched():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/1": {"labels": [{"name": "security"}]}})
    out = enrich_changes("o", "r", [Change("s1", "chore tidy", None)], topic, get_json=gj)
    assert out[0].labels == () and out[0].paths == ()
    assert matches_topic(out[0], topic) is False
    assert gj.calls == []          # no PR -> no fetch


def test_pr_fetch_404_yields_empty_no_raise():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({})                 # everything 404 -> None
    out = enrich_changes("o", "r", [Change("s1", "feat (#9)", 9)], topic, get_json=gj)
    assert out[0].labels == ()
    assert matches_topic(out[0], topic) is False


def test_each_pr_fetched_at_most_once():
    topic = Topic("sec", "d", labels=("security",))
    gj = _fake({"/repos/o/r/pulls/5": {"labels": [{"name": "security"}]}})
    enrich_changes("o", "r",
                   [Change("a", "x (#5)", 5), Change("b", "y (#5)", 5)],
                   topic, get_json=gj)
    assert gj.calls.count("/repos/o/r/pulls/5") == 1


def test_no_match_when_label_absent():
    topic = Topic("sec", "d", labels=("security",))
    c = Change("s", "x", 1, labels=("bug",), paths=())
    assert matches_topic(c, topic) is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_topics_match.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'babbla.digest.topics_match'`.

- [ ] **Step 4: Implement `topics_match.py`**

Create `src/babbla/digest/topics_match.py`:

```python
from __future__ import annotations

import fnmatch
from dataclasses import replace

from babbla.config import Topic
from babbla.digest.anchors import Change


def _pr_labels(owner: str, repo: str, n: int, get_json) -> tuple[str, ...]:
    data = get_json(f"/repos/{owner}/{repo}/pulls/{n}")
    if not data:
        return ()
    return tuple(lbl["name"] for lbl in data.get("labels", []))


def _pr_files(owner: str, repo: str, n: int, get_json) -> tuple[str, ...]:
    data = get_json(f"/repos/{owner}/{repo}/pulls/{n}/files?per_page=100")
    if not data:
        return ()
    return tuple(f["filename"] for f in data)


def _path_match(path: str, glob: str) -> bool:
    # Case-sensitive, cross-platform deterministic. `*`/`**` cross `/`, so
    # `src/babbla/**` matches nested files; over-matching is low-stakes since
    # signals only guarantee inclusion, never gate.
    return fnmatch.fnmatchcase(path, glob)


def enrich_changes(owner: str, repo: str, changes: list[Change], topic: Topic, *, get_json) -> list[Change]:
    """Populate labels/paths on PR-backed changes, only as the topic needs them.

    No-op (returns `changes`) when the topic has no signals. Each PR is fetched
    at most once per call. Missing PR data (404/None) -> empty tuples, never raises.
    """
    if not topic.has_signals:
        return changes
    label_cache: dict[int, tuple[str, ...]] = {}
    file_cache: dict[int, tuple[str, ...]] = {}
    out: list[Change] = []
    for c in changes:
        if c.pr_number is None:
            out.append(c)
            continue
        labels = c.labels
        paths = c.paths
        if topic.labels:
            if c.pr_number not in label_cache:
                label_cache[c.pr_number] = _pr_labels(owner, repo, c.pr_number, get_json)
            labels = label_cache[c.pr_number]
        if topic.paths:
            if c.pr_number not in file_cache:
                file_cache[c.pr_number] = _pr_files(owner, repo, c.pr_number, get_json)
            paths = file_cache[c.pr_number]
        out.append(replace(c, labels=labels, paths=paths))
    return out


def matches_topic(change: Change, topic: Topic) -> bool:
    if topic.labels and set(change.labels) & set(topic.labels):
        return True
    if topic.paths and any(_path_match(p, g) for p in change.paths for g in topic.paths):
        return True
    return False
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_topics_match.py tests/test_digest_anchors.py -q`
Expected: PASS (new module green; `Change`-field addition leaves anchors tests green).

- [ ] **Step 6: Commit**

```bash
git add src/babbla/digest/anchors.py src/babbla/digest/topics_match.py tests/test_topics_match.py
git commit -m "feat: deterministic topic enrichment + matching (labels/paths)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UVQAuRWnVZAmjT98i8SGKk"
```

---

### Task 3: runner marks `✓` and adds the must-include rule

**Files:**
- Modify: `src/babbla/digest/runner.py:10-48` (`_topic_preamble`, `_facts`, `summarize`)
- Test: `tests/test_digest_runner_poster.py`

**Interfaces:**
- Consumes: `matches_topic` from Task 2; `Topic.has_signals` from Task 1.
- Produces: `_facts(changes, topic=None)` prefixes matched changes with `✓ `; `_topic_preamble(topic)` appends the must-include rule iff `topic.has_signals`. `summarize` passes its `topic` into `_facts`. `summarize_shared` is unchanged (calls `_facts(changes)` — no `✓`, deferred).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_digest_runner_poster.py` (imports for `DigestRunner`, `Change`, `Topic`, `_binding`, `FakeAgent` already exist near the top of the file):

```python
async def test_summarize_signals_mark_match_and_add_rule():
    agent = FakeAgent()
    topic = Topic("security", "auth and secrets", labels=("security",))
    await DigestRunner(agent).summarize(
        _binding(),
        [Change("aaa1111", "feat: OAuth (#42)", 42, labels=("security",)),
         Change("bbb2222", "chore: bump eslint (#43)", 43, labels=("deps",))],
        "head99", topic=topic,
    )
    p = agent.prompt
    assert "- ✓ aaa1111 feat: OAuth (#42)" in p          # matched -> marked
    assert "- bbb2222 chore: bump eslint (#43)" in p      # unmatched -> no mark
    assert "marked with ✓" in p and "MUST be included" in p


async def test_summarize_topic_without_signals_has_no_marks_or_rule():
    agent = FakeAgent()
    topic = Topic("security", "auth and secrets")  # no signals
    await DigestRunner(agent).summarize(
        _binding(),
        [Change("aaa1111", "feat: OAuth (#42)", 42, labels=("security",))],
        "head99", topic=topic,
    )
    p = agent.prompt
    assert "✓" not in p
    assert "MUST be included" not in p
    assert "scoped to the topic" in p   # plain Topics preamble still present
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_digest_runner_poster.py -q -k "signals_mark or without_signals"`
Expected: FAIL — `✓` is absent from the prompt (current `_facts` ignores topic).

- [ ] **Step 3: Implement the marking + rule**

In `src/babbla/digest/runner.py`, add the import near the top (after the existing `from babbla.digest.anchors import Change`):

```python
from babbla.digest.topics_match import matches_topic
```

Replace `_topic_preamble` (lines 10-16) and `_facts` (lines 19-24):

```python
def _topic_preamble(topic: Topic) -> str:
    base = (
        f'This digest is scoped to the topic "{topic.name}": {topic.description}. '
        "Include ONLY changes relevant to this topic; omit everything else — do not pad. "
        "If NONE of the changes below are relevant to this topic, reply with exactly: "
        f"{NOTHING_RELEVANT}\n\n"
    )
    if topic.has_signals:
        base += (
            "Changes marked with ✓ match this topic by label or file path and MUST be "
            "included. For changes without ✓, include one only if it is relevant to the "
            "topic description above.\n\n"
        )
    return base


def _facts(changes: list[Change], topic: Topic | None = None) -> str:
    marked = bool(topic and topic.has_signals)
    lines = []
    for c in changes:
        pr = f" (#{c.pr_number})" if c.pr_number else ""
        mark = "✓ " if marked and matches_topic(c, topic) else ""
        lines.append(f"- {mark}{c.sha[:7]} {c.subject}{pr}")
    return "\n".join(lines)
```

Then in `summarize` (line ~39), pass the topic into `_facts`:

```python
            f"{_facts(changes, topic)}\n\n"
```

(Leave `summarize_shared`'s `_facts(changes)` call unchanged — personal digest is deferred.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_digest_runner_poster.py -q`
Expected: PASS — new tests plus every existing runner test (the no-topic and no-signal paths emit the same text as before).

- [ ] **Step 5: Commit**

```bash
git add src/babbla/digest/runner.py tests/test_digest_runner_poster.py
git commit -m "feat: runner marks deterministically-matched changes and requires their inclusion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UVQAuRWnVZAmjT98i8SGKk"
```

---

### Task 4: per-project digest action enriches before summarizing

**Files:**
- Modify: `src/babbla/digest/actions.py:1-13` (import) and `src/babbla/digest/actions.py:50-60` (`PerProjectDigestAction._emit`)
- Modify: `config/channels.yaml` (commented `topic:` example)
- Test: `tests/test_digest_scheduler.py`

**Interfaces:**
- Consumes: `enrich_changes` from Task 2; the action already holds `self._get_json` and `self._b.digest.topic`.
- Produces: `_emit` calls `enrich_changes(owner, repo, changes, topic, get_json=...)` before `summarize` **only** when `topic and topic.has_signals`; everything else (lead-in, post-guard, advance-regardless) unchanged.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_digest_scheduler.py`. The module already imports `babbla.digest.actions as A`, `PerProjectDigestAction`, `DigestConfig`, `ProjectBinding`, `Topic`, `Change`, `DigestState`, and defines `FakeStore`/`FakePoster`/`NOW`. Add a capture-runner and the test:

```python
class CaptureRunner:
    def __init__(self): self.changes = None
    async def summarize(self, binding, changes, head_sha, topic=None):
        self.changes = changes            # capture what the action handed us
        return "SUMMARY"


async def test_action_enriches_when_topic_has_signals(monkeypatch):
    binding = ProjectBinding(
        "MyTV", "o", "r", "public", "C0XXXXXXXXX", False,
        DigestConfig("weekly", "UTC", "branch", None,
                     Topic("security", "auth", labels=("security",))),
    )
    enriched = [Change("c1", "feat (#1)", 1, labels=("security",))]
    seen = {}
    def fake_enrich(owner, repo, changes, topic, *, get_json):
        seen["called"] = (owner, repo, topic.name)
        return enriched
    monkeypatch.setattr(A, "enrich_changes", fake_enrich)
    monkeypatch.setattr(A, "current_head", lambda b, *, get_json: "H")
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: [Change("c1", "feat (#1)", 1)])
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: [Change("c1", "feat (#1)", 1)])
    store, runner, poster = FakeStore(DigestState(None, None)), CaptureRunner(), FakePoster()
    action = PerProjectDigestAction(binding, store, lambda path: None, runner, poster)
    await action.maybe_run(NOW)
    assert seen["called"] == ("o", "r", "security")     # enrichment ran
    assert runner.changes == enriched                   # runner got enriched changes


async def test_action_skips_enrichment_without_signals(monkeypatch):
    binding = ProjectBinding(
        "MyTV", "o", "r", "public", "C0XXXXXXXXX", False,
        DigestConfig("weekly", "UTC", "branch", None, Topic("security", "auth")),
    )
    def boom(*a, **k):
        raise AssertionError("enrich_changes must not be called without signals")
    monkeypatch.setattr(A, "enrich_changes", boom)
    monkeypatch.setattr(A, "current_head", lambda b, *, get_json: "H")
    monkeypatch.setattr(A, "changes_between", lambda o, r, base, hd, *, get_json: [Change("c1", "x", None)])
    monkeypatch.setattr(A, "changes_since", lambda o, r, since, *, get_json: [Change("c1", "x", None)])
    store, runner, poster = FakeStore(DigestState(None, None)), CaptureRunner(), FakePoster()
    action = PerProjectDigestAction(binding, store, lambda path: None, runner, poster)
    await action.maybe_run(NOW)   # must not raise
    assert runner.changes == [Change("c1", "x", None)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_digest_scheduler.py -q -k "enriches_when or skips_enrichment"`
Expected: FAIL — `AttributeError: module 'babbla.digest.actions' has no attribute 'enrich_changes'` (not imported yet).

- [ ] **Step 3: Import `enrich_changes` in the action module**

In `src/babbla/digest/actions.py`, extend the anchors import at line 8:

```python
from babbla.digest.anchors import changes_between, changes_since, current_head, head_for
from babbla.digest.topics_match import enrich_changes
```

- [ ] **Step 4: Enrich in `_emit`**

Replace `PerProjectDigestAction._emit` (lines 50-60):

```python
    async def _emit(self, changes, head: str, now: datetime) -> None:
        if changes:
            topic = self._b.digest.topic
            if topic and topic.has_signals:
                changes = enrich_changes(
                    self._b.owner, self._b.repo, changes, topic, get_json=self._get_json
                )
            text = await self._runner.summarize(self._b, changes, head, topic=topic)
            if text.strip():
                slug = f"{self._b.owner}/{self._b.repo}"
                lead = f"Here's a {self._b.digest.cadence} update summary on *{slug}*"
                full = f"{lead}\n\n{text}"
                await self._poster.post(
                    self._b.channel_id, full, blocks=delete_button_blocks(full)
                )
        await self._store.advance(self._b.channel_id, head, now.timestamp())
```

- [ ] **Step 5: Document the signals in `config/channels.yaml`**

Find the commented `topic:` example under MyTV's `digest:` block (currently `name:` + `description:` only) and extend it:

```yaml
      # topic:
      #   name: security
      #   description: "auth, secrets, access control, CVEs, dependency security bumps"
      #   labels: [security, area/auth]      # optional: PRs with any of these labels are in-topic (✓ must-include)
      #   paths: ["src/babbla/access.py", "src/babbla/**"]   # optional: changed-file globs, repo-relative
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_digest_scheduler.py -q`
Expected: PASS — new tests plus all existing per-project digest action tests.

- [ ] **Step 7: Commit**

```bash
git add src/babbla/digest/actions.py config/channels.yaml tests/test_digest_scheduler.py
git commit -m "feat: per-project digest action enriches changes when topic has signals

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01UVQAuRWnVZAmjT98i8SGKk"
```

---

### Task 5: full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: PASS, with the new tests added (no regressions; the prior count was ~344 test functions / suite green).

- [ ] **Step 2: Confirm the invariants hold**

- A topic with no `labels:`/`paths:` produces a byte-identical prompt to before (covered by `test_summarize_topic_without_signals_has_no_marks_or_rule` and `test_action_skips_enrichment_without_signals`).
- Enrichment is GET-only and only runs for signal-bearing topics.
- `summarize_shared` / personal digest paths are untouched (no `✓`).

- [ ] **Step 3: If anything fails, debug with systematic-debugging**

Use superpowers:systematic-debugging before changing code. Do not paper over a failure by loosening an assertion.

---

## Self-Review

**Spec coverage:**
- `Topic.labels`/`paths` + `has_signals` + parse/validation → Task 1. ✅
- `Change.labels`/`paths` → Task 2 Step 1. ✅
- `enrich_changes` (no-op w/o signals, per-PR cache, 404→empty, PR-less skip), `matches_topic`, `_pr_labels`/`_pr_files`/`_path_match` (incl. `**`) → Task 2. ✅
- Runner `✓` marking + must-include rule gated on `has_signals`; `NOTHING_RELEVANT` normalization unchanged → Task 3. ✅
- `PerProjectDigestAction._emit` enrich-before-summarize → Task 4. ✅
- `config/channels.yaml` example → Task 4 Step 5. ✅
- Deferred (untouched): personal-digest matching, `summarize_shared`, Ask-scoped topics, keyword matching, multiple topics per digest. ✅
- Test files from the spec's Testing section: `test_config.py`→covered in `test_config_digest.py` (where topic tests already live), `test_topics_match.py` (new), `test_digest_runner_poster.py`, per-project action tests in `test_digest_scheduler.py`. ✅

**Placeholder scan:** none — every code/ test step shows full content.

**Type consistency:** `Topic(name, description, labels=(), paths=())` + `has_signals` used identically in Tasks 1/2/3/4. `enrich_changes(owner, repo, changes, topic, *, get_json)` and `matches_topic(change, topic)` signatures match across Tasks 2/3/4. `_facts(changes, topic=None)` and `_topic_preamble(topic)` consistent in Task 3. `Change(sha, subject, pr_number, labels=(), paths=())` positional usage matches existing tests.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-20-deterministic-topic-matching.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session with checkpoints for review.

**Which approach?**
