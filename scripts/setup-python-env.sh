#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${VENV_DIR:-$repo_root/.venv}"

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    printf 'python3\n'
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    printf 'python\n'
    return 0
  fi

  printf 'Python 3.12+ is required but was not found in PATH.\n' >&2
  return 1
}

python_bin="$(resolve_python_bin)"

if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  printf 'Python 3.12+ is required. On macOS, install python@3.12 with Homebrew or use pyenv.\n' >&2
  exit 1
fi

"$python_bin" -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install -r "$repo_root/requirements.txt" -c "$repo_root/constraints.txt"

printf 'Python environment is ready at %s\n' "$venv_dir"
printf 'Run the agent with: bash scripts/run-python-agent.sh\n'
