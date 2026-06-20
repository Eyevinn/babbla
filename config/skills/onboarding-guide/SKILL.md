---
name: onboarding-guide
description: Produce a new-contributor onboarding guide for a project as a self-contained HTML file, using only read-only GitHub access. Use when asked to onboard someone, write a getting-started or contributor guide, or explain how to set up and start working on a repo.
---

# Onboarding guide (read-only)

Produce a polished, self-contained HTML onboarding guide written into the
current working directory — a single page a new contributor could read to go
from zero to a first change.

## Steps

1. Explore the repository over the GitHub tools only (README, `CONTRIBUTING*`,
   `CLAUDE.md`/`AGENTS.md`, `docs/`, ADRs, and the manifest/build files —
   `pyproject.toml`, `package.json`, `Cargo.toml`, `Makefile`, `justfile`,
   `Dockerfile`, CI workflows). Do not attempt any write, and do not assume a
   local checkout exists.
2. Synthesize the guide's five sections, citing files/commits/PRs by their
   GitHub URLs where it helps:
   - **What this is** — one or two sentences on the project's purpose.
   - **Layout** — the top-level directories and what each holds.
   - **Set up & run** — the install/build/run commands as found in the manifest
     and docs. Quote the real commands; do not invent them.
   - **Test & checks** — the test/lint commands and any CI gates.
   - **Conventions & first change** — coding conventions (from CLAUDE.md/ADRs)
     and a concrete suggested first contribution.
3. Write ONE file `onboarding.html` into the current working directory: an
   inline `<style>` + content (no external assets, no network). Keep it
   readable — headings, short paragraphs, and code blocks for commands.
4. Reply with a 2-3 sentence summary plus the suggested first change. Do not
   paste the HTML into the reply.

If a section's source is genuinely absent (e.g. no test command anywhere), say
so in that section rather than guessing. Keep the file self-contained: all CSS
inline, no remote fonts or images.
