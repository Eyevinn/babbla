# We built an AI Slack agent before Anthropic launched theirs. Here's what we learned.

*A comparison of Claude Tag and Babbla. Lessons from building before the frameworks caught up.*

---

## 01. What is Claude Tag, and what does it bring to enterprise teams?

Claude Tag is Anthropic's AI agent for Slack and Microsoft Teams. Instead of a separate chat window, teams @-mention `@Claude` directly in any channel.

What separates it from "ChatGPT in Slack" is the identity model. Most AI tools borrow the triggering user's permissions. Claude Tag flips this: the agent has its own account in every connected system (GitHub, Google Drive, Slack). Admins define what it can access per channel, and that scope belongs to the agent, not to any individual. That matters in async teams where no single person's credentials cover everything.

Anthropic runs Claude Tag inside their own organization. Their finding: "its value compounds with tool and context access." The more systems you connect, the more useful it becomes.

Capabilities:

- Channel-aware context. Admins configure standing instructions per channel, specifying which repositories Claude can read, which tools it can use, and what conventions the team follows.
- Memory isolation. Private channels get isolated agent identities. What Claude learns in a private channel never surfaces in a public one.
- Audit trail. Every memory write, tool call, and network request the agent makes is logged.
- Long-running and async. Because the agent has its own identity, it can schedule tasks and respond to events hours after the person who asked has logged off.
- Planned: just-in-time credential grants (single-action user approvals) and user-level permission overlays on top of the agent's base access.

The agent is treated as a first-class team member, not a borrowed tool. Revoking the agent's identity removes its access everywhere at once.

We know this because we built something in the same space before it existed.

---

## 02. How does Babbla compare?

[Babbla](https://github.com/Eyevinn/babbla) is an open-source AI Slack bot we built at Eyevinn. It connects to GitHub repositories and answers natural-language questions in Slack: "why was this module refactored?", "what changed in the last two weeks?", "walk me through onboarding." It also sends scheduled digests: weekly summaries of commits, pull requests, and notable changes, delivered to channels or directly to individual subscribers. Runs on Claude (or Codex/Copilot), self-hostable via Docker, Apache 2.0.

We built it before Claude Tag was announced. The comparison comes down to two different bets about what makes an AI agent trustworthy.

- Claude Tag's bet: give the agent its own account, its own credentials, its own place in the org chart. Security follows from who the agent *is*.
- Babbla's bet: make the agent structurally incapable of causing harm through multiple independent mechanical limits. Security follows from what the agent *cannot do*, regardless of who it is or what it's told.

Both have merit. Here's where each one pulls ahead.

**Where Babbla is stronger:**

- Defense-in-depth by construction. Read-only access is enforced through five independent layers simultaneously. Any single layer failing doesn't open a write path; no misconfiguration can accidentally widen it.
- Proactive push content. Babbla sends scheduled digests (weekly commit summaries, stale PR nudges, ADR spotlights) without anyone asking. Claude Tag is reactive; you have to @-mention it.
- Fail-closed everywhere. When Babbla can't verify access, it denies. Claude Tag recommends starting with generous access and paring back, which is practical for adoption but the opposite of Babbla's security posture.
- Personal subscriptions. Team members follow specific projects and receive personalized DM digests filtered by topic.
- Self-hostable. For teams that cannot send their codebase to a third-party cloud, Babbla on private infrastructure is the only viable option.

**Where Claude Tag is stronger:**

- Per-channel identity isolation. Private channels get their own agent identity. Information sharing is architecturally impossible, not just policy-prohibited.
- Clean revocability and self-service. Revoking a Claude Tag agent identity removes access everywhere at once. Channel owners configure it without a developer; Babbla requires editing a config file.
- Persistent memory. Claude Tag has audited, channel-scoped memory across conversations. Babbla deliberately has none.
- No infrastructure to run. For most teams, this is the deciding factor.

The gap isn't quality; it's scope. Babbla is a focused tool for making GitHub repositories conversational and delivering scheduled context to engineering teams. Claude Tag is a general-purpose AI layer across all workspace tools. A team could use both: Claude Tag for general Q&A, Babbla for proactive GitHub-specific delivery.

Knowing where each one stands makes the product question clearer: what should Babbla add, and what should it stay away from?

---

## 03. What Babbla could add, and what it should not

Building a production AI agent forces decisions about what to refuse to build, not just what to build. Some of the most important decisions in Babbla are things it explicitly doesn't do.

**Worth adding:**

- Admin-curated context per channel. A free-text field in the channel config, maintained by admins and read by the agent on every question, bridges the gap between what's in the repository and what teams decide in Slack meetings. Something like: "This team uses conventional commits. The `main` branch is always release-ready." The agent reads it but never writes to it, so nothing can go stale.
- Recent digests as question context. Babbla already generates weekly digests. Passing the last few into the agent's context when answering questions gives continuity without persistent memory. Everything it needs is already there.
- Per-project GitHub token scoping. One token currently covers all repositories. Per-project tokens would make each binding independently revocable.

**Worth leaving out:**

- Agent-written persistent memory. The agent might remember something incorrectly and repeat that mistake indefinitely. Babbla's principle is that the GitHub repository is the memory. Every answer comes from auditable, source-controlled data. Claude Tag ships this with strong identity isolation as the safety net. Babbla doesn't have that yet. Adding memory writes before solving the identity model means adding the dangerous part without the safeguard.
- Cross-channel memory sharing. Without per-channel identity isolation, enforcing that a private channel's context never surfaces in a public answer is fragile in software. This belongs after the identity model is solved, not before.
- General-purpose assistant features. Every capability added is a new attack surface and a step toward competing with Claude Tag on its home turf. Babbla's strength is depth in one domain. Staying focused is a feature, not a limitation.

Those decisions didn't come from planning. They came from shipping and finding out the hard way.

---

## 04. Lessons from building before the frameworks caught up

- The hardest problem isn't the AI; it's the permissions layer. We ended up with five independent enforcement layers not because we planned it that way, but because we kept finding new ways the agent could escape a single one. Design for the unexpected: keep the blast radius small.

- The SDK's defaults are not always safe defaults. `permission_mode="dontAsk"` denies everything not pre-approved, but only if you also set `setting_sources=[]`. Without that flag, the SDK loads local settings and silently widens what gets permitted. We caught this in production.

- "Repo as memory" is a design philosophy, not a limitation. Every answer comes fresh from GitHub, so answers can't become stale and can't leak across project boundaries. We trade coverage for accuracy, and think that's right.

- Babbla and Claude Tag solve different problems, and that's fine. One is a general-purpose AI layer across the whole workspace. The other is a focused GitHub tool with scheduled delivery and self-hosting for teams that can't send their codebase to a third-party platform. That's what open-source is for.

---

*Kun Wu · Eyevinn · June 2026*
*github.com/Eyevinn/babbla*
