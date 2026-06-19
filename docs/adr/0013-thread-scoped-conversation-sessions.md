# ADR 0013: Thread-scoped conversation sessions via Slack `thread_ts`

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Kun Wu

## Context

An Ask is rarely a single question. A person asks something, reads the cited
answer, and follows up — "why was that changed?", "show me the PR". For follow-ups
to make sense, the agent must carry the prior turn's context: the earlier question,
the answer, and the commit/PR/file evidence it already gathered.

We needed to decide what a "conversation" *is* in Slack terms, where its boundary
lies, and how continuity is implemented across separate inbound events — without
adding a conversation database to operate (consistent with [0006](0006-stateful-config.md)).

## Decision

**A Slack thread is the conversation. One thread maps to one Claude Agent SDK
session, and continuity is a genuine SDK *resume* — not a context replay.**

- The mapping is `thread_ts → session_id`, one row per thread in the SQLite
  `sessions` table (`src/babbla/session_store.py`).
- First message in a thread: no stored id, so the SDK starts a fresh session. The
  `session_id` it returns is persisted (`put_session`).
- Follow-up in the same thread: the stored id is loaded (`get_session`) and passed
  as `options.resume` (`src/babbla/agent_runner.py`). The SDK **resumes the same
  conversation**, so the model sees the full prior turn — including the tool calls
  and citations it already produced — rather than a re-injected summary.
- Sessions carry a rolling **24-hour TTL** (`SessionStore`, default 86400s),
  refreshed on each message. After 24h idle, the row is treated as expired on the
  next lookup and the follow-up silently starts a fresh session.
- A per-thread async lock (`Orchestrator._lock_for`) serializes concurrent
  messages in one thread, so two fast follow-ups cannot race the resume.

This applies uniformly across surfaces — Private Ask (DM), Shared Ask (channel
mention), Subscription, Personal, and Lobby all key continuity on `thread_ts`.

### Delivery layer (how a follow-up reaches the bot)

The session model is uniform, but the *trigger* — which Slack events the bot even
receives — differs by surface, so the user-facing follow-up gesture differs too
(`src/babbla/slack_adapter.py`):

- **DM (Private Ask):** the bot receives every message via the `message`/`im`
  handler, so a plain thread reply is delivered and answered. **No re-mention
  needed.**
- **Channel (Shared Ask / Lobby):** the bot only receives `app_mention` events; a
  plain thread reply without `@bot` is ignored. **A follow-up must `@`-mention the
  bot again** — but when it does so *inside the same thread*, `thread_ts` resolves
  to the thread root (`event.thread_ts or event.ts`), the same key as the first
  question, so the re-mention resumes the same session. The mention is the trigger,
  not a new conversation.

Consequence to note: in a channel, continuity is tied to the **thread**, not the
channel. A fresh *top-level* `@`-mention (not a reply within the bot's thread) gets
a new `thread_ts` and therefore a new session with no shared context. To continue,
reply within the thread **and** mention the bot.

## Consequences

- **The conversation boundary is exactly the Slack thread.** Replying in-thread
  continues the conversation; a new thread (or a fresh top-level @-mention) starts
  a clean session with no shared context. This matches users' mental model of
  Slack threads and needs no extra UI.
- **Continuity is real, not approximate.** Because it is an SDK resume, follow-ups
  inherit the actual prior session state, not a lossy summary — answers stay
  consistent with what was already cited.
- **State stays minimal and disposable.** Only `(thread_ts, session_id, updated_at)`
  is stored; the TTL bounds growth and means losing the store degrades gracefully
  (the next message just starts fresh). No conversation database to back up.
- **TTL is a deliberate forgetting boundary.** A day-old thread revived tomorrow
  begins anew. This is intended — stale threads rarely want stale context — but it
  is a behavior to document for users, not a bug.

## Alternatives considered

- **Replay transcript into a fresh session each turn.** Rejected: lossy, costs
  tokens re-establishing context, and risks drift from what was actually cited.
- **Channel-scoped (not thread-scoped) sessions.** Rejected: would conflate
  unrelated questions in a busy channel into one context.
- **No persistence (stateless single-shot).** Rejected: follow-ups are the common
  case; statelessness makes the assistant feel amnesiac.

## Links

- `src/babbla/session_store.py` — `SessionStore` (`thread_ts → session_id`, TTL)
- `src/babbla/agent_runner.py` — `run_ask(..., resume_session_id)` → `options.resume`
- `src/babbla/orchestrator.py` — get/put session + per-thread lock
- `src/babbla/slack_adapter.py` — `app_mention` vs `message`/`im` delivery; `thread_ts` derivation
- [0006](0006-stateful-config.md) — the narrow-runtime-state stance this fits within
