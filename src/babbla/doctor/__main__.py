from __future__ import annotations

import argparse
import os
import sys

from babbla.config import load_config
from babbla.doctor import check_access, check_skills
from babbla.runtime import load_profiles


def main(argv: list[str] | None = None, get_json=None) -> int:
    parser = argparse.ArgumentParser(
        prog="babbla-doctor",
        description=(
            "Check that the configured GitHub token can read every configured "
            "repo, and that every bound skill is stageable from the skills pool."
        ),
    )
    parser.parse_args(argv)

    if get_json is None:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            # Guard the token before load_config so a missing token returns a
            # clean exit 2 even when the config is also absent/malformed.
            print("error: GITHUB_TOKEN is not set", file=sys.stderr)
            return 2
        # Lazy import: only build the network reader when actually needed.
        from babbla.digest.anchors import make_get_json
        get_json = make_get_json(token)

    try:
        ask, classifier = load_profiles(os.environ)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    def _tier(p):
        return (
            f"model={p.model} effort={p.effort or '(default)'} "
            f"fallback={p.fallback_model or '(none)'} "
            f"max_turns={p.max_turns or '(default)'} "
            f"max_budget_usd={p.max_budget_usd or '(default)'}"
        )

    print(f"[ok] Ask tier: {_tier(ask)}")
    print(f"[ok] Classifier tier: {_tier(classifier)}")

    config_path = os.environ.get("BABBLA_CONFIG", "config/channels.yaml")
    config = load_config(config_path)

    checks = check_access(config, get_json=get_json)
    for c in checks:
        marker = "ok" if c.reachable else "UNREACHABLE"
        print(f"[{marker}] {c.name} ({c.slug}): {c.detail}")

    # Skills are checked against the same runtime pool agent_runner stages from.
    skills_pool = os.environ.get("BABBLA_SKILLS_POOL", "config/skills")
    skill_checks = check_skills(config, skills_pool=skills_pool)
    for s in skill_checks:
        marker = "ok" if s.present else "MISSING"
        print(f"[{marker}] {s.name} skill {s.skill!r}: {s.detail}")

    ok = all(c.reachable for c in checks) and all(s.present for s in skill_checks)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
