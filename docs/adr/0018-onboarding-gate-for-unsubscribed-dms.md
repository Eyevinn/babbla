# ADR 0018: Unsubscribed DM askers are redirected to follow a project, not answered

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Kun Wu

## Context

Personal subscriptions ([ADR 0017](0017-private-personal-subscriptions-on-membership.md))
let a user follow projects and have their DM questions routed to them. The DM
ask path in `orchestrator.py handle_ask()` had three checks in order:

1. **Command classification** — a free-text management request ("follow MyTV",
   "make my digest daily") is dispatched as a command, never reaching the Q&A
   agent.
2. **Personal ask** — if the user follows ≥1 project, route the question to the
   right subscription.
3. **Default binding** — fall back to `config.for_dm()` and answer.

The gap: a user who follows **nothing** skipped check 2 (empty) and fell through
to check 3 — getting a real Q&A answer against the default DM project. That is the
wrong first experience. The product framing is "follow the projects you care
about, then ask about them"; an unsubscribed user should be *onboarded* (told to
follow something first), not silently answered against an arbitrary default.

Separately, following was single-project-per-message: the classifier prompt,
`parse_command`, `Command`, and the dispatch loop all assumed one name.

## Decision

**Insert an onboarding gate in `handle_ask()` between command classification
(check 1) and the default binding (check 3).** When a DM user follows nothing,
return an onboarding redirect — a prompt plus a bulleted list of followable
projects — with **no agent run** (`session_id=None`).

- **Ordering is preserved.** Command classification still runs first, so an
  unsubscribed user can say "follow MyTV" and it works — the command is caught
  before the gate. The gate only intercepts *questions*, not management commands.
- **The gate fires only when all hold:** `is_dm`, personal subscriptions are
  enabled (`_personal_store is not None`), `user_id is not None`, and
  `list_for(user_id)` is empty.
- **The default DM binding (`config.for_dm()`) becomes unreachable for DM Q&A
  when personal subscriptions are enabled** — unsubscribed users hit the gate,
  subscribed users hit the personal-ask path. This is **intended**, not a
  regression. `for_dm()` remains the back-compat path only when
  `_personal_store is None` (personal subscriptions not configured), where
  behavior is unchanged. Channel asks (`is_dm` false) are unaffected.
- **The followable list reuses the existing predicate** — open-tier projects
  (`is_open_tier`), the same list the subscribe path advertises with. Private
  projects are **not named** (preserving 0007 / 0017 "points-don't-reveal"). If
  no followable projects exist, a graceful variant is shown with no list and no
  example.
- **The redirect teaches the multi-follow syntax** — its example
  (`follow A, B`) uses comma-separated names, doubling as documentation for the
  companion change below.

**Follow / unfollow now accept multiple comma-delimited projects in one
message** ("follow A, B and C"). Comma is the delimiter because project names may
be multi-word but never contain commas. Handling is **best-effort**: valid names
are subscribed, the rest reported as skipped with reasons (unknown / private),
symmetric for unfollow. A single name yields the existing single-result message
(channel pointer, advertise list); multiple names use a combined success/skip
message.

Mechanically: `Command.projects` is a computed property splitting `arg` on
commas (the parser is untouched — a 1-tuple for a single name); the orchestrator
dispatch loops over `cmd.projects`, partitioning via the same per-name binding
lookup + `_authorize_personal` followable check the single-follow path uses, so
ADR 0017's membership gating applies per name automatically.

## Consequences

- **Unsubscribed DM askers get an onboarding redirect, not an answer.** The
  first DM from a new user steers them to follow a project — the intended entry
  into personal subscriptions.
- **`for_dm()` is dead for DM Q&A whenever personal subscriptions are enabled.**
  No code was removed: it is retained for the `_personal_store is None`
  back-compat path and for surface resolution. A future cleanup could drop the
  DM case if personal subscriptions become mandatory, but that is out of scope.
- **No private-name leakage:** the onboarding list and single-unknown advertise
  list both filter to open-tier; a private project is named in a multi-follow
  skip message only because the user typed it themselves (already known to them
  from being in its channel).
- **Multi-follow is best-effort, not all-or-nothing:** a message naming a mix of
  valid, unknown, and private projects subscribes the valid ones and reports the
  rest, rather than failing the whole request.
- **No storage change.** `PersonalSubStore.add/remove` stay single-project; the
  loop lives in the orchestrator.

## Links

- Builds on: [0017](0017-private-personal-subscriptions-on-membership.md) —
  membership-gated private personal subscriptions (the per-name followable check
  this gate and the multi-follow loop delegate to)
- Refines: [0007](0007-access-visibility-redaction.md) — "points-don't-reveal"
  is preserved by the open-tier-only followable list
- Related: [0013](0013-thread-scoped-conversation-sessions.md) — the onboarding
  redirect returns `session_id=None` (no thread session created)
