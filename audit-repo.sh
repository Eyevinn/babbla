#!/usr/bin/env bash
# Thin wrapper around the Python audit CLI. Reads GITHUB_TOKEN from the env.
# Usage: ./audit-repo.sh <owner>/<repo> [--emit-binding] [--no-color]
exec python -m babbla.audit "$@"
