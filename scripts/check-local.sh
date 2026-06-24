#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
export PYTHONPATH="${PYTHONPATH:-src}"

if command -v ruff >/dev/null 2>&1; then
  ruff check src tests scripts
else
  "$python_bin" -m ruff check src tests scripts
fi

"$python_bin" -m pytest
"$python_bin" -m compileall src
