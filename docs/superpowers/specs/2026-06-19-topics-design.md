# Topics — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-19
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Builds on:** [Scheduled Actions Framework](2026-06-19-scheduled-actions-design.md) (the digest actions + runner),
[Shared Subscriptions](2026-06-19-shared-subscriptions-design.md) (the subscription digest)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0008 — release-anchored digests](../../adr/0008-release-anchored-digests.md),
[PROPOSAL-design.md — Subscription / Topic / Digest](../../PROPOSAL-design.md)

## Why this slice exists

A **Topic** is the proposal's term for *"a thematic slice of interest within or across Projects
(e.g. security changes, a feature area, incidents) — lets a Subscription be narrower than a whole
Project."* It is the last open slice of Phase 4, deferred until now as the fuzziest item because
*how* a thematic slice is defined and matched was unclear.

This design resolves the fuzziness by scoping the first Topics slice tightly: **digest-only,
LLM-scoped.** A Topic is a named, described thematic filter attached to a digest config; the digest
summarizer is instructed to cover only changes matching the topic and to stay silent when none
match this period. Asks are untouched.

### Decisions made during brainstorming

- **Surface: digest-only.** Ask-scoping was rejected for the first slice as low marginal value (a
  user can already ask a scoped question free-form) and fuzzier (relevance judged live per
  question). Digest-scoping is the concrete, recurring value: a standing "security" or "incidents"
  digest on a cadence is a push you cannot trivially reproduce by asking.
- **Matching: LLM-scoped, not deterministic.** A Topic is a natural-language description; matching
  happens inside the single summarization call the digest already makes. Deterministic matching
  (PR labels / file-path globs) is more precise but brittle and leans on projects maintaining label
  conventions — which cuts against Babbla's no-pollution / graceful-degradation stance. LLM-scoping
  degrades gracefully (sparse signal → thinner digest, never a failure) and matches how the Lobby
  classifier already works.
- **Attaches to both digest kinds:** `DigestConfig` (per-project digest) and `SubscriptionDigest`
  (shared / portfolio digest). Same `Topic` type, same prompt-threading — covering both is nearly
  free and matches the definition's "within **or across** Projects." **Personal-digest topics are
  deferred** (they would need per-user topic config via `/babbla`, a separate surface).
- **One topic per digest** (YAGNI). Multiple thematic digests on a channel = multiple
  subscriptions. A list of topics per digest is revisited only on a real need.
- **Watermark on an empty match** (the one subtle behavior): if there *were* new changes this
  period but none match the topic, the digest **advances its watermark** (the changes were
  evaluated against the topic and found irrelevant — no reason to re-examine them) but **posts
  nothing**. This differs from today's "no new changes at all" path, which does not advance. So a
  topic digest goes silent without endlessly re-scanning the same commits.
- **Inert / back-compatible:** a `digest:` block with no `topic:` sub-block behaves exactly as
  today — the new post-guard is a no-op when the summary is non-empty (which it always is for an
  unscoped digest).

## Architecture & data flow

Topics ride the **existing digest pipeline** — no new module, no new action, no `app.py` change. The
only moving parts are a config field, a runner prompt extension, and a post-guard in the two digest
actions.

```
config (digest.topic) ──▶ PerProjectDigestAction / SharedDigestAction
                              │  changes gathered as today (anchors/watermark)
                              ▼
                          DigestRunner.summarize[_shared](… , topic=…)
                              │  topic present → scoping preamble + NOTHING_RELEVANT rule
                              ▼
                          text  ("" when the model replies NOTHING_RELEVANT)
                              │
              ┌───────────────┴───────────────┐
        text.strip()                      not text.strip()
              │                                │
        post + advance                   advance only (silent)
```

## Components & files

### Changed: `src/babbla/config.py`

```python
@dataclass(frozen=True)
class Topic:
    name: str
    description: str
```

- `DigestConfig` gains `topic: Topic | None = None`; `SubscriptionDigest` gains
  `topic: Topic | None = None`.
- New helper `_parse_topic(label: str, raw: dict | None) -> Topic | None`:
  - `None`/absent → `None`.
  - present but missing `name` or `description` (empty/whitespace counts as missing) →
    `ValueError(f"{label}: topic requires both name and description")` (fail-fast, matching the
    existing `_parse_digest` / `_parse_cadence_tz` style).
  - otherwise → `Topic(name=str(raw["name"]).strip(), description=str(raw["description"]).strip())`.
- Wire `_parse_topic` into `_parse_digest` (label = project name) and into the subscription-digest
  parse path in `_parse_subscriptions` (label = `f"subscription {channel_id}"`). The subscription
  digest is currently built as `SubscriptionDigest(cadence=ct[0], tz=ct[1])` from a `_parse_cadence_tz`
  result; extend it to also parse `raw_sub.get("digest")`'s `topic` sub-block.

### Changed: `src/babbla/digest/runner.py`

- `summarize(self, binding, changes, head_sha, topic: Topic | None = None) -> str`
- `summarize_shared(self, context_binding, per_project_changes, topic: Topic | None = None) -> str`
- A shared private helper builds the scoping preamble when `topic` is not None and appends the
  sentinel rule:

  ```
  This digest is scoped to the topic "<name>": <description>.
  Include ONLY changes relevant to this topic; omit everything else — do not pad.
  If NONE of the changes below are relevant to this topic, reply with exactly: NOTHING_RELEVANT
  ```

