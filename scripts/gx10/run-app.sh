#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
source "$here/desktop-env.sh"
app_dir=$(cat "$HOME/.config/daybook-gx10/app-dir")
cd -- "$app_dir"
export PATH="$HOME/.local/bin:$PATH"
exec uv run --no-sync daybook run
