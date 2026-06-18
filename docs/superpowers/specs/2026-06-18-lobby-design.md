# Lobby — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-18
**Slice of:** [Phase 4 — Lobby + Subscriptions / Visibility](../../ROADMAP.md#phase-4--lobby--subscriptions--visibility)
**Builds on:** [Visibility Enforcement](2026-06-18-visibility-enforcement-design.md) (slice 1)
**Related:** [ADR 0003 — read-only by construction](../../adr/0003-read-only-by-construction.md),
[ADR 0007 — access/visibility/redaction](../../adr/0007-access-visibility-redaction.md),
[ADR 0009 — repo is source of truth for "why"](../../adr/0009-repo-is-source-of-truth-for-why.md),
[PROPOSAL-design.md — Surfaces & access model](../../PROPOSAL-design.md)

## Why this slice exists

This is slice 2 of Phase 4 (Visibility → **Lobby** → Subscriptions). The Lobby is the open
discovery + onboarding surface from the proposal's surface model: a single place where anyone can
**Ask without naming a project**. Babbla locates the relevant project, answers it (for
public/internal projects), and points the asker to that project's channel — discovery doubles as
onboarding.

### What is new here

Today **every Slack surface resolves to exactly one project** (`Config.for_channel` /
`Config.for_dm`). The Lobby is the first surface with **no pre-bound project**: it must *locate* one
among all bindings from free text. That routing step is the substance of this slice. Everything
downstream is reused:

- `authorize_ask(binding, surface)` (slice 1) already handles a non-channel surface correctly — any
  surface that is not `Surface.CHANNEL` takes the `public`/`internal`→allow, `private`→deny-and-point
  branch. So the Lobby reuses the slice-1 gate by **only adding `Surface.LOBBY` to the enum**.
- `AgentRunner.run_ask(text, binding, resume_session_id)` already builds a per-project agent
  (system prompt + GitHub MCP scoped to `owner/repo`) from whichever `binding` it is handed. The
  Lobby just picks the binding.

### Decisions made during brainstorming

- **Surface:** a dedicated Lobby channel, configured as a top-level `lobby_channel_id` in
  `channels.yaml`. The bot answers @-mentions there. Distinct from per-project channels and from the
  Private-Ask DM.
- **Routing mechanism:** an **LLM classifier**, dependency-injected (mirroring
  `AgentRunner.query_fn`), so the path is deterministic and offline-testable.
- **Routing catalog source:** the repo's native GitHub `description`, fetched read-only at startup
  (purest "repo is source of truth"). Degrades to name/repo-only routing if GitHub is unreachable.
- **Threaded follow-ups:** **sticky** — route once per thread, remember the chosen project, resume
  its session on follow-ups.

### Two visibility calls (approved)

- **Targeted private match** → name the project and point to its channel. Per the proposal's
  "points-don't-reveal", naming + a channel pointer is permitted; repo *content* is never surfaced.
- **The discovery / no-match list** advertises **public + internal projects only** — it never
  enumerates private projects to a non-member. (Answering a question that already describes a private
  project is different from advertising its existence unprompted.)

### Impact when unconfigured

With no `lobby_channel_id` set (the committed template default), the Lobby branch in the adapter is
never taken — **zero behavior change** for existing channel/DM Asks. Like slice 1, this is built
ahead of need; it activates when a lobby channel is configured and a second project is onboarded.

## Architecture & request flow

The Lobby is a routing layer in front of the existing Ask machinery. A Lobby @-mention flows through
`orchestrator.handle_lobby_ask(*, text, thread_ts)`:

```
1. Sticky check:  LobbyThreadStore.get(thread_ts) → project already chosen for this thread?
      ├─ yes → use that binding (skip routing)
      └─ no  → route(text, catalog, classify_fn) → CatalogEntry | None
2. None (no confident match) → discovery reply (public+internal projects + their channels)
3. entry → authorize_ask(binding, Surface.LOBBY):
      ├─ deny (private)  → points-don't-reveal reply (name + channel); no run_ask, no sticky write
      └─ allow           → persist sticky project; run_ask(binding, resume); answer + pointer suffix
```

Even on a sticky-thread hit, `authorize_ask` runs before answering, so a mid-thread visibility flip
to `private` correctly switches follow-ups to points-don't-reveal. Lobby asks reuse the
orchestrator's existing per-thread lock, so two messages in one thread serialize (no double-routing
race).

