#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mode="${1:-paper}"

if ! command -v docker >/dev/null 2>&1; then
  printf 'docker command is required.\n' >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose v2 is required. Use `docker compose`, not legacy `docker-compose`.\n' >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp env.dev.example .env
  printf 'Created .env from env.dev.example.\n'
fi

if grep -Eq '^OPENAI_API_KEY=(sk-xxxxxxx)?$' .env; then
  printf '.env の OPENAI_API_KEY を設定してください。\n' >&2
  printf '例: OPENAI_API_KEY=sk-...\n' >&2
  exit 1
fi

mkdir -p bridge-data bridge-plugin/build/libs

compose=(docker compose -f docker-compose.yml)
# Linux では host.docker.internal が標準では解決できないことがあるため既存 override を重ねる。
if [[ "$(uname -s)" == "Linux" ]]; then
  compose+=(-f docker-compose.host-services.yml)
fi

watch_args=()
if docker compose up --help | grep -q -- '--watch'; then
  watch_args+=(--watch)
fi

case "$mode" in
  paper)
    compose+=(-f docker-compose.paper.yml --profile paper)
    exec "${compose[@]}" up --build "${watch_args[@]}"
    ;;
  host-paper|host)
    exec "${compose[@]}" up --build "${watch_args[@]}" node-bot python-agent
    ;;
  *)
    printf 'Unknown mode: %s\n' "$mode" >&2
    printf 'Usage: bash scripts/dev-up.sh [paper|host-paper]\n' >&2
    exit 1
    ;;
esac
