# Hybrid Deterministic Topic Matching — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-20
**Builds on:** [Topics](2026-06-19-topics-design.md) (the LLM-scoped digest topic),
[Scheduled Actions Framework](2026-06-19-scheduled-actions-design.md) (the digest actions + runner)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ROADMAP — organizing principle: no pollution / graceful degradation](../../ROADMAP.md#organizing-principle-decided-2026-06-18)

## Why this slice exists

Topics today are **purely LLM-scoped**: the digest summarizer is told the topic name + description and
asked to include only relevant changes (or reply `NOTHING_RELEVANT`). That degrades gracefully but has no
precision floor — the model can miss or wrongly drop a clearly-relevant change, and a maintainer who *does*
label PRs (`security`, `area/playback`) or keeps a tidy directory layout gets no benefit from those signals.

This slice adds **optional deterministic signals** — PR labels and changed-file path globs — to a topic, and
layers them onto the existing LLM scoping as a **hybrid that guarantees inclusion** rather than gating it.

### The hybrid principle (the key decision)

The original Topics design rejected deterministic matching as "brittle / pollution-adjacent" because a pure
label/path filter *requires* projects to maintain a labelling convention — which cuts against Babbla's
no-pollution and graceful-degradation stance. This design keeps that stance by making signals **additive**:

- **Signals guarantee inclusion, they do not gate.** A change whose PR carries a matching label or touches a
  matching path is marked `✓ in-topic` and the model is told it **must** include it.
- **Everything else stays LLM-judged.** Changes with no matching signal — including PR-less commits, and any
  change in a repo that simply doesn't use the convention — are still offered to the model under the existing
  description-based filter.
- **No signals → no change.** A topic with neither `labels:` nor `paths:` behaves byte-for-byte as today.

Result: precision where conventions exist (clearly-tagged work can't be silently dropped), recall everywhere
(description-scoping still catches untagged-but-relevant work), and zero new requirement on subject projects.

### Scope

- **In:** the **per-project digest** `topic:` (the `DigestConfig.topic` surface).
- **Deferred:** **personal-digest** deterministic matching — personal topics are a per-user, list-shaped
  surface (`topics_by_project`, multiple topics per project) that would need per-topic enrichment; out of
  scope here to keep this slice tight. The data model (`Topic.labels`/`Topic.paths`) is shared, so extending
  to personal later is additive.

## Architecture & data flow

Deterministic signals ride the **existing digest pipeline**. The only new moving part is an enrichment step
in the per-project digest action (which holds `get_json`), plus annotation in the runner's prompt.

```
DigestConfig.topic (name, description, labels?, paths?)
        │
        ▼
PerProjectDigestAction._emit
   changes gathered as today (anchors/watermark)
   │  topic has labels/paths?
   │     yes → enrich PR-backed changes (fetch labels / changed files) and
   │           compute matches_topic(change) per change
   ▼
DigestRunner.summarize(binding, changes, head, topic=…)
   _facts() marks ✓ on matched changes;
   _topic_preamble() adds the "must include ✓" rule when signals present
   ▼
text  ("" when the model replies NOTHING_RELEVANT)  →  post-guard + advance (unchanged)
```

## Components & files

### Changed: `src/babbla/config.py`

```python
@dataclass(frozen=True)
class Topic:
    name: str
    description: str
    labels: tuple[str, ...] = ()   # PR labels that mark a change as in-topic
    paths: tuple[str, ...] = ()    # glob patterns over changed file paths
```

- `_parse_topic` additionally parses optional `labels:` and `paths:` as lists of strings (absent → `()`;
  a non-list value → `ValueError(f"{label}: topic.labels must be a list of strings")`, same for `paths`).
  `name` + `description` requirements are unchanged. A topic with `labels`/`paths` but those being the only
  keys is still valid (signals are additive to description, not a replacement — `name`/`description` stay
  required).
- `Topic.has_signals` convenience: `bool(self.labels or self.paths)`.

### Changed: `src/babbla/digest/anchors.py`

```python
@dataclass(frozen=True)
class Change:
    sha: str
    subject: str
    pr_number: int | None
    labels: tuple[str, ...] = ()   # NEW — populated only by enrichment
    paths: tuple[str, ...] = ()    # NEW — changed file paths, populated only by enrichment
```

Adding optional fields is back-compatible: every existing `Change(...)` construction and equality check is
unaffected (defaults to empty), and unenriched changes carry empty tuples.

New enrichment + matching helpers (in `anchors.py`, or a small `digest/topics_match.py` if it reads cleaner):

```python
def enrich_changes(owner, repo, changes, topic, *, get_json) -> list[Change]:
    """Populate labels/paths on PR-backed changes, only as the topic needs them.
    No-op (returns `changes`) when the topic has no signals."""
    if not topic.has_signals:
        return changes
    out = []
    for c in changes:
        if c.pr_number is None:
            out.append(c); continue
        labels = _pr_labels(owner, repo, c.pr_number, get_json) if topic.labels else ()
        paths  = _pr_files(owner, repo, c.pr_number, get_json) if topic.paths else ()
        out.append(replace(c, labels=labels, paths=paths))
    return out

def matches_topic(change, topic) -> bool:
    if topic.labels and set(change.labels) & set(topic.labels):
        return True
    if topic.paths and any(_path_match(p, g) for p in change.paths for g in topic.paths):
        return True
    return False
```

- `_pr_labels`: `get_json(f"/repos/{o}/{r}/pulls/{n}")` → `[lbl["name"] for lbl in data["labels"]]`; `None`/404
  → `()`.
- `_pr_files`: `get_json(f"/repos/{o}/{r}/pulls/{n}/files?per_page=100")` → `[f["filename"] for f in data]`;
  `None`/404 → `()`. (Single page; a PR touching >100 files is rare and the first page is a sufficient signal.)
- `_path_match`: `fnmatch.fnmatch(path, glob)` with `**` support via `PurePath(path).match(glob)` — globs are
  matched against the repo-relative path (e.g. `src/babbla/**`, `docs/adr/*.md`). Documented as repo-relative,
  unanchored unless the glob starts at the repo root.

Enrichment is cached per `(pr_number)` within a single `enrich_changes` call so a PR appearing once is fetched
at most once for labels and once for files.

### Changed: `src/babbla/digest/runner.py`

- `_facts(changes, topic=None)` prefixes a matched change with a marker when `topic and topic.has_signals and
  matches_topic(c, topic)`:

  ```
  - ✓ a1b2c3d Add OAuth PKCE flow (#42)        # deterministic in-topic
  - 9f8e7d6 Bump eslint (#43)                   # LLM-judged
  ```

- `_topic_preamble(topic)` appends, **only when `topic.has_signals`**, a rule:

  ```
  Changes marked with ✓ match this topic by label or file path and MUST be included.
  For changes without ✓, include one only if it is relevant to the topic description above.
  ```

  When the topic has no signals the preamble is exactly today's text (no `✓` rule, no markers) — fully
  back-compatible.
- The `NOTHING_RELEVANT → ""` normalization is unchanged. (If every change is `✓`, the model will not emit
  the sentinel; if there are no `✓` changes and none match the description, it still can.)

### Changed: `src/babbla/digest/actions.py`

`PerProjectDigestAction._emit` enriches before summarizing when the topic has signals:

```python
async def _emit(self, changes, head, now):
    if changes:
        topic = self._b.digest.topic
        if topic and topic.has_signals:
            changes = enrich_changes(self._b.owner, self._b.repo, changes, topic, get_json=self._get_json)
        text = await self._runner.summarize(self._b, changes, head, topic=topic)
        if text.strip():
            await self._poster.post(self._b.channel_id, text, blocks=delete_button_blocks(text))
    await self._store.advance(self._b.channel_id, head, now.timestamp())
```

The post-guard + advance-regardless behavior from the Topics slice is unchanged.

### Changed: `config/channels.yaml`

Extend the commented `topic:` example under a project `digest:` to show optional `labels:`/`paths:`:

```yaml
      # topic:
      #   name: security
      #   description: "auth, secrets, access control, CVEs, dependency security bumps"
      #   labels: [security, area/auth]      # optional: PRs with any of these labels are in-topic
      #   paths: ["src/babbla/access.py", "src/babbla/**/*auth*"]   # optional: changed-file globs
```

### Unchanged

`app.py` (rides existing digest wiring), `PersonalDigestAction` (personal deterministic matching deferred),
`orchestrator.py`, `slack_adapter.py`, the cadence/watermark layer, all Ask paths, `summarize_shared`'s
existing signature (personal still passes `topics_by_project` as today — no `✓` there yet).

## Error handling & edge cases

- **No `labels:`/`paths:`** → `topic.has_signals` false → enrichment skipped, prompt + behavior identical to
  the LLM-only Topics slice (regression-guarded).
- **Signals set, PR enrichment 404 / `None`** (PR data unavailable) → that change carries empty labels/paths
  → unmatched → falls through to the LLM description filter. Never raises.
- **PR-less commit with signals set** → not enriched, not `✓` → LLM-judged. Graceful: unconventional repos
  lose nothing.
- **Repo with no matching labels/paths this period** → no `✓` marks → pure LLM-scoping over the period →
  identical outcome to today (the convention simply didn't fire). No false silence.
- **Every change is `✓`** → all included; the model won't emit `NOTHING_RELEVANT`.
- **Path glob never matches** (typo / wrong root) → behaves as "no path signal"; the label signal and LLM
  filter still apply. Low-stakes, human-read.
- **Read-only preserved** — enrichment is GET-only (`/pulls/{n}`, `/pulls/{n}/files`); the only writes remain
  the Slack post + watermark advance.
- **Extra API cost** — bounded by the number of PR-backed changes in the digest window (a handful per week),
  and incurred **only** for a topic that declares signals.

## Testing

All deterministic — fake `get_json` returns canned PR/file JSON; fake agent runner captures the prompt and
returns canned text or the sentinel; fixed `now`; `tmp_path` stores. No network, no real model.

### `tests/test_config.py` (extend)
- `topic:` with `labels:`/`paths:` parses into `Topic(..., labels=(...), paths=(...))`.
- `topic:` with neither → `labels == ()` and `paths == ()`; `has_signals` false.
- `labels:`/`paths:` given a non-list → `ValueError`.

### `tests/test_topics_match.py` (new) — `enrich_changes` / `matches_topic`
- `enrich_changes` is a no-op when `topic.has_signals` is false (returns input unchanged, no `get_json` calls).
- With label signal: fetches `/pulls/{n}`, populates labels; a PR with a matching label → `matches_topic` true.
- With path signal: fetches `/pulls/{n}/files`, populates paths; a changed file matching a glob → true;
  `**` glob support verified (`src/babbla/**`).
- PR-less change → never enriched, `matches_topic` false.
- 404 on PR fetch → empty labels/paths, no raise, `matches_topic` false.
- Enrichment fetches each PR at most once (call-count assertion).

### `tests/test_digest_runner_poster.py` (extend; fake agent captures the prompt)
- `summarize(..., topic=Topic(..., labels=("security",)))` over a change list where one is matched →
  the captured prompt marks that change with `✓` and contains the "must include ✓" rule.
- `summarize(..., topic=Topic(...))` with no signals → no `✓` markers, no `✓` rule (today's prompt).

### per-project digest action tests (extend)
- Topic with signals → action calls `enrich_changes` (fake `get_json` invoked) before `summarize`; matched
  change is `✓` in the prompt; non-empty summary → post + advance.
- Topic without signals → no enrichment calls; behaves as the LLM-only Topics slice.

## Scope summary

- **Changed:** `config.py` (`Topic.labels`/`paths` + parse + `has_signals`), `digest/anchors.py`
  (`Change.labels`/`paths`, `enrich_changes`, `matches_topic`, PR label/file fetch + `_path_match`),
  `digest/runner.py` (`✓` marking + must-include rule, both gated on `has_signals`), `digest/actions.py`
  (enrich-before-summarize in `PerProjectDigestAction`), `config/channels.yaml` (commented example).
- **New:** `tests/test_topics_match.py`.
- **Behavior when no `labels:`/`paths:`:** none (fully back-compatible with the LLM-only Topics slice).

## Out of scope (deferred)

- **Personal-digest deterministic matching** — per-user, list-shaped topics; additive later via the shared
  `Topic` model.
- **Ask-scoped topics** — dropped (not pursued).
- **Keyword/commit-message matching** — only labels + file paths here; commit-text matching would duplicate
  what the LLM description filter already does.
- **Multiple topics per project digest** — still one topic per digest (use the LLM description for breadth).
