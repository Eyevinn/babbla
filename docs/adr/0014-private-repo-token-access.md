# ADR 0014: GitHub token may read specific private/internal repos for onboarding

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Kun Wu

## Context

Babbla reads each project's "why" over a read-only GitHub path
([0003](0003-read-only-by-construction.md),
[0009](0009-repo-is-source-of-truth-for-why.md)). Today's documented policy is a
fine-grained token with **public-repo read-only** access — sufficient for the
MyTV pilot and Babbla itself, both public. Onboarding the Nth project
([`../ONBOARDING.md`](../ONBOARDING.md)) will eventually include a private or
internal repo, whose "why" the public-only token cannot read. The failure mode
is silent: reads return empty and answers look thin for no obvious reason.

## Decision

**The fine-grained GitHub token may be granted read access to specific private
or internal repos that are onboarded to Babbla.** For such a repo, grant
repository access plus **Contents, Metadata, Pull requests, Issues = Read** —
read scopes only, on *named* repos, never org-wide write. Both the local
`GITHUB_TOKEN` env and the OSC-hosted secret ([0011](0011-always-on-container-hosting.md))
are updated together.

Access stays **read-only by construction** ([0003](0003-read-only-by-construction.md)):
expanding *which* repos the token can read does not grant any write capability.
A private repo's "why" is surfaced only on that project's own channel, where
membership is the access boundary ([0007](0007-access-visibility-redaction.md));
elsewhere it is points-don't-reveal.

A read-access preflight (`python -m babbla.doctor`, and a boot-time WARNING)
verifies the token can read every configured repo, so a missing scope is caught
explicitly instead of as a silent empty answer.

## Consequences

- Onboarding a private/internal project is possible without a second auth system
  — one fine-grained token with the needed repos in scope.
- Each private onboarding requires a deliberate token-scope update **and** team
  confirmation that Babbla reading the repo's "why" is acceptable — friction by
  design, matching "onboarding is deliberate, one project at a time".
- **Trade-off:** the token's blast radius grows by one repo per private
  onboarding. Mitigated by read-only, named-repo scopes and the preflight; a
  token broker / per-repo credentials remains out of scope until project count
  or org boundaries demand it.
- Supersedes the implicit "public-repo read-only" framing for the token; the
  read-only *construction* (0003) is unchanged.

## Links

- Runbook: [`../ONBOARDING.md`](../ONBOARDING.md) — step 3 (GitHub token access)
- Design: [`../superpowers/specs/2026-06-20-project-onboarding-runbook-design.md`](../superpowers/specs/2026-06-20-project-onboarding-runbook-design.md)
- Related: [0003](0003-read-only-by-construction.md), [0007](0007-access-visibility-redaction.md), [0009](0009-repo-is-source-of-truth-for-why.md), [0011](0011-always-on-container-hosting.md)
