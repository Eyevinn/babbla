# Private projects in personal subscriptions, gated on live channel membership — Design

**Status:** Approved (brainstorming)
**Date:** 2026-06-22
**Implements:** [ADR 0017 — Personal subscriptions may cover private projects, gated on live channel membership](../../adr/0017-private-personal-subscriptions-on-membership.md)
**Refines:** [ADR 0007 — Access = Slack membership; visibility tiers; PII redaction](../../adr/0007-access-visibility-redaction.md)
**Related:** [ADR 0006 — stateful config](../../adr/0006-stateful-config.md)

## Why this change

Today a private project is excluded from personal subscriptions in three places, and the
personal digest excludes it in a fourth — all via a flat `is_open_tier(binding)` check that
makes the decision from config alone, with no Slack call:

- `orchestrator.py:97` — **subscribe** refuses a non-open-tier project (`render_private_refused`).
- `orchestrator.py:79` — **topic add/remove** refuses a non-open-tier project.
- `orchestrator.py:164` — **DM answer** denies a private project at answer time
  (the "flip-after-subscribe" guard, `authorize_ask(binding, Surface.DM)`).
- `digest/actions.py:100` — **personal digest** filters bindings to `is_open_tier` only.

This is stricter than ADR 0007's own principle ("access is Slack channel membership"). A member
of a private project's channel is already authorized for that project's content. The change:
**a private project may be followed, asked about in a DM, topic-filtered, and delivered in the
personal digest — for as long as the user is a live member of the project's bound private
channel.** The check is live every time, fail-closed, and never auto-unsubscribes (ADR 0017).

## Architecture

`access.py` stays a **pure decision module** (no I/O). The Slack membership lookup lives at the
edges (orchestrator, digest action) behind an injected **membership oracle**, and fires only for
private bindings — open-tier paths stay call-free.

```
caller (orchestrator / digest)
  ├─ is_open_tier(binding)? ──────────────── yes ─► allow (no Slack call)
  └─ private ─► await membership(user_id, binding.channel_id)  ─► access.authorize_personal(...)
                         │
                         └─ membership oracle (babbla/membership.py) ─► Slack conversations.members
                                                                         (fail-closed on error)
```

### Component 1 — membership oracle (`babbla/membership.py`, new)

A factory that builds an async callable:

```
make_membership(client, *, ttl_seconds=5) -> (async (user_id: str, channel_id: str | None) -> bool)
```

- Returns `False` immediately if `channel_id` is `None`.
- Calls `client.conversations_members(channel=channel_id, limit=200)`, paginating via
  `response_metadata.next_cursor`, and returns whether `user_id` is in the member set.
  Short-circuits as soon as `user_id` is found.
- **Fail closed:** any `SlackApiError`, transport error, or timeout is caught, logged, and
  returns `False`. A withheld answer is acceptable; a leak is not.
- **TTL cache:** an in-process dict keyed by `(channel_id, user_id)` → `(is_member, expires_at)`,
  with `ttl_seconds` default 5. This absorbs bursts within a single thread/turn without violating
  "live every time" in any meaningful window. Only positive *and* negative results are cached
  (both expire). The cache is best-effort and process-local; it is not persisted.
- Time source: the oracle is constructed with a `now_fn` (defaults to the real clock) so tests
  control expiry deterministically.

Requires the Slack scope `groups:read` (read membership of private channels). Babbla is already a
member of project channels (it must be, to receive mentions), so `conversations.members` succeeds.

### Component 2 — `access.py` (extend)

Add a pure function alongside `authorize_ask`:

```
def authorize_personal(binding: ProjectBinding, *, is_member: bool) -> AccessDecision
```

- `is_open_tier(binding)` → `AccessDecision(allowed=True)`.
- private **and** `is_member` and `binding.channel_id` is set → `AccessDecision(allowed=True)`.
- otherwise → `AccessDecision(allowed=False, reason=…, pointer=_pointer(binding))`.

`authorize_ask` and the `CHANNEL` surface are **unchanged**. `_pointer(binding)` (the existing
0007-style "ask about it in <#channel>" text) is reused — a non-member never sees "doesn't exist."

### Component 3 — `orchestrator.py` (three call sites)

The orchestrator receives an injected `self._membership` (the oracle) and a `user_id` already in
scope at every site below.

