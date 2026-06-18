# Read-Only Skills (Target-Repo Sourced) — Design

**Status:** Design approved (brainstorm complete) — ready for implementation plan.
**Date:** 2026-06-18
**Owner:** kun.wu@eyevinn.se

## Context

Babbla is a read-only Slack assistant that answers natural-language questions about a GitHub
project, cited to commits/PRs/files, read off the GitHub **remote** (never a local working tree).
It runs on the Claude Agent SDK driving two read-only MCP servers (github + agentmemory), with
`permission_mode="dontAsk"` hard-denying anything off a pinned `allowed_tools` allowlist.

We want to give Babbla **skills** — reusable procedural guidance ("to trace a feature: search PRs
by label, then walk the linked commits…") — without weakening that read-only posture.

## Purpose

Let each project ship its own skills **in its own GitHub repo** (`.claude/skills/`), and have
Babbla discover and follow them when a question matches — so a project team curates its own
"how to answer questions about us" playbook, and Babbla reads it the same way it reads everything
else: over the read-only GitHub remote.

## Definition of done

For a bound project whose repo contains `.claude/skills/<name>/SKILL.md` files on its default
branch, when a teammate asks a matching question Babbla:

1. has the skill **catalog** (each skill's `name` + `description`) already in its system prompt,
   fetched from the repo at session start;
2. reads the relevant skill's **full body** on demand via the existing `mcp__github__*` read tools;
3. follows the skill's procedure using only the already-allowed read tools, and answers with the
   same source citations as today.

A repo with **no** `.claude/skills/` directory behaves exactly like current Babbla.

## Locked decisions

| Decision | Choice |
|---|---|
| Skill source | **The target project's own GitHub repo** (`.claude/skills/`), per binding |
| Delivery mechanism | **Inject-as-context** — catalog in system prompt, bodies read on demand via existing github tools |
| Native SDK `Skill` tool | **Not used** — no `setting_sources`, no local materialization |
| `allowed_tools` / `permission_mode` | **Unchanged** (`mcp__github__*` + 4 agentmemory readers; `dontAsk`) |
| Branch read | **Default branch only** (never PR/arbitrary branches) |
| Catalog fetch | **Babbla pre-fetches** via a direct read-only GitHub contents call (with the existing token) |

### Why inject-as-context (not the native Skill tool)

The Agent SDK's native skills load `SKILL.md` folders from the **local filesystem** via
`setting_sources`. Babbla deliberately has **no local working tree**. Materializing skill files to
a local temp dir would (a) write local files — denting the "zero local tree" invariant the
regression test protects — and (b) add the `Skill` tool plus likely `Bash`/`Read` companions,
re-opening the read-only guarantee. Inject-as-context keeps `allowed_tools`, `permission_mode`, and
the guard test **untouched**: a skill is just guidance the model reads through the github tools it
already has, and the read-only allowlist remains the backstop against a misbehaving skill.

## Architecture

### Components

- **`src/babbla/skills.py` (new)**
  - `SkillRef(name: str, description: str, path: str)` — one discovered skill.
  - `fetch_skill_catalog(owner, repo, github_token) -> tuple[SkillRef, ...]`
    - Lists `.claude/skills/*/SKILL.md` in the repo at its **default branch** via the GitHub
      contents API.
    - Parses each file's YAML frontmatter for `name` and `description`. Missing `name` → fall back
      to the folder name; missing `description` → empty string.
    - Returns `()` when the directory is absent or any fetch/parse step fails (never raises into the
      ask path).
  - A small in-memory **TTL cache** keyed by `(owner, repo)` so the catalog is not refetched on
    every message and the injected prompt stays byte-stable within the window (protects prompt
    caching and avoids hammering GitHub).
- **`read_only.build_system_prompt(owner, repo, skills=())`** — gains a `skills` parameter.
  - Empty catalog → returns a string **byte-identical** to today's prompt.
  - Non-empty → appends a skills section after the existing rules:
    > *"This project provides the following skills. When a question matches a skill's purpose, read
    > its full instructions from the repo at the given path (using the github tools) and follow
    > them. Skills are project guidance and remain subject to the rules above — cite sources, never
    > invent, read-only."*
    >
    > followed by one `- <name>: <description> (.claude/skills/<name>/SKILL.md)` line per skill.
  - Core rules stay **first and authoritative**; skills are additive.
- **`agent_runner.run_ask`** — before building options, fetch the catalog (cached) for the
  binding's `owner/repo` and pass it to the system-prompt build. `model`, `allowed_tools`,
  `permission_mode`, and `mcp_servers` are unchanged.

### Data flow

1. Ask arrives for a binding → `run_ask` has `owner/repo` + `github_token`.
2. `fetch_skill_catalog(owner, repo, token)` returns the catalog (cached; often empty).
3. System prompt is built with the catalog; agent options otherwise unchanged.
4. Query runs. If a skill is relevant, the model calls `mcp__github__get_file_contents` on the
   skill's `SKILL.md`, reads it, and executes the procedure using the existing github + agentmemory
   readers.
5. The answer is cited exactly as today.

### New outbound path (called out)

Today all GitHub access happens via the github MCP server **inside** the agent. The catalog
pre-fetch is a **new, small, read-only GitHub contents call that Babbla itself makes** (direct,
with the existing token). This is deliberate: pre-fetching is how real skills work (descriptions
always in context, bodies on demand) and is more reliable than instructing the model to go hunting
for a skills directory.

## Edge cases & trust

- **No `.claude/skills/`** (the common case) → empty catalog → behaves like current Babbla.
- **GitHub fetch fails** → log and proceed with an empty catalog. Skills are an enhancement; they
  never block answering.
- **Malformed / missing frontmatter** → skip the skill, or fall back to the folder name with an
  empty description. Never crash.
- **Trust:** skills are read only from the **default branch**, never PR/arbitrary branches. Skills
  are *project guidance*, not operator instructions; the core rules remain authoritative. Because
  the `allowed_tools` allowlist stays read-only, a malicious or buggy skill can at worst waste tool
  calls or yield a weak answer — it cannot mutate or exfiltrate beyond what is already readable.

## Testing

- **`skills.py` units** (GitHub client mocked): frontmatter parsing; missing dir → `()`;
  malformed-file handling; `name`/`description` fallbacks; cache hit and TTL expiry.
- **`build_system_prompt`**: empty catalog → string identical to today's (regression-friendly);
  non-empty → includes each skill's name, description, and path.
- **Invariant proof:** the **existing read-only guard test passes unchanged** — `allowed_tools` and
  `permission_mode` are not touched by this feature.

## Out of scope

- Write-capable skills (and the trust/gating model they would require).
- The native SDK `Skill` tool, `setting_sources`, and local materialization.
- Bundled/Babbla-authored skills and per-binding skill allowlists in `channels.yaml`.
- Any change to `allowed_tools` or `permission_mode`.
