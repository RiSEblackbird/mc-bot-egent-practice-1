#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="$repo_root/.venv/bin/python"

resolve_python_bin() {
  if [[ -x "$venv_python" ]]; then
    printf '%s\n' "$venv_python"
    return 0
  fi

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
  printf 'Python 3.12+ is required. Run bash scripts/setup-python-env.sh first.\n' >&2
  exit 1
fi

export PYTHONPATH="$repo_root:$repo_root/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo_root"
exec "$python_bin" -m python
