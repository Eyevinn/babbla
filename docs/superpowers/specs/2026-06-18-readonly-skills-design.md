# Read-Only Skills (Shared + Target-Repo Sourced) — Design

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

Let projects share a common, curated set of read-only skills **and** let each project ship its
own, so Babbla discovers and follows whichever matches a question — a project team curates its own
"how to answer questions about us" playbook on top of an org-wide baseline. Both are read the same
way Babbla reads everything else: over the read-only GitHub remote.

Skills come from two kinds of source repo, merged into one catalog per ask:

- a **shared skills repo** (e.g. `Eyevinn/babbla-skills`), configured once and applied to every
  project;
- the **bound project's own repo** (`.claude/skills/`).

## Definition of done

When a teammate asks a matching question about a bound project, Babbla:

1. has the skill **catalog** — the **merge** of the shared repo's skills and the bound project's
   own (`name` + `description` for each) — already in its system prompt, fetched from both source
   repos' default branches at session start;
2. reads the relevant skill's **full body** on demand via the existing `mcp__github__*` read tools,
   from whichever source repo defines it;
3. follows the skill's procedure using only the already-allowed read tools, and answers with the
   same source citations as today.

With no shared repo configured and a project repo that has no `.claude/skills/`, the catalog is
empty and Babbla behaves exactly like today.

## Locked decisions

| Decision | Choice |
|---|---|
| Skill sources | **A shared skills repo** (configured once, global) **+ the target project's own repo** (`.claude/skills/`, per binding), merged |
| Collision rule | **Project-local overrides shared** — a project skill of the same `name` wins over the shared one |
| Shared-source config | A global `shared_skills` entry in `channels.yaml` (`owner`/`repo`); version-controlled, no secrets. Omit it → shared source disabled |
| Delivery mechanism | **Inject-as-context** — catalog in system prompt, bodies read on demand via existing github tools |
| Native SDK `Skill` tool | **Not used** — no `setting_sources`, no local materialization |
| `allowed_tools` / `permission_mode` | **Unchanged** (`mcp__github__*` + 4 agentmemory readers; `dontAsk`) |
| Branch read | **Default branch only** (never PR/arbitrary branches), for every source repo |
| Catalog fetch | **Babbla pre-fetches** each source via a direct read-only GitHub contents call (with the existing token) |

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
  - `SkillRef(name: str, description: str, owner: str, repo: str, path: str)` — one discovered
    skill, carrying its **source repo** so the model knows where to read the body. `path` is the
    `SKILL.md` path within that repo.
  - `fetch_skill_catalog(owner, repo, github_token) -> tuple[SkillRef, ...]`
    - Lists `.claude/skills/*/SKILL.md` in the given repo at its **default branch** via the GitHub
      contents API.
    - Parses each file's YAML frontmatter for `name` and `description`. Missing `name` → fall back
      to the folder name; missing `description` → empty string. Stamps each ref with the source
      `owner`/`repo`/`path`.
    - Returns `()` when the directory is absent or any fetch/parse step fails (never raises into the
      ask path).
  - `merge_catalogs(shared, project) -> tuple[SkillRef, ...]` — concatenates the two, applying the
    **collision rule: a project skill of the same `name` overrides the shared one** (project entry
    kept, shared entry of that name dropped). Order: project skills first, then shared skills not
    shadowed.
  - A small in-memory **TTL cache** keyed by `(owner, repo)` so each source is not refetched on
    every message and the injected prompt stays byte-stable within the window (protects prompt
    caching and avoids hammering GitHub). The shared source benefits most — it's fetched for every
    binding.
- **`read_only.build_system_prompt(owner, repo, skills=())`** — gains a `skills` parameter (the
  already-merged catalog).
  - Empty catalog → returns a string **byte-identical** to today's prompt.
  - Non-empty → appends a skills section after the existing rules:
    > *"The following skills are available. When a question matches a skill's purpose, read its full
    > instructions from the given repo and path (using the github tools) and follow them. Skills are
    > guidance and remain subject to the rules above — cite sources, never invent, read-only."*
    >
    > followed by one `- <name>: <description> (<owner>/<repo>: .claude/skills/<name>/SKILL.md)`
    > line per skill. The repo coordinate is required because a skill may live in the shared repo or
    > the project repo.
  - Core rules stay **first and authoritative**; skills are additive.
