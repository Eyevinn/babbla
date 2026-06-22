# Spec: Onboarding gate for unsubscribed DMs + multi-project follow

**Date:** 2026-06-22
**Status:** Implemented (merged to main 2026-06-22)
**Scope:** Two independent changes to the DM subscription flow.

## Context

Babbla answers DM questions by routing them to projects a user follows. Two
gaps in the current DM experience:

1. A user with **zero subscriptions** who DMs a question still gets a real Q&A
   answer (today it falls through to the default DM binding via
   `config.for_dm()`), rather than being told to follow a project first.
2. A user can only **follow one project per message**. "follow A, B and C"
   does not work — single-project is hardcoded across the classifier prompt,
   `parse_command`, `Command`, and the dispatch loop.

### Relevant code (as of this spec)

| Concern | Location |
|---|---|
| DM entry → `process_ask` | `src/babbla/slack_adapter.py` `_on_message()` |
| Routing (Check 1 command / Check 2 personal / Check 3 default) | `src/babbla/orchestrator.py` `handle_ask()` |
| Command dispatch | `src/babbla/orchestrator.py` `_dispatch_command()` |
| Intent classification (NL → command line) | `src/babbla/personal.py` `classify_intent()` |
| Command grammar parsing | `src/babbla/personal.py` `parse_command()` |
| Render helpers (`render_subscribed`, etc.) | `src/babbla/personal.py` |
| Subscription storage | `src/babbla/session_store.py` `PersonalSubStore` |

### Interaction with in-flight work (ADR 0017)

A separate effort (`docs/superpowers/specs/2026-06-22-private-personal-subscriptions-design.md`,
ADR 0017) changes **which projects are followable**: private projects become
followable when the user is a member of the project's Slack channel. To avoid
collision, this spec does **not** duplicate the open-tier check. Both the
onboarding project list and the multi-follow dispatch loop delegate to the
**same "is this project followable for this user" determination the subscribe
path already uses**. As that predicate evolves under ADR 0017, both features
follow automatically.

---

## Change 1 — Onboarding gate for unsubscribed DMs

### Behavior

Insert an **onboarding gate** in `handle_ask()` between Check 1 (command
classification) and Check 3 (default binding).

- **Ordering is preserved.** Command classification (Check 1) still runs first,
  so an unsubscribed user can still say "follow mytv" and have it work — the
  command is caught before the gate.
- **Gate fires** only when **all** hold:
  - `is_dm` is true,
  - personal subscriptions are enabled (`self._personal_store is not None`),
  - `user_id is not None`,
  - `self._personal_store.list_for(user_id)` returns empty.
- **When it fires:** return a `CitedAnswer` with the onboarding text and
  `session_id=None`. No agent run, no default-binding Q&A.
- **Back-compat:** if personal subscriptions are not configured
  (`self._personal_store is None`), behavior is unchanged.

### Consequence (intended)

With the feature enabled, the default DM binding (`config.for_dm()`) becomes
**unreachable for DM Q&A** — unsubscribed users hit the gate, subscribed users
hit Check 2. This is intended. Channel asks (Check 3 with `is_dm` false) are
unaffected. The implementation plan should confirm `for_dm()` has no other DM
consumers before treating it as dead for that path.

### Onboarding message

New render function, e.g. `personal.render_no_subscriptions(followable_names)`:

> I don't have any projects to look into for you yet — follow one first and
> I'll answer your questions about it.
>
> Projects you can follow:
> • mytv
> • babbla
> • agentic-engineering-kit
>
> Just say: `follow mytv, babbla`

- The example uses comma-separated names to teach the multi-follow syntax
  (Change 2).
- `followable_names` is derived from config bindings filtered by the **same
  followable predicate the subscribe path uses** (today `is_open_tier`; under
  ADR 0017 this may also surface member-visible private projects). Do not
  re-implement the predicate here.
- **Empty-list edge case:** if no followable projects exist, render a graceful
  variant ("There aren't any projects available to follow yet.") with no bullet
  list and no example.

---

## Change 2 — Follow / unfollow multiple projects at once

`follow A, B and C` (and the symmetric unfollow) subscribe/unsubscribe to all
named projects in one message. **Comma (`,`) is the canonical delimiter** —
project names can be multi-word (e.g. "Stream Starter") so spaces are not safe
split points, but names contain no commas. An optional space after the comma is
tolerated.

### Layered changes

1. **Classifier prompt** (`personal.py`, `classify_intent` system prompt):
   teach it to emit a **comma-delimited canonical form** —
   `subscribe mytv, babbla, agentic-engineering-kit` — and add NL examples
   like "follow A, B and C" → `subscribe A, B, C` and "unfollow X and Y" →
   `unsubscribe X, Y`. Single-project examples remain valid.

2. **`parse_command`** (`personal.py`): for `subscribe`/`unsubscribe`, take the
   argument string, split on `,`, trim each piece, drop empties → a tuple of
   names. Represent as `Command.projects: tuple[str, ...]` (a single follow
   yields a 1-tuple, preserving existing single-follow behavior). Other verbs
   (topic, digest, list) are unchanged.

3. **`_dispatch_command`** (`orchestrator.py`): loop over `cmd.projects`.
   For each name: look it up via the existing binding lookup + followable
   predicate. **Best-effort** partition into:
   - `subscribed` — valid, now added (dedupe names already subscribed),
   - `skipped_unknown` — no matching binding,
   - `skipped_private` — binding exists but not followable for this user.

   Call `_personal_store.add` (or `remove`) once per valid name. The store stays
   single-row per call; the loop lives in the orchestrator.

4. **Render** (`personal.py`): new `render_subscribed_many(subscribed, skipped)`
   and `render_unsubscribed_many(...)` that report successes and skips with
   reasons. Example:

   > ✅ Subscribed to *mytv* and *babbla*.
   > ⚠️ Skipped "Secret" (private) and "Foo" (don't know that one).

   Single-name results may continue to use the existing single-result renderers
   for a clean message, or the "many" renderer with one item — implementer's
   choice, kept consistent across follow/unfollow.

### Storage

No schema change. `PersonalSubStore.add/remove` remain single-project.

---

## Testing

### Change 1 — onboarding gate
- Unsubscribed DM **question** → onboarding text returned, **no agent run**.
- Unsubscribed DM **"follow mytv"** → still subscribes (Check 1 precedes gate).
- **Subscribed** DM question → unchanged personal-ask routing.
- **Channel** ask (`is_dm` false) → unchanged.
- `_personal_store is None` → unchanged (falls to default binding).
- Followable list empty → graceful variant, no bullets/example.

### Change 2 — multi-follow
- `parse_command("subscribe mytv, babbla, agentic-engineering-kit")` →
  `projects == ("mytv", "babbla", "agentic-engineering-kit")`.
- `parse_command("subscribe Stream Starter")` → `("Stream Starter",)`
  (multi-word single name survives).
- `classify_intent` maps "follow A, B and C" → `subscribe A, B, C`
  and "unfollow X and Y" → `unsubscribe X, Y`.
- `_dispatch_command` with a mix of valid/unknown/private →
  correct `subscribed` / `skipped_unknown` / `skipped_private` partition;
  `add` called once per valid name; already-subscribed names deduped.
- Multi-unfollow symmetric: `remove` called per valid name; unknown/not-followed
  reported.

## Out of scope

- Channel and lobby flows.
- Storage schema changes.
- The ADR 0017 membership determination itself — this spec only consumes the
  shared followable predicate, it does not define or modify it.
