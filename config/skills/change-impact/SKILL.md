---
name: change-impact
description: Given a proposed change described by the user, trace which files/modules it would touch and surface related ADRs and prior decisions, producing a self-contained HTML impact report. Use when asked about the impact of a change, what a change affects, or how a proposed feature/refactor ripples through the codebase.
---

# Change-impact analysis (read-only)

Produce a concise HTML impact report for a proposed change, written to the
current working directory. Everything must be read from the GitHub repo over
the read-only MCP tools — never assume a local checkout, never write to the
subject repo.

## Steps

1. **Understand the proposed change** from the user's message. If it is vague,
   state your interpretation at the top of the report rather than asking; the
   report is the answer.

2. **Explore the repository** over the GitHub tools:
   - Locate entry points and modules related to the change (search by filename,
     symbol, or keyword as needed).
   - Trace one level of callers/callees: what calls the affected code and what
     it calls in turn. Do not recurse indefinitely — two levels is enough to
     show blast radius.
   - Read `docs/adr/` (or `adr/`) for any ADRs that mention affected components,
     the design being changed, or related decisions.
   - Check `CONTEXT.md` or `CLAUDE.md` for domain terms and conventions the
     change must respect.
   - Check CI/workflow files (`.github/workflows/`) to flag any pipelines the
     change would exercise or break.

3. **Write ONE file `change-impact.html`** into the current working directory.
   Structure it as four sections:

   - **Proposed change** — one or two sentences restating your interpretation.
   - **Affected files & modules** — a table or bulleted list: file path (linked
     to GitHub), brief role, and how the change touches it (direct / indirect).
   - **Related decisions** — ADRs and design notes that bear on this change,
     each cited by its GitHub URL. Flag any that the change might violate or
     supersede.
   - **Watch-outs** — terminology conflicts, missing test coverage visible in
     the repo, CI gates that would run, and anything the change touches that
     looks load-bearing or surprising.

   Keep the file self-contained: all CSS inline, no external assets or fonts.
   Use a clean, readable layout — short paragraphs and tables over walls of text.

4. **Reply** with a 2-3 sentence summary of the blast radius and the single
   most important watch-out. Do not paste the HTML into the reply.

If the proposed change cannot be traced (e.g. the repo has no matching code),
say so clearly in the report rather than inventing results.
