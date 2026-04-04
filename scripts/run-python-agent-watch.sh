#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
watchfiles_bin="$repo_root/.venv/bin/watchfiles"
venv_python="$repo_root/.venv/bin/python"

if [[ ! -x "$watchfiles_bin" || ! -x "$venv_python" ]]; then
  printf 'The project virtualenv is missing. Run bash scripts/setup-python-env.sh first.\n' >&2
  exit 1
fi

export PYTHONPATH="$repo_root:$repo_root/python${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo_root"
exec "$watchfiles_bin" --filter python --ignore-paths .venv -- "$venv_python -m python"
