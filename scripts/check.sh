#!/usr/bin/env bash
# Local checks (see docs/ARCHITECTURE.md §10). Run before every push.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f pyproject.toml ]; then
  echo "check: no Python package yet (the Contracts work adds it); nothing to check"
  exit 0
fi

uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run python scripts/check_boundaries.py
