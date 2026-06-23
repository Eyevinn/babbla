# Babbla skills pool

Vetted, **read-only** skills a project may opt into via `skills:` in
`config/channels.yaml`. This pool is Babbla-controlled and version-controlled;
the subject repo can never contribute a skill.

A skill is admissible only if **all** hold:

1. It needs no GitHub **writer** — it reads via the existing read-only MCP
   surface only.
2. It never mutates the subject repo (Babbla holds no local clone; the repo is
   reachable only over the read-only GitHub MCP).
3. Any file it writes goes to the **current working directory** (the per-request
   scratch dir). It must not write outside cwd.

Each skill is a folder `<name>/SKILL.md` (+ optional bundled resources), in the
standard Claude Code skill format. The folder name is the value used in
`skills:`.

## Pool

| Skill | What it produces |
|---|---|
| `architecture-diagram` | Self-contained HTML+SVG architecture diagram of the repo |
| `onboarding-guide` | Self-contained HTML new-contributor onboarding guide |
| `change-impact` | Self-contained HTML impact report for a proposed change — which files it touches and which ADRs it relates to |
| `project-status` | Markdown project status snapshot from GitHub Issues — open issues grouped by milestone and label, recommended next action |
