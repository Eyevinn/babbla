from __future__ import annotations

import argparse
import os
import sys

from babbla.config import load_config
from babbla.doctor import check_access


def main(argv: list[str] | None = None, get_json=None) -> int:
    parser = argparse.ArgumentParser(
        prog="babbla-doctor",
        description="Check that the configured GitHub token can read every configured repo.",
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

    config_path = os.environ.get("BABBLA_CONFIG", "config/channels.yaml")
    config = load_config(config_path)

    checks = check_access(config, get_json=get_json)
    for c in checks:
        marker = "ok" if c.reachable else "UNREACHABLE"
        print(f"[{marker}] {c.name} ({c.slug}): {c.detail}")

    return 0 if all(c.reachable for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
