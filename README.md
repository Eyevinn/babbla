# Babbla

**Babbla** is a read-only Slack assistant: ask a natural-language question about a GitHub
project and get an answer **cited to commits, PRs, and files** — drawn from the GitHub remote,
never a local working tree. It is built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
driving two **read-only** MCP servers (GitHub + [agentmemory](https://github.com/rohitg00/agentmemory)).

This repository is the first pilot — a Q&A loop over a single project (the public
[MyTV](https://github.com/Wkkkkk/MyTV) repo). The design and implementation plan live under
[`docs/`](docs/superpowers).

## Why read-only matters

The agent is granted **only read tools** from two MCP servers — `github`
(`GITHUB_READ_ONLY=1`, stdio) and `agentmemory` (a four-tool reader allowlist) — and runs with
`permission_mode="dontAsk"`, which **hard-denies** anything off the allowlist (no interactive
prompts on a headless server, no `bypassPermissions`). It reads the GitHub **remote**, so a
host's uncommitted/untracked/gitignored files are structurally invisible. The enforcement is
pinned by a regression test: see [`src/concierge/read_only.py`](src/concierge/read_only.py) and
[`tests/test_read_only_guard.py`](tests/test_read_only_guard.py).

## Prerequisites

- Python 3.12+
- Docker (runs `ghcr.io/github/github-mcp-server`)
- Node / `npx` (runs the `@agentmemory/mcp` bridge)
- An agentmemory backend reachable at `AGENTMEMORY_URL` (default `http://localhost:3111`)

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

### Slack app

Create an app at <https://api.slack.com/apps>, enable **Socket Mode**, then add:

- **Bot token scopes:** `app_mentions:read`, `chat:write`, `im:history`
- **Event subscriptions:** `app_mention`, `message.im`
- Enable the **Messages tab** so DMs are delivered, then install and invite the bot to a channel.

Map the channel/DM to a project in [`config/channels.yaml`](config/channels.yaml).

## Run

```bash
set -a && source .env && set +a
.venv/bin/python -m concierge.app
```

@-mention the bot in its channel, or DM it, with a question about the project. It posts a
placeholder, then edits in a cited answer in-thread; follow-ups in the same thread continue the
conversation.

## Test

```bash
.venv/bin/pytest -m "not integration"   # fast unit suite, no tokens needed
.venv/bin/pytest -m integration -s       # live smoke test (needs Docker + GITHUB_TOKEN + Claude auth)
```

## License

[Apache-2.0](LICENSE) © Eyevinn Technology AB.

---

Built and maintained at [Eyevinn Technology](https://www.eyevinntechnology.se) — the Open Source
Software Center.