- After the agent call, if a topic was supplied and the reply (stripped) equals the sentinel
  `NOTHING_RELEVANT`, the runner returns `""`. Otherwise it returns the answer text as today.
- With `topic=None` the prompt and return value are byte-for-byte unchanged from today.

### Changed: `src/babbla/digest/actions.py`

- `PerProjectDigestAction._emit` passes `topic=self._b.digest.topic` to `summarize`, and posts only
  when the returned text is non-empty; it advances the watermark **regardless**:

  ```python
  async def _emit(self, changes, head, now):
      if changes:
          text = await self._runner.summarize(self._b, changes, head, topic=self._b.digest.topic)
          if text.strip():
              await self._poster.post(self._b.channel_id, text)
      await self._store.advance(self._b.channel_id, head, now.timestamp())
  ```

- `SharedDigestAction` tail passes `topic=self._sub.digest.topic` to `summarize_shared`, guards the
  post, and advances regardless:

  ```python
  text = await self._runner.summarize_shared(
      context_binding, per_project_changes, topic=self._sub.digest.topic
  )
  if text.strip():
      await self._poster.post(sub.channel_id, text)
  await self._store.advance(sub.channel_id, heads, now.timestamp())
  ```

  (The existing `if not per_project_changes: return` guard above this is unchanged — a period with
  no new changes at all still returns early and does not advance.)

### Changed: `config/channels.yaml`

Commented `topic:` examples under both a project `digest:` and a subscription `digest:` (no real
values committed).

### Unchanged

`app.py` (topics ride existing digest wiring), `PersonalDigestAction` (personal topics deferred),
`orchestrator.py`, `slack_adapter.py`, the anchors/cadence/watermark layer, and all Ask paths.

## Error handling & edge cases

- **No `topic:` block** → `topic=None`; prompt, return value, post, and advance all identical to
  today (regression-guarded).
- **`topic:` present but missing `name` or `description`** → `ValueError` at config load
  (fail-fast).
- **New changes this period, none match the topic** → runner returns `""`; action **advances the
  watermark, posts nothing** (silent; changes won't be re-scanned next period).
- **New changes this period, some match** → runner returns a topic-scoped summary; action posts +
  advances as usual.
- **No new changes at all** (`head == watermark`, or `per_project_changes` empty) → existing
  early-return path; no post, no advance — unchanged.
- **Model ignores the sentinel and writes prose for an irrelevant period** → at worst an
  occasional thin/off-target digest, human-read and low-stakes; acceptable for an LLM-scoped filter
  (graceful degradation, consistent with the Lobby classifier's failure mode).
- **A scoping preamble cannot leak a private project** — topics attach only to digests, whose
  visibility is governed by the channel they post to (per-project digest → the project's channel;
  shared digest → the subscription channel). Topics add no new surface, so the established
  digest/visibility model is untouched.

## Testing

All deterministic — a fake agent runner returns canned text or the sentinel and captures the prompt;
no network, no real model.

### `tests/test_config.py` (extend)

- `topic:` under a project `digest:` parses into `Topic(name, description)`.
- `topic:` under a subscription `digest:` parses into `Topic(name, description)`.
- Absent `topic:` → `DigestConfig.topic is None` / `SubscriptionDigest.topic is None`.
- `topic:` present but missing `name` → `ValueError`; missing `description` → `ValueError`.

### `tests/test_digest_runner_poster.py` (extend; fake agent captures the prompt)

- `summarize(..., topic=Topic("security", "..."))` → the captured prompt contains the topic name,
  the description, and the `NOTHING_RELEVANT` rule.
- `summarize(..., topic=None)` → the captured prompt is the existing unscoped prompt (no preamble).
- Agent replies `NOTHING_RELEVANT` with a topic supplied → `summarize` returns `""`.
- `summarize_shared` mirrors the above (topic preamble injected; sentinel → `""`).

### `tests/test_digest_cadence.py` / per-project + shared digest action tests (extend)

- Topic digest, runner returns a non-empty summary → `poster.post` called **and** `store.advance`
  called.
- Topic digest, runner returns `""` (sentinel) → `poster.post` **not** called, `store.advance`
  **still** called (watermark advances; silent).
- Non-topic digest → posts and advances exactly as today (regression guard; `topic` threaded as
  `None`).

## Scope summary

- **Changed:** `config.py` (`Topic`, `topic` field on both digest configs, `_parse_topic`),
  `digest/runner.py` (`topic=` param + scoping preamble + sentinel normalization),
  `digest/actions.py` (pass topic through + post-guard + advance-regardless in both digest actions),
  `config/channels.yaml` (commented examples).
- **New:** none (no module, no action, no wiring).
- **Behavior when no `topic:` block:** none (fully back-compatible).

## Out of scope (deferred)

- **Ask-scoped topics** — narrowing a live Ask to a thematic slice (low marginal value; revisit on
  real need).
- **Personal-digest topics** — per-user topic config via `/babbla` (new surface).
- **Deterministic matching** — PR labels / file-path globs / keywords (brittle; pollution-adjacent).
- **Multiple topics per digest** — a list; use multiple subscriptions until a real need appears.
