# Natural-Language Personal Topics — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-20
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility) (follow-on)
**Builds on:** [Personal Subscriptions](2026-06-19-personal-subscriptions-design.md) (the per-user write store, the NL-in-DM intent classifier, the `PersonalDigestAction`),
[Topics](2026-06-19-topics-design.md) (the `Topic` type + `summarize_shared` topic preamble / `NOTHING_RELEVANT` contract)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0006 — stateful config](../../adr/0006-stateful-config.md),
[ADR 0007 — access/visibility/redaction](../../adr/0007-access-visibility-redaction.md)

## Why this slice exists

[Topics](2026-06-19-topics-design.md) shipped as a **static, config-only** feature: a `topic:`
block (`name` + `description`) nested under a per-project or shared `digest:`, parsed once at load
into a frozen `Topic` and never mutated at runtime. There is no way to create, change, or list a
topic without editing `config/channels.yaml` and restarting.

This slice gives **individuals** runtime control over their own topics, **in natural language, in a
DM** — exactly the way [Personal Subscriptions](2026-06-19-personal-subscriptions-design.md) already
let a user follow/unfollow projects by writing prose to Babbla. A **Personal Topic** is a per-user
thematic filter bound to **one** project the user follows; it narrows that project's section of the
user's **Personal Digest** to only the changes that match.

### What is new here

- **New:** a per-user topic write store (a `personal_topics` table on the existing
  `PersonalSubStore`); topic intents in the existing DM intent classifier (`personal.py`);
  per-project topic filtering inside the Personal Digest.
- **Reused:** the entire NL-in-DM management flow (`classify_intent` → internal `Command` →
  `Orchestrator._dispatch_command` → reply renderer), the `summarize_shared` digest aggregator, the
  `NOTHING_RELEVANT` "stay silent" contract, and the open-tier visibility predicate.

### Decisions made during brainstorming

- **Scope:** Personal Digest topics only. Per-project channel digests and shared/portfolio digests
  keep their static config `topic:` (unchanged). No change to those surfaces.
- **Granularity:** **N topics per user, each bound to exactly one project, multiple allowed per
  project, mutually independent.** A user can hold `security` + `lobby` on Babbla and `playback` on
  MyTV simultaneously.