## Components & files

### New: `src/babbla/lobby.py`

The routing layer — pure given its injected functions.

- `@dataclass(frozen=True) CatalogEntry`: `binding: ProjectBinding`, `description: str | None`.
  Holding the whole binding (rather than copying its fields) means `entry.binding` feeds straight
  into `authorize_ask(entry.binding, …)` and `run_ask(text, entry.binding, …)` with no re-mapping;
  `discovery_reply` / `pointer_suffix` read `entry.binding.name` / `.channel_id` / `.visibility`.
- `build_catalog(bindings, get_json) -> tuple[CatalogEntry, ...]`: fetches each repo's GitHub
  description via `GET /repos/{owner}/{repo}` (reusing `digest.anchors.make_get_json`). Per-entry
  try/except: any fetch failure → `description=None`. Includes **all** projects (private too, so
  they can route → deny → point). Built once at startup.
- `route(text, catalog, classify_fn) -> CatalogEntry | None`: calls `classify_fn(text, catalog)`
  (the model), maps the reply to a catalog entry by **exact name**; returns `None` for `"NONE"`,
  unrecognised replies, or prose containing no known name. Pure.
- `make_classify_fn(query_fn, model) -> classify_fn`: the default classifier — a tools-less SDK
  query whose prompt lists each entry's `name + description` and asks for exactly one project name
  or `NONE`. Injected so tests pass a fake.
- `discovery_reply(catalog) -> str`: lists public+internal entries with `<#channel>` links
  (excludes private; omits link when `channel_id is None`).
- `pointer_suffix(entry) -> str`: the "↪ join `<#channel>`" onboarding nudge appended to answers;
  degrades to no link when `channel_id is None`.

### New: `LobbyThreadStore` in `src/babbla/session_store.py`

Mirrors `DigestStateStore`. Own table — no migration of the existing `sessions` table:

```sql
CREATE TABLE IF NOT EXISTS lobby_threads (
    thread_ts    TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    updated_at   REAL NOT NULL
)
```

- `get(thread_ts) -> str | None` (TTL-expiring, same pattern/`time_fn` as `SessionStore`).
- `put(thread_ts, project_name) -> None` (UPSERT).

A Lobby thread uses **both** stores: `SessionStore` for `session_id` resume, `LobbyThreadStore` for
the sticky project.

### Changed: `src/babbla/access.py`

Add `LOBBY = "lobby"` to `Surface`. No change to `authorize_ask` — `LOBBY` is not `CHANNEL`, so it
already takes the non-channel branch.

### Changed: `src/babbla/config.py`

Add optional top-level `lobby_channel_id: str | None` to `Config`, parsed from a top-level key in
`channels.yaml` (not under `projects:`). Absent → `None`.

### Changed: `src/babbla/orchestrator.py`

Add `handle_lobby_ask(*, text, thread_ts)` implementing the flow above. The catalog, router /
`classify_fn`, and `LobbyThreadStore` are constructor dependencies alongside the existing runner and
session store.

### Changed: `src/babbla/slack_adapter.py`

In the `app_mention` handler: if `event["channel"] == config.lobby_channel_id` →
`handle_lobby_ask`; else the existing `handle_ask`. Lobby replies post in-thread exactly as today.
With `lobby_channel_id is None`, all mentions go to `handle_ask`.

### Changed: `config/channels.yaml`

Document the new top-level `lobby_channel_id`, left `null` in the committed template (the user's
local value stays unstaged, per repo convention).