- **subscribe** (`:90–100`): if `is_open_tier(binding)` → subscribe as today. Else
  `member = await self._membership(user_id, binding.channel_id)`,
  `decision = authorize_personal(binding, is_member=member)`; on deny return `decision.pointer`,
  on allow `add(user_id, binding.name)` + `render_subscribed`.
- **topic add/remove** (`:73–89`): same membership gate replaces the `is_open_tier` refusal at
  `:79`. A member may topic-filter a private project; a non-member gets the pointer.
- **DM answer** (`_handle_personal_ask`, `:164`): replace `authorize_ask(entry.binding, Surface.DM)`
  with the membership gate — `member = await self._membership(user_id, entry.binding.channel_id)`
  then `authorize_personal`. (Plumb `user_id` into `_handle_personal_ask` /
  `_resolve_subscription`; it is available at the `handle_ask` caller.)

`render_private_refused` is **removed** — every private refusal now routes through
`authorize_personal` → `_pointer`, so the message is the consistent 0007 pointer. The
`render_unknown_project` advertising list keeps listing **open-tier only** (no private names
leaked to non-members).

### Component 4 — `digest/actions.py` (`PersonalDigestAction`)

`PersonalDigestAction.__init__` gains an injected `membership` oracle. In `_maybe_run_user`, the
binding filter (`:98–101`) becomes per-user membership-aware:

```
bindings = []
for n in names:
    b = self._by_name.get(n)
    if b is None: continue
    if is_open_tier(b) or await self._membership(user_id, b.channel_id):
        bindings.append(b)
```

This is the single membership snapshot for this user for this run (the run is one moment in time);
it is not persisted across runs. Everything downstream (head/watermark diffing, `summarize_shared`,
the PII-redaction path of 0007, DM delivery) is unchanged. A non-member's private project is simply
absent from `bindings`; on rejoin it reappears on the next run.

### Component 5 — wiring (`app.py`)

Build one oracle from `app.client` and inject it into both the `Orchestrator`
(`build_orchestrator`) and `PersonalDigestAction` (`build_scheduler`). When no client is available
(unit/headless context), inject a **deny-by-default stub** — a small async function that ignores
its arguments and returns `False` — so private projects stay locked unless an oracle is wired;
fail-closed by construction.

## Error handling & edge cases

- **Slack lookup fails / times out** → oracle returns `False` → private content withheld (deny on
  ask/topic, omitted from digest). Logged, never raised to the user as an error.
- **Private binding with no `channel_id`** → `authorize_personal` denies; pointer falls back to the
  existing "private and has no channel yet" text. Such a project is effectively non-subscribable.
- **User removed from channel after subscribing** → next ask is denied (pointer), next digest omits
  it; the subscription record is kept. Re-joining restores access with no user action (ADR 0017).
- **Open-tier projects** never trigger a Slack call (the `is_open_tier` short-circuit precedes the
  oracle at every site).
- **Topic on a project the user no longer belongs to** → the topic record persists but is moot while
  the project is excluded from their digest.

## Read-only preserved

No change to the agent tool surface (ADR 0003) or to what is read from repos. The only new external
call is a Slack **read** (`conversations.members`); no Slack writes beyond the existing DM posting.

## Testing (TDD)

- `access.py` — pure truth table for `authorize_personal`: open-tier (allow, ignores membership);
  private+member+channel (allow); private+non-member (deny+pointer); private+no-channel (deny).
- `membership.py` — fake Slack client: member present across pages (True); absent (False);
  `channel_id=None` (False, no call); `SlackApiError` (False, fail-closed); TTL cache hit avoids a
  second call within window and re-fetches after expiry (driven by injected `now_fn`).
- `orchestrator.py` — fake oracle (member / non-member / raising) across subscribe, topic-add, and
  DM answer: assert allow vs pointer; assert the oracle is **not** called for open-tier bindings.
- `digest/actions.py` — fake oracle: private project included for a member, omitted for a
  non-member, reappears when membership flips back; open-tier unaffected.

## Scope summary

One consistent membership rule across all four personal entry points (subscribe, topic, DM answer,
digest), backed by one oracle, with `access.py` kept pure. `render_private_refused` removed in favor
of the unified pointer.

## Out of scope (future)

- Background membership sweeps or push-based channel-leave handling (the live check makes them
  unnecessary).
- Caching membership across digest runs or auto-unsubscribing on leave (explicitly rejected, ADR 0017).
- Any change to the Lobby ("points-don't-reveal" unchanged), to channel asks, or to PII redaction.
- Advertising private project names in discovery lists.
