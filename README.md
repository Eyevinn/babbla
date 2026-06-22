# Babbla

**Ask any project what changed, what's live, and why — right in Slack, without interrupting a developer.**

**Babbla** is a read-only Slack assistant. Ask a natural-language question about a GitHub
project and get an answer **cited to commits, PRs, and files** — drawn from the GitHub remote,
never a local working tree. It can also send you scheduled **digests** of what changed. It is
built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) over a
read-only GitHub MCP server.

It began as a single-project Q&A pilot over the public [MyTV](https://github.com/Wkkkkk/MyTV)
repo and has since grown a lobby, scheduled digests, personal subscriptions, and read-only
skills. The pitch, design, and phased plan live under [`docs/`](docs/).

## What it does

In plain language, all inside Slack — no terminal required:

- **Ask** — pose a question, get an answer drawn from the project's history, code, and past
  decisions, cited back to the commits/PRs/files it came from.
- **Digest** — receive a scheduled summary of what changed: what was released, any incidents
  and how they were resolved, and the key decisions made. A digest can post to a team channel or
  arrive by DM as a **personal digest**, and can be narrowed to a **topic** (e.g. just security).
  Other scheduled nudges ride the same scheduler — a weekly **quiz**, stale-PR reminders, an
  ADR-of-the-week.
- **Skills** — define any vetted, read-only **skill** for a project's use case. A skill replies
  in chat and can *optionally* produce an output file that Babbla posts back into the thread —
  all without ever touching the subject repo. Seeded with `architecture-diagram` and
  `onboarding-guide`.

Real questions it answers: *"What shipped to production this week?"* · *"Why did we change X?"*
· *"Is feature Y live yet, or still in preview?"*

Follow the projects you care about and set your own digest cadence just by telling Babbla in a
DM — *"follow MyTV"*, *"follow MyTV, Stream Starter and Simulcast"*, *"what am I following?"*,
*"make my digest weekly"*. Ask a question before you follow anything and Babbla points you at the
projects you can follow first.

## Where it lives

- **The Lobby** — one open channel where anyone can ask *anything*. You don't need to know which
  project a question belongs to; Babbla routes it, answers, and points you to the right team
  channel. Good for newcomers finding their feet.
- **Project channels** — a shared space per project where the team's questions, updates, and the
  team digest live together, so everyone learns from each other's questions.
- **Private DMs** — your own questions, and a **personal digest** of the projects you follow
  (managed in plain language by DM), just for you.

## Why it's safe

- **Read-only by construction.** Babbla never mutates the repos it reads, enforced by
  independent layers so no single misconfiguration makes it writable (ADR 0003). The `github`
  MCP server runs with `GITHUB_READ_ONLY=1` over stdio — it cannot even expose a writer. The
  agent is confined to the `github` tools and **isolated from the host's Claude settings**
  (`setting_sources=[]`, `strict_mcp_config=True`) so nothing on the host can widen its tool
  surface; `permission_mode="dontAsk"` then denies anything not pre-approved (no interactive
  prompts on a headless server, never `bypassPermissions`). Independently, a `PreToolUse` hook
  **denies every non-`github` tool** (`Bash`, `Read`, `Write`, …) on the plain path; the skilled
  Ask path swaps in a hook that additionally permits writes **only** inside a throwaway,
  per-thread **scratch dir** outside every repo. Babbla reads the GitHub **remote**, so a host's
  uncommitted/untracked/gitignored files are structurally invisible. The enforcement is pinned by
  regression tests that assert the **runtime options actually sent to the CLI** (not just config)
  — see [`src/babbla/read_only.py`](src/babbla/read_only.py) and
  [`tests/test_read_only_guard.py`](tests/test_read_only_guard.py), and ADRs
  [0003](docs/adr/0003-read-only-by-construction.md) and
  [0015](docs/adr/0015-skilled-answer-path.md).
- **Respects who can see what.** Each project is `public`, `internal`, or `private`. Public
  projects answer anywhere; internal ones answer to the team; private (client) ones are
  points-don't-reveal everywhere except their own channel — where membership *is* the access.
  See [ADR 0007](docs/adr/0007-access-visibility-redaction.md).
- **Requires nothing of the projects it reads.** No Babbla-specific files, no mandated artifacts,
  no per-developer setup. Babbla is a read-only outside observer of your normal workflow.

## How it reads the "why"

The project repo is the source of truth. Babbla draws "why" from the surfaces a team already
maintains — commit messages, PR bodies, `docs/adr/`, `README`/`CLAUDE.md`/`docs/`, and issues —
over the read-only GitHub path. Sparse docs produce thinner answers, never failure (graceful
degradation). See [ADR 0009](docs/adr/0009-repo-is-source-of-truth-for-why.md) and
[`docs/RECOMMENDATIONS.md`](docs/RECOMMENDATIONS.md) for how a team gets better answers.

## Docs

| Doc | What it covers |
|---|---|
| [`PROPOSAL-pitch.md`](docs/PROPOSAL-pitch.md) | The one-page pitch — problem, idea, and what's being asked. |
| [`PROPOSAL-design.md`](docs/PROPOSAL-design.md) | The design and implementation plan. |
| [`ROADMAP.md`](docs/ROADMAP.md) | Post-pilot direction and the phased plan from foundation to multi-project. |
| [`RECOMMENDATIONS.md`](docs/RECOMMENDATIONS.md) | Advisory "getting the most out of Babbla" guide for subject teams. |
| [`ONBOARDING.md`](docs/ONBOARDING.md) | The repeatable runbook for binding the Nth project. |
| [`DEPLOY.md`](docs/DEPLOY.md) | Server hosting (Eyevinn OSC) and the headless-auth story. |
| [`adr/`](docs/adr/) | Architecture Decision Records — the durable record of "why". |

## Prerequisites

- Python 3.12+
- Docker (runs `ghcr.io/github/github-mcp-server`)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in tokens — never commit .env
```

Required environment variables (see [`.env.example`](.env.example)):

| Var | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | Slack app-level token (`xapp-…`, Socket Mode) |
| `GITHUB_TOKEN` | Fine-grained PAT, **read-only**, scoped to the target repo |
| `ANTHROPIC_API_KEY` | *Optional* — omit to use a Claude Code CLI subscription login |

### Model and effort tuning

Babbla has two tiers: the **Ask tier** (interactive Asks, scheduled digests, quiz, and ADR runs)
and the **Classifier tier** (lobby routing and personal-intent — pure label-emitters). Set
`BABBLA_MODEL` to change the shared default for both tiers; existing deployments that don't set
it are unchanged. Each tier can then be tuned independently with four optional knobs:

```
BABBLA_ASK_MODEL=...            # overrides BABBLA_MODEL for the Ask tier
BABBLA_ASK_EFFORT=high          # low|medium|high|xhigh|max
BABBLA_ASK_FALLBACK_MODEL=...
BABBLA_ASK_MAX_TURNS=8
BABBLA_ASK_MAX_BUDGET_USD=2.0

BABBLA_CLASSIFIER_MODEL=...     # overrides BABBLA_MODEL for the Classifier tier
BABBLA_CLASSIFIER_EFFORT=low
BABBLA_CLASSIFIER_FALLBACK_MODEL=
BABBLA_CLASSIFIER_MAX_TURNS=1
BABBLA_CLASSIFIER_MAX_BUDGET_USD=
```

All settings are optional and inert until set. See [`.env.example`](.env.example) for the full
commented block. Run `babbla-doctor` (or `python -m babbla.doctor`) to see the resolved tiers.

### Slack app

Create an app at <https://api.slack.com/apps>, enable **Socket Mode**, then add:

- **Bot token scopes:** `app_mentions:read`, `chat:write`, `im:history`, `im:write`,
  `channels:history`, `groups:history`, `groups:read`, and `files:write` (`files:write` lets
  Babbla post skill artifacts; the two `*:history` scopes back the channel events below;
  `groups:read` lets Babbla verify private-channel membership so private projects can be followed
  in personal subscriptions — fail-closed, see [ADR 0017](docs/adr/0017-private-personal-subscriptions-on-membership.md)).
- **Event subscriptions:** `app_mention`, `message.im`, `message.channels`, `message.groups`
  (the `message.*` events let Babbla tidy up an orphaned answer when its question is deleted —
  `message.im` for DMs, `message.channels`/`message.groups` for public/private channels).
- Enable **Interactivity** (powers the 🗑 delete button) and the **Messages tab** (so DMs are
  delivered), then install and invite the bot to a channel.

Map the channel/DM to a project in [`config/channels.yaml`](config/channels.yaml). To onboard a
project end to end, follow [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Run

```bash
set -a && source .env && set +a
.venv/bin/python -m babbla.app
```

@-mention the bot in its channel, or DM it, with a question about the project. It posts a
placeholder, then edits in a cited answer in-thread; follow-ups in the same thread continue the
conversation.

### Always-on / container

Babbla also ships as a container (Socket Mode → no inbound port; the image bundles the read-only
`github-mcp-server` binary, so there's no Docker-in-Docker). Build and run locally with your
Claude subscription (no API key needed):

```bash
docker compose up --build
```

For server hosting (Eyevinn OSC) and the headless-auth story, see
[`docs/DEPLOY.md`](docs/DEPLOY.md).

## Test

```bash
.venv/bin/pytest -m "not integration"   # fast unit suite, no tokens needed
.venv/bin/pytest -m integration -s       # live smoke test (needs Docker + GITHUB_TOKEN + Claude auth)
```

## License

[Apache-2.0](LICENSE) © Eyevinn Technology AB.

---

Built and maintained at [Eyevinn Technology](https://www.eyevinntechnology.se).
</content>
</invoke>
