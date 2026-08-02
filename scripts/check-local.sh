#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON:-python3}"
export PYTHONPATH="${PYTHONPATH:-src}"

completed_steps=()

write_summary() {
  local status="$1"
  if [ -z "${GITHUB_STEP_SUMMARY:-}" ]; then
    return 0
  fi

  {
    echo "### Repository closure suite"
    echo
    echo "- Status: $status"
    echo "- Command: \`scripts/check-local.sh\`"
    echo "- Steps:"
    for step in "${completed_steps[@]}"; do
      echo "  - $step"
    done
  } >>"$GITHUB_STEP_SUMMARY"
}

finish() {
  local status="$?"
  trap - EXIT
  if [ "$status" -eq 0 ]; then
    write_summary "passed"
  else
    write_summary "failed"
  fi
  exit "$status"
}
trap finish EXIT

run_step() {
  local label="$1"
  shift

  echo "==> $label"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::group::$label"
  fi

  set +e
  "$@"
  local status="$?"
  set -e

  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::endgroup::"
  fi

  if [ "$status" -ne 0 ]; then
    echo "FAIL: $label" >&2
    return "$status"
  fi

  completed_steps+=("$label")
}

if command -v ruff >/dev/null 2>&1; then
  ruff_cmd=(ruff check src tests scripts)
else
  ruff_cmd=("$python_bin" -m ruff check src tests scripts)
fi

run_step "ruff" "${ruff_cmd[@]}"
run_step "shell syntax" bash -n scripts/*.sh
run_step "pytest" "$python_bin" -m pytest -q
