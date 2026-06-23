# Spec: Pre-rollout behavior gate (live, against our channels)

**Date:** 2026-06-22
**Status:** Approved (brainstorming)
**Scope:** A new, opt-in test suite — run by hand right before each
deploy/restart — that exercises **all** Babbla behaviors against real backends,
with a thin true-end-to-end smoke through real Slack. It does **not** replace the
506 fast unit tests; it fills the gap they cannot: live plumbing.

## Context

| Layer | Today | Gap |
|---|---|---|
| Unit (506) | Fakes for runner/stores/membership. Exhaustive **logic** coverage. Fast. | Never touches real Slack/GitHub/Claude or the deployed image. |
| `@pytest.mark.integration` (handful) | Real GitHub/Claude, but call the orchestrator directly; a couple of smokes. | No Slack round-trip; not organized as a behavior gate. |
| Manual | `tests/manual/*` run by hand; the live DM/channel/lobby walkthrough done by a human. | Not repeatable, not asserted, easy to skip under pressure. |

The "all behaviors against our channels" check the operator does manually before
trusting a deploy has no automated form. This spec gives it one.

## Goals / non-goals

- **Goal:** one command (`make gate`) that, against real backends, asserts every
  behavior group still works, and proves the *deployed image + Slack + Path-B
  auth + GitHub MCP* are actually wired.
- **Goal:** structural assertions only — robust to Claude's non-determinism.
- **Goal:** zero risk to production channels or the public repo.
- **Non-goal:** replacing unit tests, or running on every commit/CI (it's a
  manual, occasional gate). Continuous monitoring is explicitly out of scope.

## Architecture — two tiers

A new `tests/e2e/` tree, excluded from the default run via a `gate` marker and a
required-env guard (skips cleanly when creds are absent). Invoked by
`scripts/pre-rollout-gate.sh` (wrapped as `make gate`), which runs Tier A then
Tier B and prints a single PASS/FAIL summary.

### Tier A — broad: orchestrator + real backends

Builds the **real** `Orchestrator`, `AgentRunner`, digest/action runners, and a
**real Slack membership oracle** (`make_membership` over a Slack *web* client —
web API only, so no Socket Mode conflict with prod). Drives them by calling
`handle_ask` / `handle_command` / action `maybe_run` **directly** and asserts on
the returned `CitedAnswer` / captured post payloads.

- Covers **every** behavior group (see Coverage map).
- Real GitHub + real Claude → exercises citation, routing, the GitHub MCP path.
- Real membership oracle against the **private test channel** → exercises the
  visibility×surface matrix and ADR 0017 gating for real (test user is a member;
  a second non-member id is denied).
- **Scheduled actions** are triggered by calling each runner directly with a
  forced `now`, against real GitHub, capturing the post via a recording poster —
  no waiting on cadence.
- Failure isolation: a Tier A failure means a **logic/backend** regression.

### Tier B — thin: true end-to-end via Slack

Against a **throwaway container of the about-to-ship image**, started with a
**dedicated test Slack app token** (separate from prod, so no Socket Mode event
contention), bound to the test channels. The **dedicated test user** posts via
its user token (`xoxp`); the harness polls Slack for Babbla's reply in-thread
(bounded timeout) and asserts structurally.

~6 smokes, critical plumbing paths only:
1. DM Q&A (subscribed) → reply cites a source.
2. DM onboarding (no subs) → redirect text + membership-aware followable list.
3. DM subscribe command → confirmation + store updated.
4. Channel ask → reply.
5. Lobby ask → routed reply + pointer suffix.
6. Visibility pair → private channel ask answers for the member; lobby ask about
   the same private project gives the 🔒 points-don't-reveal pointer.

Plus an **infra health check**: container up, Socket Mode connected, GitHub MCP
reachable, Path-B token valid.

- Failure isolation: a Tier B failure with Tier A green means a **plumbing /
  deploy / auth / rendering** regression.

## Test environment (operator prerequisites — one-time)

Defined in `tests/e2e/channels.test.yaml` (git-ignored; a NULL template is
committed, mirroring the prod `channels.yaml` convention):

- **Test projects/channels** in the workspace:
  - one `public` test project,
  - one `internal` test project,
  - one `private` test project whose channel has the **test user as a member**,
  - the **lobby** channel,
  each bound to a **stable public GitHub repo** for deterministic GitHub calls.
- **Dedicated test Slack user** + its user token (`xoxp`), and a **dedicated test
  Slack app** (bot token + app token) added to all test channels.
- A non-member user id (any id not in the private channel) for the deny case.
- Secrets supplied via env (`tests/e2e/.env`, git-ignored): test GitHub token,
  test Slack bot/app/user tokens, test channel ids, member/non-member ids.

The gate **skips with a clear message** if these are absent — it never falls back
to production tokens or channels.