- **Filter semantics — union per project:** a project's digest section includes changes relevant to
  **ANY** of that project's topics. A followed project with **no** topic shows **everything**
  (today's behavior, unchanged).
- **Description source — auto-expand via LLM:** the user names a topic (`"security"`); the intent
  classifier expands it into a steering `description` (`"auth, secrets, access control, CVEs,
  dependency security bumps"`) and Babbla **confirms back** what it set, so the user can correct an
  over-eager expansion by restating.
- **Management surface — NL-in-DM only.** No slash command, no user-typed syntax. (Subscriptions
  have both a slash command and NL; topics get **only** the NL path.) The classifier still emits an
  internal command line that the dispatcher parses — an implementation detail the user never sees,
  identical in spirit to how `subscribe <project>` is emitted today.
- **Follow-first:** a topic only makes sense for a project the user's Personal Digest covers. Adding
  a topic to a project the user does not follow is refused with a nudge to subscribe first — **no
  auto-subscribe**.

### Impact when unconfigured / unused

- A user who never asks Babbla to manage a topic has **no `personal_topics` rows**; their Personal
  Digest behaves **exactly as today** (every followed project shown in full).
- The Personal Digest is still gated on the `personal_digest` config block (unchanged). Topics add
  no new top-level config and nothing to `channels.yaml`.
- Per [ADR 0003](../../adr/0003-read-only-by-construction.md): topics are per-user state Babbla
  writes about **itself**, never a write toward any project — fully consistent with read-only.

## Architecture & module layout

Mirrors the Personal Subscriptions split; **no new module**.

- **`src/babbla/session_store.py`** — extend `PersonalSubStore` with a `personal_topics` table and
  topic methods (per-user state stays in one cohesive store; Decision A1).
- **`src/babbla/personal.py`** — extend the `Command` dataclass, `parse_command`, `_MGMT_VERBS`,
  the `classify_intent` system prompt, and add topic reply renderers. Still pure, no I/O.
- **`src/babbla/digest/runner.py`** — `summarize_shared` gains an optional per-project topic map and
  emits per-section topic instructions (Decision B1).
- **`src/babbla/digest/actions.py`** — `PersonalDigestAction` loads the user's topics, threads the
  map into `summarize_shared`, and gains the empty-post guard (bug fix, below).
- **Touch points:** `orchestrator.py` (`_dispatch_command` topic cases). No `app.py`, no
  `slack_adapter.py`, no Slack-manifest change — there is no new command or event.

## Data model & store (Decision A1)

One new table, owned by the existing `PersonalSubStore`.

```sql
CREATE TABLE IF NOT EXISTS personal_topics (
    user_id      TEXT NOT NULL,
    project_name TEXT NOT NULL,
    name         TEXT NOT NULL,   -- normalized: trimmed + casefolded (stable identity / dedup)
    description  TEXT NOT NULL,   -- LLM-expanded steering text (or the name, if expansion empty)
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, project_name, name)
);
```

Methods (all `async`, via the store's existing `asyncio.to_thread` + `sqlite3` pattern):

- `add_topic(user_id, project, name, description) -> None` — `INSERT OR REPLACE`: re-adding an
  existing `(project, name)` **updates** its description (idempotent restate).
- `remove_topic(user_id, project, name) -> None` — `DELETE` (idempotent).
- `topics_for(user_id) -> dict[str, tuple[tuple[str, str], ...]]` — `{project_name: ((name,
  description), …)}`, stable order by `created_at`. Empty dict when the user has none.

`name` is stored normalized (trimmed, casefolded) so `Security` and `security` are the same topic;
`description` is stored as produced. The display name in confirmations is the user's wording for
that turn.

## Internal classifier contract (not user-facing)

There is **no slash command and no user-typed syntax**. The user writes prose; the existing
`classify_intent` LLM (extended) maps it to **one internal command line** or `NONE`. These lines are
emitted by the model and read by `parse_command`; users never see or type them. A `|` field
separator (rare in prose, unlike the space-delimited subscription grammar) carries the multi-word
project / name / description:

```
topic add <project> | <name> | <description>
topic remove <project> | <name>
topic list
```

- `Command` gains optional `project` / `name` / `description` fields (alongside today's `verb` /
  `arg`).
- `_MGMT_VERBS` gains `topic`; `_command_line` already takes the last verb-leading line, so a
  `topic …` line routes to topic dispatch and anything else (prose, `NONE`) still falls through to
  Q&A.
- `parse_command` special-cases the `topic` verb: split the remainder on `|` into
  `(subverb, project, name, description)`; a malformed line (missing required field) → `help`.
- The **classifier performs the description expansion** (Decision: auto-expand). Its system prompt
  gains topic examples, e.g.:
  - `"only show me security changes in Babbla"` → `topic add Babbla | security | auth, secrets,
    access control, CVEs, dependency security bumps`
  - `"stop filtering Babbla to lobby"` / `"remove the lobby topic from Babbla"` → `topic remove
    Babbla | lobby`
  - `"what topics do I have"` / `"my filters"` → `topic list`
  - project names copied **exactly** from the catalog list already passed to the classifier.

This keeps the user experience identical to subscriptions (write prose, get a confirmation), while
the only structural cut versus subscriptions is the absent slash twin.

## Dispatch (`Orchestrator._dispatch_command`)

New cases for the `topic` verb, reusing the subscription validation helpers:

- **`topic add`** — validate the project is **(a) known** in the catalog (else
  `render_unknown_project`), **(b) open-tier** (else `render_private_refused` — a private project
  can't be in a Personal Digest anyway), and **(c) already followed** by this user (else a new
  *follow-first* nudge — no auto-subscribe). On success: `add_topic(...)`, then **confirm back** the
  name **and** the expanded description so the user can restate to correct it.
- **`topic remove`** — `remove_topic(...)`; idempotent confirmation (no error if it wasn't set).
- **`topic list`** — render the user's topics grouped by project; empty → a friendly "no topics;
  here's how to add one in plain English" line. (Kept a **distinct** verb rather than folded into
  the subscriptions `list`, so each reply stays focused; the subscriptions `list` may cross-reference
  it.)

All policy stays in the orchestrator; `personal.py` only parses and renders.

## Personal Digest filtering (Decision B1)

`PersonalDigestAction._maybe_run_user` already computes `per_project_changes` (the changed commits
per followed open-tier project) and `heads`. It additionally loads `topics_for(user_id)` and builds
a per-project topic map for the projects present in `per_project_changes`, then passes it into the
aggregator:

```
topics_by_project = personal_store.topics_for(user_id)        # {project: ((name, desc), …)}
text = await runner.summarize_shared(
    context_binding, per_project_changes, topics_by_project=topics_by_project
)
if text.strip():                                              # <-- empty-post guard (bug fix)
    await poster.post(dm_channel, text, blocks=delete_button_blocks(text, owner_id=user_id))
await personal_digest_state.advance(user_id, heads, now.timestamp())
```

`runner.summarize_shared` gains an optional `topics_by_project` argument. For each project section:

- **No topics** for that project → section rendered in full (unchanged).
- **Topics present** → the section prompt instructs: *"Include ONLY changes relevant to ANY of these
  topics: `<name>` (`<description>`), …. If none of this project's changes are relevant, omit this
  project's section entirely."*
- A whole-response guard preserved from Topics: *"If no project section has any relevant content,
  reply with exactly `NOTHING_RELEVANT`."* The runner maps `NOTHING_RELEVANT` → `""` (as today).

**Bug fix folded in:** `PersonalDigestAction` currently calls `poster.post(...)`
**unconditionally**. With per-project filtering, every section can drop out, leaving an empty
summary — which would DM a blank message. The `if text.strip()` guard (mirroring
`PerProjectDigestAction._emit`) skips the post but **still advances the watermark**, honoring the
"stay quiet but don't repeat" contract. This guard is correct independent of topics and is the same
class of empty-payload omission the per-project digest already has.

**Delete button:** the post adopts the `delete_button_blocks(text, owner_id=user_id)` the Personal
Digest already uses (a real owner id — unaffected by the empty-`value` fix from commit `aa76c39`).

### Filtering interaction with the watermark

Filtering changes only what is *summarized*, never what is *advanced*. `heads` is computed from the
real commit range, so the watermark advances for **all** followed projects regardless of whether
their section was filtered out — the user is not re-shown filtered-away commits next cycle. This
matches the existing per-project digest semantics.

## Error handling & edge cases

- **`topic add` to an unfollowed project** → follow-first nudge; nothing written.
- **`topic add` to an unknown project** → `render_unknown_project`; nothing written.
- **`topic add` to a private project** → `render_private_refused`; nothing written (a private
  project can't be in a Personal Digest).
- **Duplicate `topic add` (same project+name)** → description updated (`INSERT OR REPLACE`),
  idempotent; confirmation reflects the new description.
- **`topic remove` of a non-existent topic** → idempotent confirmation, no error.
- **Empty description after expansion** → fall back to `name` as the description (never store an
  empty steering string).
- **Classifier unsure / prose / greeting** → `NONE` → falls through to the Q&A agent (unchanged).
- **Topic on a project the user later unfollows** → orphan rows are harmless: the digest only reads
  `topics_for` for projects in `per_project_changes`, which is derived from the followed set. (No
  cascade delete in this slice; revisit only if it proves confusing.)
- **Digest: all sections filtered out** → no DM, watermark still advances (the new guard).
- **Digest: some sections filtered, others kept** → DM contains only the kept sections.
- **Unconfigured / no topics** → fully inert; digest identical to today.

## Testing

All deterministic — injected fakes (classifier `intent_fn`, `runner`, `poster`, stores); no network,
no real model.

### `tests/test_session_store.py` (extend)

- `PersonalSubStore` topics: `add_topic`/`remove_topic` idempotency; `INSERT OR REPLACE` updates
  description; **multiple topics per project**; per-user isolation; name normalization
  (`Security` == `security`); `topics_for` grouping + order.

### `tests/test_personal.py` (extend)

- `parse_command` topic lines: `add`/`remove`/`list`; `|`-split into fields; missing required field
  → `help`; case/whitespace tolerance.
- `classify_intent`: NL phrases → topic command lines (via a fake `intent_fn`); description present
  in an `add`; ambiguous/greeting → `NONE` (falls through).
- Topic reply renderers: confirm-add (shows name + description), removed, list (none / grouped),
  follow-first nudge.

### `tests/test_orchestrator.py` (extend; fakes)

- `_dispatch_command` topic cases: add to followed project → persisted + confirm; add to unfollowed
  → follow-first, nothing written; add to unknown → catalog reply; add to private → refused; remove;
  list.

### `tests/test_personal_digest.py` (extend)

- Union filter: a project with two topics includes changes matching **either**.
- A followed project with **no** topic is unfiltered.
- **All** sections filtered out → no DM, watermark advances (the empty-post guard).
- **Mixed** → only matching sections sent.
- Regression: a user with **no** topics gets today's full digest (no behavior change).

### `tests/test_digest_*` / runner (extend)

- `summarize_shared(topics_by_project=…)`: per-section topic instruction present only for projects
  with topics; `NOTHING_RELEVANT` → `""`; absent map → byte-for-byte today's prompt (no regression
  for the existing shared/personal callers).

## Scope summary

- **New:** `personal_topics` table + topic methods on `PersonalSubStore`; topic verbs in
  `personal.py` (`Command` fields, `parse_command`, `_MGMT_VERBS`, classifier prompt, renderers);
  per-project topic filtering in `summarize_shared`; the `if text.strip()` empty-post guard in
  `PersonalDigestAction`.
- **Changed:** `session_store.py`, `personal.py`, `orchestrator.py` (`_dispatch_command`),
  `digest/runner.py`, `digest/actions.py`.
- **Unchanged:** `app.py`, `slack_adapter.py`, the Slack manifest (no new command/event);
  per-project and shared static config `topic:` (untouched); `config/channels.yaml` (no new block).
- **Behavior when unconfigured / no topics:** none (fully inert).

## Out of scope (deferred)

- **Slash command for topics** — explicitly cut; NL-in-DM only.
- **Topics on per-project channel digests / shared digests via NL** — those keep their static config
  `topic:`; runtime NL management is personal-only this slice.
- **Per-(user, project) multiple-topic *intersection* (AND) semantics** — union only.
- **Cascade-delete of topics when a user unfollows a project** — orphans are inert; revisit on real
  confusion.
- **A "rename topic" verb** — restating an `add` with the same name updates the description, which
  covers the common case.
