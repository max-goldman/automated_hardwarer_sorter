#!/usr/bin/env bash
# One-step setup for macOS and Linux: installs uv if missing, then the pinned environment.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

uv sync
echo
echo "Done. Try:  uv run sorter camera-info"
