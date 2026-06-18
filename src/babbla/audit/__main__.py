from __future__ import annotations

import argparse
import os
import sys

from babbla.audit.assess import evaluate
from babbla.audit.github_reader import RepoUnreachable
from babbla.audit.report import render_binding, render_report


def _parse_slug(slug: str) -> tuple[str, str]:
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(slug)
    return parts[0], parts[1]


def main(argv: list[str] | None = None, reader=None) -> int:
    parser = argparse.ArgumentParser(prog="audit-repo", description="Audit a repo for Babbla onboarding.")
    parser.add_argument("slug", help="owner/repo, e.g. Wkkkkk/MyTV")
    parser.add_argument("--emit-binding", action="store_true", help="print only the channels.yaml block")
    parser.add_argument("--no-color", action="store_true", help="ASCII status markers")
    args = parser.parse_args(argv)

    try:
        owner, repo = _parse_slug(args.slug)
    except ValueError:
        print(f"error: expected owner/repo, got '{args.slug}'", file=sys.stderr)
        return 2

    if reader is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("error: GITHUB_TOKEN is not set", file=sys.stderr)
            return 2
        # Lazy import: only import make_reader when needed, to avoid ImportError at module load
        from babbla.audit.github_reader import make_reader
        reader = make_reader(token)

    try:
        facts = reader.fetch(owner, repo)
    except RepoUnreachable as exc:
        print(f"error: cannot read {owner}/{repo}: {exc}", file=sys.stderr)
        return 2

    report = evaluate(facts)

    if args.emit_binding:
        print(render_binding(report).rstrip("\n"))
        return report.exit_code

    color = sys.stdout.isatty() and not args.no_color
    print(render_report(report, color=color), end="")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
