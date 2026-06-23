---
name: project-status
description: Produce a project status overview from GitHub Issues — purpose, recent activity, open issues grouped by milestone and label, and a recommended next action. Use when asked about project status, what to work on next, what's on the backlog, what's next, or when asked to orient on the project's current state.
---

# Project status (read-only)

Produce a structured project status snapshot as a Markdown file written into
the current working directory. Everything must be read from the GitHub repo
over the read-only MCP tools — never assume a local checkout, never write to
the subject repo.

## Steps

1. **Read the README** for the project's purpose (first meaningful paragraph).

2. **Fetch recent activity**: request up to 5 recently-closed issues and up to
   5 recently-closed pull requests. Merge them, sort by `closed_at` descending,
   keep the top 5.

3. **Fetch open issues**: retrieve all open issues (paginate if needed). Note
   each issue's milestone, labels, number, title, URL, and `created_at`.

4. **Pick the recommended next action** using this priority order:
   - An open issue labelled `bug`, `critical`, or `blocker` (oldest first among
     ties).
   - An open issue in the nearest-due milestone (oldest first among ties).
   - An open issue labelled `priority` or `high` (oldest first).
   - The oldest open issue overall.
   Write one sentence explaining the choice.

5. **Write ONE file `project-status.md`** into the current working directory
   with these four sections:

   ```
   ## Purpose
   <1–2 sentences from the README>

   ## Recent activity
   <last 5 closed issues/PRs as a bullet list: [title](url) — Issue/PR #N, closed YYYY-MM-DD>

   ## Open issues
   <grouped by milestone (nearest due date first), then by label (bug > priority/critical/high > other > unlabelled)>
   <each entry: - [title](url) #N — age — labels>

   ## Recommended next action
   **[title](url) #N** — <one sentence of reasoning>
   ```

   If a section's source is absent (no milestones, no labels, no open issues,
   no recently closed items), say so explicitly in that section rather than
   omitting it.

6. **Reply** with a 2–3 sentence summary: what's actively in progress, how many
   issues are open, and what the recommended next action is. Do not paste the
   Markdown into the reply.