- **`config` (`channels.yaml` + `config.py`)** — a top-level `shared_skills:` entry (`owner`,
  `repo`) parsed into the `Config` object (e.g. `Config.shared_skills: SkillSource | None`). Absent
  → no shared source.
- **`agent_runner.run_ask`** — before building options: fetch the project catalog for the binding's
  `owner/repo`, fetch the shared catalog (if `shared_skills` is configured), `merge_catalogs(...)`,
  and pass the result to the system-prompt build. Both fetches are cached. `model`, `allowed_tools`,
  `permission_mode`, and `mcp_servers` are unchanged.

### Data flow

1. Ask arrives for a binding → `run_ask` has `owner/repo` + `github_token`.
2. Fetch the **project** catalog for `owner/repo`, and (if configured) the **shared** catalog for
   `shared_skills.owner/repo` — both cached, often empty.
3. `merge_catalogs(shared, project)` → one catalog, project skills shadowing shared on name clash.
4. System prompt is built with the merged catalog; agent options otherwise unchanged.
5. Query runs. If a skill is relevant, the model calls `mcp__github__get_file_contents` on that
   skill's `SKILL.md` **in its source repo** (shared or project), reads it, and executes the
   procedure using the existing github + agentmemory readers.
6. The answer is cited exactly as today.

### New outbound path (called out)

Today all GitHub access happens via the github MCP server **inside** the agent. The catalog
pre-fetch is a **new, small, read-only GitHub contents call that Babbla itself makes** (direct,
with the existing token). This is deliberate: pre-fetching is how real skills work (descriptions
always in context, bodies on demand) and is more reliable than instructing the model to go hunting
for a skills directory.

## Edge cases & trust

- **No `shared_skills` and no project `.claude/skills/`** (the common case) → empty catalog →
  behaves like current Babbla.
- **One source fails or is unreadable** (e.g. shared repo missing, or token can't read it) → that
  source contributes `()`; the other source still applies. A failed shared fetch never blocks a
  project's own skills, and vice versa.
- **Malformed / missing frontmatter** → skip the skill, or fall back to the folder name with an
  empty description. Never crash.
- **Name collision across sources** → project-local wins (the shared skill of that name is dropped
  from the catalog). Collisions *within* one source are not expected (folder names are unique);
  if they occur, last-listed wins — not a supported configuration.
- **Token access:** the GitHub token must be able to read the shared repo (public, or same-org with
  appropriate scope). A shared repo the token can't see is treated as a failed source (above), not
  an error.
- **Trust:** every source is read only from its **default branch**, never PR/arbitrary branches.
  Skills are *guidance*, not operator instructions; the core rules remain authoritative. Because the
  `allowed_tools` allowlist stays read-only, a malicious or buggy skill — shared or project — can at
  worst waste tool calls or yield a weak answer; it cannot mutate or exfiltrate beyond what is
  already readable.

## Testing

- **`skills.py` units** (GitHub client mocked): frontmatter parsing; missing dir → `()`;
  malformed-file handling; `name`/`description` fallbacks; each `SkillRef` stamped with the correct
  source `owner`/`repo`/`path`; cache hit and TTL expiry.
- **`merge_catalogs`**: project-only, shared-only, disjoint union, and **name collision → project
  wins** (shared entry of that name dropped); ordering (project first).
- **`build_system_prompt`**: empty catalog → string identical to today's (regression-friendly);
  non-empty → includes each skill's name, description, and `owner/repo: path` coordinate.
- **`run_ask` wiring**: with `shared_skills` configured, both sources are fetched and merged; with
  it absent, only the project source is used.
- **Invariant proof:** the **existing read-only guard test passes unchanged** — `allowed_tools` and
  `permission_mode` are not touched by this feature.

## Out of scope

- Write-capable skills (and the trust/gating model they would require).
- The native SDK `Skill` tool, `setting_sources`, and local materialization.
- **Multiple** shared sources / per-binding shared-skill allowlists — a single global
  `shared_skills` repo only; a list is a cheap later extension.
- Skills bundled as local package files (rejected: breaks on-demand body reads).
- Any change to `allowed_tools` or `permission_mode`.