## Coverage map

| Behavior group | Tier A | Tier B |
|---|---|---|
| DM Q&A (subscribed) | ✅ | ✅ |
| Onboarding gate + membership-aware followable list | ✅ | ✅ |
| Subscribe/unsubscribe (single, multi, dedupe, unknown, private gate) | ✅ | ✅ (1) |
| Topics (add/remove/list, needs-follow, private gate) | ✅ | — |
| Digest cadence set / list | ✅ | — |
| Channel ask | ✅ | ✅ |
| Lobby (route / sticky / no-match discovery / private points-don't-reveal) | ✅ | ✅ |
| Visibility×surface matrix + membership gating | ✅ | ✅ (pair) |
| Scheduled actions (personal + per-project digest, quiz, stale-PR, ADR) | ✅ (forced `now`) | — |
| Read-only security guard (Bash/Write denied on answer path) | ✅ | — |
| Infra (Socket Mode, GitHub MCP, Path-B token) | — | ✅ |

## Cross-cutting concerns

- **Determinism:** assert structure, not text — "has ≥1 citation", "routed to
  project X", "contains the 🔒 pointer", "store now lists Y", "post names repo
  slug Z". Never assert exact answer wording.
- **Isolation & state:** each run uses a fresh temp DB and a fresh container;
  Tier B deletes the test messages it posted on the way out (best-effort).
- **Safety:** dedicated test tokens/channels only; GitHub access stays read-only;
  the throwaway container keeps the ADR 0003 confinement (no `setting_sources`
  leak, read-only guard). The gate must be incapable of posting to a production
  channel — enforced by loading only `channels.test.yaml`.
- **Cost/latency:** real Claude + GitHub per case; the suite is small and manual,
  so a multi-minute runtime is acceptable. Tier A and the Tier B smokes run with
  generous per-case timeouts.

## Running, relative to deploy

`make gate` flow:
1. Build the new image.
2. Run **Tier A** (no container needed).
3. Start a **throwaway test-app container** of the new image; run **Tier B**
   against it; tear it down.
4. Print PASS/FAIL. On PASS the operator promotes (the normal
   `docker compose up -d --build`); on FAIL, nothing was deployed.

This keeps it a true *pre*-rollout gate — production is untouched until it passes.

## Validating the gate itself

- Tier A/B harness helpers (reply-poller, recording poster, env guard) get their
  own small unit tests with fakes, so the gate's plumbing is trusted.
- A deliberate-break dry run (e.g. point a test project at a nonexistent repo)
  confirms the gate actually fails red, not just skips.

## Out of scope

- Continuous/scheduled monitoring and alerting.
- Running in CI on every commit.
- Load/perf testing; multi-workspace/external-surface testing.
- Any change to production behavior or config.

## Appendix — Test environment setup (one-time)

### Dedicated test Slack app

A **second app in the same workspace**, mirroring prod Babbla's scopes/events but
with its own tokens, pointed only at the test channels. Create it via
<https://api.slack.com/apps> → **Create New App → From a manifest**:

```yaml
display_information:
  name: Babbla Test
features:
  bot_user:
    display_name: babbla-test
    always_online: true
oauth_config:
  scopes:
    bot:                      # mirrors prod Babbla (README.md / docs/DEPLOY.md)
      - app_mentions:read
      - chat:write
      - im:history
      - im:write
      - channels:history
      - channels:read         # conversations.members on a public test channel
      - groups:history
      - groups:read           # conversations.members on a private test channel
      - files:write
    user:
      - chat:write            # lets the TEST USER post questions as themselves
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
      - message.channels
      - message.groups
  socket_mode_enabled: true
  org_deploy_enabled: false
```

### Tokens (one app yields all three)

- **`SLACK_APP_TOKEN` (`xapp-…`)** — *Basic Information → App-Level Tokens*,
  generate with `connections:write`. A distinct app token = its own Socket Mode
  connection, so the throwaway gate container never steals events from prod.
- **`SLACK_BOT_TOKEN` (`xoxb-…`)** — *Install App → Bot User OAuth Token*.
- **Test-user token (`xoxp-…`)** — the `user: chat:write` scope makes the OAuth
  install page offer user authorization; have the **dedicated test user**
  authorize to obtain their User OAuth Token (used to post questions as the
  asker). No third app needed.

### Channels & ids

- Invite **both** the test bot and the test user to all test channels (public,
  internal, private, lobby).
- The test user **must be a member** of the private test channel (member path);
  pick any user id **not** in it for the deny path.
- Record into the git-ignored `tests/e2e/.env`: the three test tokens, the test
  channel ids, the member/non-member user ids, and a read-only test GitHub token.

**Cost:** the Slack app is free config; the only real effort is provisioning the
test user (workspace-admin) and creating the test channels.
