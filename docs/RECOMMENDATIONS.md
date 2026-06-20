# Getting the most out of Babbla

**Status:** Advisory — recommendations, not requirements
**Audience:** Teams whose projects Babbla reads and reports on

Babbla answers questions about your project — "what changed", "why is this code
here", "what's live in prod" — by reading what your repo already contains. It
requires **no changes** to your project: no new files, no mandated artifacts, no
per-developer setup. Everything below is a way to get *better* answers, never a
condition for getting answers at all.

> **Graceful degradation.** Sparse docs produce thinner answers, never failure.
> Babbla reads whatever is there. If the "why" behind a change isn't written down
> anywhere, Babbla can describe *what* changed (from the diff) but not *why* — so
> the recommendations here are really "write down the why where it already
> belongs." See [`adr/0009-repo-is-source-of-truth-for-why.md`](adr/0009-repo-is-source-of-truth-for-why.md).

## How Babbla finds the "why"

Babbla reads your project over a **read-only** GitHub path and draws on the
surfaces you already maintain, roughly in this order of usefulness:

1. **Commit messages** — the closest record to each change.
2. **PR bodies** — the rationale and discussion around a set of commits.
3. **`docs/adr/`** — the durable record of notable decisions.
4. **`README`, `CLAUDE.md`, `docs/`** — the standing description of what the
   project is and how it works.
5. **Issues** — the problem context a change responds to.

The better these read, the better Babbla answers. None is mandatory.

## Recommendations

### 1. Write descriptive PR bodies

A one-line PR title tells Babbla *what*; a PR body tells it *why*. When you open a
PR, say what problem it solves, what alternatives you considered, and anything
non-obvious about the approach. This is the single highest-leverage habit — PRs
are where the "why" most naturally lives, and Babbla reads them directly.

### 2. Keep `README` / `CLAUDE.md` / `docs/` current

These are the project's standing description. When behaviour or architecture
changes meaningfully, update them in the same PR. Stale top-level docs mislead
Babbla the same way they mislead a new joiner.

### 3. Record notable decisions as ADRs

For decisions worth remembering — a technology choice, a trade-off, something a
future maintainer would otherwise re-litigate — add a short
[Architecture Decision Record](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
under `docs/adr/`. Babbla treats `docs/adr/` as a first-class source of "why".
(Babbla's own ADRs in [`adr/`](adr/) are a working example of the format.)

## What you do *not* need to do

- ❌ Add any Babbla-specific file or config to your repo.
- ❌ Adopt a particular memory tool, service, or account.
- ❌ Change your branching, review, or release process.
- ❌ Run anything for Babbla's benefit.

Babbla is a read-only outside observer of your normal workflow. The
recommendations above are just good documentation hygiene that happens to make a
project legible — to Babbla, to new joiners, and to your future self.

## Access & privacy

Babbla reads your repo over the existing GitHub access path, so it inherits your
repo's access control: a private repo's "why" stays private; a public repo's "why"
is public by construction. Babbla never modifies the projects it reads. See
[`adr/0003-read-only-by-construction.md`](adr/0003-read-only-by-construction.md)
and [`adr/0007-access-visibility-redaction.md`](adr/0007-access-visibility-redaction.md).
