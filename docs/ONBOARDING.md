# Onboarding a project to Babbla

Babbla serves *many* projects, but onboarding the Nth one is almost entirely
**config + the existing audit tool** — not feature work. This is the repeatable
procedure; "onboard project X" means "run this runbook for X". MyTV is the
worked example throughout.

> Read-only by construction (ADR 0003): every step below is config, docs, or a
> read-only GitHub call. Babbla never writes to the project repo.

## 1. Audit the repo

```bash
./audit-repo.sh <owner>/<repo>          # e.g. ./audit-repo.sh Wkkkkk/MyTV
```

Reads the repo's "why" surfaces (README, `docs/`, `docs/adr/`, PR bodies, commit
messages) and reports each as ok / thin / missing, with advisory pointers to
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md). Thin surfaces mean thinner answers
(graceful) — **never a blocker**.

```bash
./audit-repo.sh <owner>/<repo> --emit-binding   # prints a starting channels.yaml block
```

## 2. Decide visibility — `public` / `internal` / `private`

Per `access.py`: on a project's own channel, **membership *is* the access**
(always allowed). `public`/`internal` are also answerable on DM and in the Lobby.
`private` is **points-don't-reveal** everywhere except its own channel.

## 3. GitHub token access

- **public** → the existing public-repo-read fine-grained token already works.
  Nothing to change.
- **private / internal** → grant the fine-grained token read on *that specific
  repo*: **repository access** + **Contents, Metadata, Pull requests, Issues =
  Read**. Update **both** the local `GITHUB_TOKEN` env **and** the OSC-hosted
  secret (always-on). Confirm with the project team that Babbla reading the
  private repo's "why" is acceptable — it is surfaced only on that project's
  channel. This expands the documented "public-repo read-only" policy; the
  rationale is recorded in
  [ADR 0014](adr/0014-private-repo-token-access.md).

## 4. Add the binding

Edit `config/channels.yaml` (the committed file is a NULL template; real values
stay local-only):

```yaml
- name: <Name>
  owner: <owner>
  repo: <repo>
  visibility: public | internal | private
  channel_id: <C…>          # once the Slack channel exists; null = no channel yet
  dm: false                 # the single dm:true stays on the pilot project
  # optional: digest: / quiz: / stale_prs: / adr: blocks
```

Config load enforces the invariants: exactly one `dm: true`, a private+`dm:true`
warning, valid cadences/timezones.

## 5. Slack channel

Create the channel, invite `@Babbla`, and set its `channel_id` in the binding.
For a private project, channel membership *is* the access boundary.

## 6. Lobby (if configured)

If a lobby is configured, the new project **auto-joins** the discovery catalog
(`build_catalog` runs over all bindings); routing requires ≥2 projects. Private
projects are points-don't-reveal in the lobby. No lobby configured → the project
is reachable on its own channel only (auto-join is a no-op).

## 7. Verify (live)

```bash
python -m babbla.doctor          # confirms the token can read every configured repo
```

- **`babbla doctor`** → the new repo reports `ok`. A private-repo scope miss
  shows as `UNREACHABLE … not in token scope?` — go back to step 3.
- **Ask in the new channel** → a cited answer drawn from the new repo.
- **Lobby ask** that should route to it → routes (open-tier) or
  points-don't-reveal (private).
- **Optional:** configure a `digest:` block and run
  `python -m babbla.digest --once --project <Name>`.

A private-repo token-scope miss otherwise manifests as empty/failed reads at
this step — which `babbla doctor` now catches up front (and at boot, as a logged
WARNING).
