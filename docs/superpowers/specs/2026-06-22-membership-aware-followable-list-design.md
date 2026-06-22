# Spec: Membership-aware "followable projects" advertising in DMs

**Date:** 2026-06-22
**Status:** Approved (brainstorming)
**Scope:** One bounded change to how the DM flow *advertises* which projects a
user can follow. No new tier, no schema change, no lobby change.

## Context

A user in a 1:1 DM with Babbla sees a "projects you can follow" list in two
places. Both currently advertise **open-tier projects only**
(`is_open_tier` → `public` + `internal`):

| Surface | Location |
|---|---|
| Onboarding gate (zero subscriptions) | `orchestrator.py` `handle_ask()` → `render_no_subscriptions` |
| Unknown-project hint (bad `follow`/`topic` target) | `orchestrator.py` `_dispatch_command()` → `render_unknown_project` (two call sites) |

This creates an asymmetry. Under **ADR 0017**, the *subscribe* path and the
*ask* path are membership-aware: a verified member of a private project's Slack
channel **can** follow it and **can** get answers about it in a DM. But the
*advertise* path is not — it lists open-tier only, so a private-channel member
is never told they can follow the private project they already have access to.

The fix makes the advertised set equal the subscribe-accepted set, which already
equals the ask-answerable set.

### Why the lobby is out of scope

The lobby's discovery list (`lobby.discovery_reply`) posts **one message to a
shared channel**, seen by everyone in the lobby. It cannot be filtered
per-viewer, and it has no `user_id`. Private projects therefore stay hidden in
the lobby, full stop (ADR 0007 "points-don't-reveal"). Discoverability of a
private project is possible **only in a DM, and only to a channel member** — the
single surface where Babbla knows who is asking.

## Change

Add one orchestrator helper that computes the followable names for a user by
reusing the existing per-binding authorization decision:

```python
async def _followable_for(self, user_id: str) -> list[str]:
    """Names the user may follow: open-tier always, private only when the
    caller is a verified channel member. Single source of truth = the same
    authorize_personal decision the subscribe/ask paths use, so the advertised
    set always matches what `follow` will accept."""
    decisions = await asyncio.gather(
        *(self._authorize_personal(user_id, b) for b in self._config.bindings)
    )
    return [b.name for b, d in zip(self._config.bindings, decisions) if d.allowed]
```

- **Single source of truth.** Delegating to `_authorize_personal` guarantees the
  advertised list matches the subscribe gate exactly. If the predicate evolves,
  advertising follows automatically.
- **Cost model preserved (ADR 0017).** `_authorize_personal` short-circuits
  open-tier **before** any Slack call; only private bindings trigger a membership
  lookup. `asyncio.gather` runs the private lookups concurrently so the
  user-facing onboarding reply stays fast.

### Call sites (all three already have `user_id` in scope)

1. Onboarding gate (`handle_ask`): replace
   `[b.name ... if is_open_tier(b)]` with `await self._followable_for(user_id)`.
2. `_dispatch_command` topic-add unknown-project branch: same.
3. `_dispatch_command` single-subscribe unknown-project branch: same.

The `personal.py` render functions (`render_no_subscriptions`,
`render_unknown_project`) are **unchanged** — they already take a name list.

## Properties

- **No leak.** A non-member never produces a positive membership check, so
  private names never reach them. ADR 0007 holds.
- **Fail-closed.** A membership-lookup failure resolves to "not a member"
  (`deny_membership` default), so the project is omitted, never wrongly shown.
- **Lobby untouched.** `discovery_reply`, `render_list`, and the digest paths are
  not modified.
- **Refines ADR 0017** (extends membership-awareness from subscribe/ask to
  advertising). Recorded as an amendment to ADR 0017 — no new ADR.

## Testing

- Onboarding gate, member of a private project → private name appears in the
  followable list alongside open-tier; `follow <private>` then succeeds.
- Onboarding gate, non-member → private name **absent**; only open-tier shown.
- Membership lookup raises / returns False → private project omitted (fail-closed).
- Open-tier-only config → no membership calls made (cost-model guard).
- Unknown-project hint (subscribe + topic-add), member vs non-member → same
  membership-aware listing.
- Lobby `discovery_reply` unchanged (regression).

## Out of scope

- Lobby / channel discovery surfaces.
- Storage schema, new visibility tier, the `authorize_personal` predicate itself.
