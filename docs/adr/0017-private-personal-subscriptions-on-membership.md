# ADR 0017: Personal subscriptions may cover private projects, gated on live channel membership

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Kun Wu

## Context

[ADR 0007](0007-access-visibility-redaction.md) set the access model: **access is
Slack channel membership**, with visibility tiers (public / internal / private)
layered on top. On a project's own channel, "you are in the channel" *is* the
authorization. On **non-channel surfaces** (DM, Lobby) 0007 took a blunt shortcut:
`public` + `internal` are answerable, `private` is denied outright — because those
surfaces had no per-user membership signal to consult.

Personal subscriptions inherited that shortcut. Today a private project is excluded
from personal subscriptions in two places:

- **Subscribe gate** — `orchestrator.py` refuses to follow a non-open-tier project
  (`render_private_refused`: "personal subscriptions only cover public/internal
  projects").
- **Answer gate** — even a hypothetically-subscribed private project is denied at
  answer time (`authorize_ask(binding, Surface.DM)`, the "flip-after-subscribe"
  guard).

`access.py` makes both decisions **purely from config — no Slack call.**

This is stricter than 0007's own principle. A member of a private project's channel
is *already authorized* for that project's content (0007). Yet they cannot follow it
in a DM or receive it in their personal digest — surfaces that are private *to that
one user* by construction. The gap is not a missing authorization rule; it is that
the DM/digest path never asked "is this user a member?" and defaulted to deny.

## Decision

**Extend personal subscriptions to cover private projects too — gated on a live
check that the user is a member of the project's bound private channel.** This
honors 0007's "access = Slack membership" on the personal surfaces instead of
blanket-denying them.

- **Membership becomes the gate, checked live every time** content would be
  surfaced on a personal surface:
  - at **subscribe** (you may only follow a private project you are currently in),
  - at **every DM answer** for a private subscribed project,
  - at **every personal-digest send**, per private project, per run.

  Membership is **never cached across these events.** A user removed from the
  channel stops receiving that project's content on the very next ask/digest — no
  stale window.

- **Fail closed.** Not a member, no bound channel, or a failed/timed-out Slack
  membership lookup → deny the answer (pointer to the channel) and omit the project
  from the digest. Consistent with 0007's "fail-safe gate, not best-effort."

- **A private subscription is retained when membership is lost** — the record is
  kept, but delivery is denied (DM) or skipped (digest) while the user is not a
  member. **Re-joining the channel silently restores access.** No auto-unsubscribe.

- **A private project with no bound channel stays non-subscribable** — there is
  nothing to check membership against; the user is pointed to "ask once its channel
  is set up" (existing `_pointer` behavior).

- **Discovery still does not reveal private projects.** The "I can follow: …"
  advertisement (`render_unknown_project`) continues to list **open-tier projects
  only** — a member follows their private project by naming it exactly (which they
  already know from being in its channel). This preserves 0007's
  "points-don't-reveal" for non-members.

- **PII redaction (0007) is unchanged.** Private content reaching a personal digest
  still passes the client-PII redaction gate before send. The **Lobby** is
  untouched — this decision is scoped to *personal subscriptions* (DM answers +
  personal digest), not open discovery.

Mechanically: `access.py` gains a membership-aware path. `authorize_ask` for a
private binding on the DM (and digest) surface consults a **membership oracle**
backed by Slack (`conversations.members` / `users.conversations`), rather than
returning a flat deny. The open-tier path stays **call-free** — only private
projects on a personal surface incur a lookup. This requires a Slack read scope for
private channels (`groups:read`).

## Consequences

- **Access is now membership-aware on personal surfaces**, closing the gap in 0007
  where DM/digest ignored membership and denied uniformly. 0007's blunt
  "non-channel ⇒ open-tier-only" rule is **refined**, not reversed: private is
  allowed *iff* live membership confirms it.
- **Cost:** one Slack membership lookup per private DM ask, and one per private
  project per personal-digest run. Within a single digest run a per-run membership
  snapshot is acceptable (the run is one moment in time); it must not persist across
  runs. The open-tier paths add no calls.
- **Immediate revocation:** because the check is live, a removed member loses access
  on their next interaction — no background sweep needed. Re-join restores it with
  no user action.
- **New dependency + scope:** Babbla now calls a Slack membership API and needs
  `groups:read` (private channels). A missing scope or API failure fails closed, so
  the degradation is "private content withheld," never "private content leaked."
- **No private-name leakage:** non-members still cannot discover a private project
  exists via the follow list or the Lobby; only its own channel's members can act on
  it.
- **Personal subscription records may now name private projects.** These live in the
  same written personal config as today ([0006](0006-stateful-config.md)); the
  record's *existence* is per-user and not exposed to others.

## Links

- Refines: [0007](0007-access-visibility-redaction.md) — access = Slack membership;
  visibility tiers; PII redaction
- Related: [0006](0006-stateful-config.md) (personal subscription config is written),
  [0009](0009-repo-is-source-of-truth-for-why.md) (private repo "why" is private by
  construction)
