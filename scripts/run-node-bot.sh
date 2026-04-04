#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-start}"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  printf 'Node.js 22+ and npm are required.\n' >&2
  printf 'On macOS, use nvm/fnm with .nvmrc or install node@22 from Homebrew.\n' >&2
  exit 1
fi

node_major="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [[ "$node_major" -lt 22 ]]; then
  printf 'Node.js 22+ is required. Current version: %s\n' "$(node --version)" >&2
  printf 'Run nvm use, fnm use, or install node@22 before starting the bot.\n' >&2
  exit 1
fi

cd "$repo_root/node-bot"

if [[ ! -d node_modules ]]; then
  npm ci
fi

case "$mode" in
  start)
    exec npm start
    ;;
  dev)
    exec npm run dev
    ;;
  build)
    exec npm run build
    ;;
  test)
    exec npm test
    ;;
  *)
    printf 'Unknown mode: %s\n' "$mode" >&2
    printf 'Usage: bash scripts/run-node-bot.sh [start|dev|build|test]\n' >&2
    exit 1
    ;;
esac
