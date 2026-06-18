# Babbla — Deployment Runbook

This document covers running Babbla as an always-on service: locally (no container), locally
via Docker Compose, and on [Eyevinn OSC](https://www.ovsp.se) for persistent hosting.

---

## What runs

Babbla is a single long-lived Python process (`babbla.app`). It connects to Slack over
**Socket Mode**, which means it opens an outbound WebSocket — no inbound port, no load
balancer, no TLS termination required. State (conversation history, per-thread context) is
stored in a **SQLite database** at the path set by `BABBLA_DB` (default `babbla.db` beside the
working directory; on the container it is on a named Docker volume at `/state/babbla.db`).

A separate one-shot command (`babbla.digest --once`) generates and posts the weekly digest;
it exits after sending and can be triggered by a cron job or a scheduled container run.

---

## Required env / secrets

| Variable | Required | Purpose |
|---|---|---|
| `SLACK_BOT_TOKEN` | Yes | Slack bot token (`xoxb-…`) |
| `SLACK_APP_TOKEN` | Yes | Slack app-level token (`xapp-…`, Socket Mode) |
| `GITHUB_TOKEN` | Yes | Fine-grained PAT, read-only, scoped to the target repo |
| `ANTHROPIC_API_KEY` | Optional locally / required on OSC | Claude API key; omit on a developer laptop to use a Claude subscription (Path B) |
| `BABBLA_GITHUB_MCP` | Set to `binary` in the image | Selects the bundled `github-mcp-server` binary instead of pulling via Docker |
| `AGENTMEMORY_URL` | Optional | Set to empty string (or leave unset) to disable agentmemory enrichment |

All secrets are injected at runtime — never baked into the image or committed to the
repository.

---

## GITHUB_TOKEN scopes

The token must be a **fine-grained Personal Access Token** scoped to the target repository
(or organisation) with the following **read-only** permissions:

| Scope | Needed for |
|---|---|
| `contents` (read) | Fetching files, commits, and branches |
| `metadata` (read) | Repository metadata (always required for fine-grained PATs) |
| `actions` (read) | Workflow run digests (`deploy` digest type only) |

For a plain Q&A or `branch`-type digest, `contents` + `metadata` is sufficient. Add
`actions:read` only if you configure a `deploy`-type channel in `config/channels.yaml`.

---

## Local run (no container, no API key)

This is the fastest path on a developer laptop that already has a Claude Code CLI subscription.
The subscription credential is used automatically — no `ANTHROPIC_API_KEY` needed.

```bash
# 1. Install dependencies (once)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# 2. Load secrets from .env
set -a && source .env && set +a

# 3. Start the always-on bot
.venv/bin/python -m babbla.app

# 3b. (Separate terminal) Run a one-shot digest manually
.venv/bin/python -m babbla.digest --once
```

`config/channels.yaml` maps Slack channel IDs to GitHub projects. Edit it to point at your
target repo before starting.

---

## Local container (Docker Compose)

The `docker-compose.yml` at the repo root builds the image locally (which includes the bundled
`github-mcp-server` binary) and injects secrets from `.env`:

```bash
docker compose up --build
```

The compose file mounts `./config` as read-only at `/data` (so `channels.yaml` is picked up)
and stores the SQLite database on a separate named volume (`babbla-state`) at
`/state/babbla.db`. This keeps the config mount read-only while giving SQLite a writable
location.

To tail logs: `docker compose logs -f babbla`

To stop: `docker compose down` (the `babbla-state` volume persists; add `-v` to also remove it)

---

## OSC deploy (Eyevinn Open Source Cloud)

The steps below use placeholder values; substitute your real project/service identifiers.

### 1. Build and push the image

```bash
docker build -t ghcr.io/<org>/babbla:latest .
docker push ghcr.io/<org>/babbla:latest
```

### 2. Create an always-on instance

In the OSC console (or via the OSC CLI), create an **Always-On** service instance using the
pushed image. Always-On instances restart automatically on failure — no external watchdog
needed.

### 3. Inject secrets

Configure the following environment variables in the OSC service settings (not in source
control):

```
SLACK_BOT_TOKEN=xoxb-…
SLACK_APP_TOKEN=xapp-…
GITHUB_TOKEN=github_pat_…
ANTHROPIC_API_KEY=sk-ant-…
BABBLA_GITHUB_MCP=binary
AGENTMEMORY_URL=
BABBLA_CONFIG=/data/channels.yaml
BABBLA_DB=/state/babbla.db
```

### 4. Attach persistent volumes

Attach two volumes to the service:

| Mount path | Purpose |
|---|---|
| `/data` | `channels.yaml` configuration (read-only recommended) |
| `/state` | SQLite database (`babbla.db`) — must be writable |

Upload your `config/channels.yaml` to the `/data` volume before the first start. The Slack
channel mapping must include your channel IDs (`C0XXXXXXXXX`) and GitHub repo targets.

### 5. Confirm one instance stays running

After deploy, verify in the OSC console that exactly one instance shows status **Running**.
Check the service logs for the `[APP] connected to Slack` line. Send a test mention in the
configured channel (`C0XXXXXXXXX`) and confirm a reply arrives.

---

## Auth note: subscription vs. API key

On a developer laptop, Babbla can authenticate to Claude through the **Claude Code CLI
subscription** (Path B) without any `ANTHROPIC_API_KEY`. This works because the Claude Agent
SDK falls back to the local CLI credential when no API key is present.

This credential is **laptop-bound** — it lives in the local CLI session and cannot be
transferred to a server. For OSC (or any remote host), you must inject a dedicated
`ANTHROPIC_API_KEY` as a secret. Using the subscription credential as a server secret (e.g.,
by exporting the CLI token) is a fragile interim that will break on token rotation and is not
supported.

In short: **local development → subscription (no key needed); OSC/server → inject
`ANTHROPIC_API_KEY`**.
