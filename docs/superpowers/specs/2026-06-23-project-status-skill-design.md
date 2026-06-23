# Project Status Skill — Design

**Status:** Spec
**Date:** 2026-06-23
**Skill name:** `project-status`
**Location:** `config/skills/project-status/SKILL.md`

---

## Problem

Babbla users often ask a project channel "what's the status?" or "what should we
work on next?" Today those questions hit the plain Ask path and get a prose answer
drawn from commits and README — there's no structured, scannable snapshot of the
open backlog and recent activity. An issue-aware status skill closes that gap.

---

## Goal

When a user asks a status, backlog, or orientation question, Babbla invokes the
`project-status` skill. The skill reads GitHub Issues and the README over the
existing read-only MCP tools and writes a single Markdown file (`project-status.md`)
to the scratch directory, which Babbla then uploads to Slack. The agent replies
with a 2–3 sentence summary plus the single recommended next action.

---

## Trigger phrases (informative — set in the skill's `description`)

Any of: "what's the status?", "what should we work on?", "what's next?",
"what's on the backlog?", "orient me on this project", "give me an overview",
"what's happening in this repo?", or close paraphrases thereof.

---

## Output: `project-status.md`

Four sections, in order:

### 1. Purpose
One or two sentences drawn from the project's README (first meaningful paragraph).

### 2. Recent activity
The last 5 closed items across issues and pull requests, sorted by closed date
descending. Each entry: title (linked to GitHub URL), type (Issue / PR), number,
and closed date.

Fetch strategy: request last 5 closed issues and last 5 closed PRs separately,
merge, sort by `closed_at` descending, keep top 5.

### 3. Open issues
All open issues, structured as:

- **Grouped by milestone** (if any milestones exist): one subsection per
  milestone, sorted by milestone due date ascending (nearest first). Issues
  within a milestone sorted by label priority then age.
- **Ungrouped issues**: grouped by their most prominent label (`bug` >
  `priority`/`critical`/`high` > other labels > unlabelled). Each issue shows:
  title (linked to GitHub URL), number, age (e.g. "3 days", "2 weeks"), and
  labels.

If there are no open issues, say so explicitly.

### 4. Recommended next action
One issue, highlighted. The agent uses judgment to pick it:

**Priority order:**
1. Open issue labelled `bug`, `critical`, or `blocker` (oldest first among ties)
2. Open issue in the nearest-due milestone (oldest first among ties)
3. Open issue labelled `priority` or `high` (oldest first)
4. Oldest open issue overall

One sentence of reasoning is required (e.g. "Recommended because it is the
oldest open bug and blocks the v1.2 milestone.").

---

## Skill steps (what the agent executes)

1. Read the README for purpose (one GitHub file fetch).
2. Fetch up to 5 recently-closed issues and up to 5 recently-closed PRs;
   merge, sort by `closed_at` desc, keep top 5.
3. Fetch all open issues (paginate if needed); note milestones and labels.
4. Pick the recommended next action using the priority order above; write one
   sentence of reasoning.
5. Write `project-status.md` to the current working directory using the four
   sections above. Use standard Markdown: `##` headings, `-` bullets, inline
   links for issue/PR titles.
6. Reply with a 2–3 sentence summary (what's actively in progress, how many
   open issues, what the recommended action is). Do not paste the Markdown into
   the reply.

---

## Constraints

- **Read-only only.** No writes to the subject repo. The scratch dir write
  (`project-status.md`) is governed by the existing ADR 0003 scratch guard.
- **No hallucination.** If a section's source is absent (e.g. no milestones,
  no labels), say so in that section rather than guessing.
- **Graceful on empty repos.** If there are no open issues, Section 3 says
  "No open issues." If there are no closed items in the last period, Section 2
  says "No recently closed issues or PRs."
- **Fits existing skill pattern.** Same structure as `architecture-diagram`,
  `onboarding-guide`, `change-impact`: read-only GitHub tools → one artifact
  file → short text reply.

---

## Output format rationale

Markdown (not HTML) because project status is pure text/structure. Markdown
renders natively in Slack's file preview and is more readable in a plain-text
fallback than an HTML blob. The other three skills use HTML only because they
produce visuals (SVG diagram, styled guide, styled report); a flat status list
needs none of that.

---

## Files changed

| File | Action |
|---|---|
| `config/skills/project-status/SKILL.md` | Create |
| `docs/superpowers/specs/2026-06-23-project-status-skill-design.md` | Create (this file) |