## Error handling & edge cases

- **Classifier failure / timeout** → treated as `None` (discovery reply), never crashes. The model
  returning prose instead of a bare name is handled by `route`'s exact-name mapping (→ `None`).
- **Description fetch failure at startup** (GitHub down, 404, auth) → that entry's
  `description=None`; routing still works on name/repo. Startup never blocks (per-entry try/except,
  15s timeout like the digest reader).
- **Matched project `channel_id=None`** → answer still posts; pointer degrades to no link.
- **Private match `channel_id=None`** → points-don't-reveal with the graceful no-link message
  (reuses slice 1's wording shape).
- **Lobby not configured** → adapter branch never taken; zero impact on existing asks; slice inert.
- **Sticky re-authorization** → `authorize_ask` runs even on a sticky hit, catching mid-thread
  visibility flips.
- **Concurrency** → existing per-thread lock serializes messages in a thread.
- **One failed Lobby ask never crashes the process** → the adapter's existing `try/except` around
  `process_ask` covers it.

## Testing

All deterministic — `classify_fn` and `get_json` are injected fakes; no network, no real model calls.

### `tests/test_lobby.py` (new)

- `build_catalog`: fake `get_json` returns descriptions → entries carry them; `get_json` raising for
  one repo → that entry `description is None`, others unaffected; private projects included.
- `route`: fake `classify_fn` returns an exact name → that entry; `"NONE"` → `None`; unknown/garbage
  → `None`; prose with no known name → `None`.
- `discovery_reply`: lists public+internal with `<#channel>` links; excludes private; omits link
  when `channel_id is None`.
- `pointer_suffix`: includes `<#channel>` when set; degrades without it when `None`.

### `tests/test_session_store.py` (extend)

- `LobbyThreadStore` get/put round-trip; TTL expiry via injected `time_fn`; unknown thread → `None`.

### `tests/test_access.py` (extend)

- `Surface.LOBBY` rows: private+LOBBY → deny+pointer; public/internal+LOBBY → allow.

### `tests/test_orchestrator.py` (extend; fake runner + fake stores + fake router)

- New Lobby thread, router → public project → `run_ask` called with that binding; sticky project +
  session both persisted; answer carries the pointer suffix.
- Sticky thread hit → router **not** called; `run_ask` resumes with the stored session.
- Router → `None` → discovery reply; `run_ask` **not** called; nothing persisted.
- Router → **private** project → points-don't-reveal reply; `run_ask` **not** called; no sticky
  persisted.
- Sticky thread whose project is now private → re-authorized → points-don't-reveal.

### `tests/test_slack_adapter.py` (extend)

- `app_mention` in `lobby_channel_id` → `handle_lobby_ask`; mention elsewhere → `handle_ask`;
  `lobby_channel_id=None` → all mentions → `handle_ask`.

### `tests/test_config.py` (extend)

- Top-level `lobby_channel_id` parses; absent → `None`.

## Scope summary

- **New:** `src/babbla/lobby.py`, `tests/test_lobby.py`
- **Changed:** `session_store.py` (+`LobbyThreadStore`), `access.py` (+`Surface.LOBBY`),
  `config.py` (+`lobby_channel_id`), `orchestrator.py` (+`handle_lobby_ask`),
  `slack_adapter.py` (lobby dispatch), `config/channels.yaml` (documented `null`)
- **Behavior when no lobby channel is configured:** none (fully inert)

## Out of scope (later Phase 4 slice)

- **Subscriptions / Topics** — Shared (channel ↔ many projects) and Personal (persisted individual
  interests); ask/digest scoping. The Lobby routes to a *single* best-match project; multi-project
  fan-out and subscription scoping belong to slice 3.
- **Cheaper classifier model** — routing uses the configured model via the injected `classify_fn`;
  swapping in a cheaper/faster model for classification is a later optimization, not required now.
- **Description refresh** — the catalog is fetched once at startup; periodic refresh is YAGNI.
