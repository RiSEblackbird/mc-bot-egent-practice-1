#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "required file missing: $1"
}

max_size() {
  local file="$1"
  local max_lines="$2"
  local max_bytes="$3"
  local lines bytes
  lines="$(wc -l < "$file" | tr -d ' ')"
  bytes="$(wc -c < "$file" | tr -d ' ')"
  (( lines <= max_lines )) || fail "$file exceeds ${max_lines} lines: $lines"
  (( bytes <= max_bytes )) || fail "$file exceeds ${max_bytes} bytes: $bytes"
}

require_text() {
  local file="$1"
  local pattern="$2"
  grep -Fq -- "$pattern" "$file" || fail "$file must contain: $pattern"
}

reject_text() {
  local file="$1"
  local pattern="$2"
  if grep -Fq -- "$pattern" "$file"; then
    fail "$file contains retired instruction: $pattern"
  fi
}

COMMON_FILES=(
  "AGENTS.md"
  "CLAUDE.md"
  "docs/agent-harness.md"
  "docs/agent-principles.md"
  "python/AGENTS.md"
  "python/dashboard/AGENTS.md"
  "node-bot/AGENTS.md"
  "bridge-plugin/AGENTS.md"
  "tests/AGENTS.md"
  "requirements-agent-harness.txt"
  "scripts/validate_agent_frontmatter.py"
)

NESTED_AGENTS=(
  "python/AGENTS.md"
  "python/dashboard/AGENTS.md"
  "node-bot/AGENTS.md"
  "bridge-plugin/AGENTS.md"
  "tests/AGENTS.md"
)

CANONICAL_SKILLS=(
  ".agents/skills/ui-ux-review/SKILL.md"
  ".agents/skills/github-delivery/SKILL.md"
  ".agents/skills/production-investigation/SKILL.md"
  ".agents/skills/security-publication/SKILL.md"
)

CLAUDE_RULES=(
  ".claude/rules/agent-harness.md"
  ".claude/rules/agent-governance.md"
  ".claude/rules/python.md"
  ".claude/rules/dashboard.md"
  ".claude/rules/node-bot.md"
  ".claude/rules/bridge-plugin.md"
  ".claude/rules/tests.md"
)

CLAUDE_SKILLS=(
  ".claude/skills/ui-ux-review/SKILL.md"
  ".claude/skills/github-delivery/SKILL.md"
  ".claude/skills/production-investigation/SKILL.md"
  ".claude/skills/security-publication/SKILL.md"
)

CURSOR_RULES=(
  ".cursor/rules/agent-harness.mdc"
  ".cursor/rules/agent-governance.mdc"
  ".cursor/rules/python.mdc"
  ".cursor/rules/dashboard.mdc"
  ".cursor/rules/node-bot.mdc"
  ".cursor/rules/bridge-plugin.mdc"
  ".cursor/rules/tests.mdc"
)

for file in \
  "${COMMON_FILES[@]}" \
  "${CANONICAL_SKILLS[@]}" \
  "${CLAUDE_RULES[@]}" \
  "${CLAUDE_SKILLS[@]}" \
  "${CURSOR_RULES[@]}"; do
  require_file "$file"
done

max_size "AGENTS.md" 180 16384
for file in "${NESTED_AGENTS[@]}"; do
  max_size "$file" 100 8192
  combined_bytes="$(( $(wc -c < AGENTS.md) + $(wc -c < "$file") ))"
  (( combined_bytes <= 24576 )) || fail "AGENTS.md + $file exceeds 24576 bytes: $combined_bytes"
done

for file in "${CANONICAL_SKILLS[@]}"; do
  max_size "$file" 180 16384
done
for file in "${CLAUDE_RULES[@]}" "${CLAUDE_SKILLS[@]}" "${CURSOR_RULES[@]}"; do
  max_size "$file" 30 4096
done

python3 scripts/validate_agent_frontmatter.py --self-test
python3 scripts/validate_agent_frontmatter.py \
  "${CANONICAL_SKILLS[@]}" \
  "${CLAUDE_RULES[@]}" \
  "${CLAUDE_SKILLS[@]}" \
  "${CURSOR_RULES[@]}"

CLAUDE_CONTENT="$(tr -d '\r' < CLAUDE.md | sed '/^[[:space:]]*$/d')"
[[ "$CLAUDE_CONTENT" == "@AGENTS.md" ]] || fail "CLAUDE.md must contain only @AGENTS.md"

for file in "${CLAUDE_RULES[@]}"; do
  require_text "$file" "AGENTS.md"
done
for file in "${CLAUDE_SKILLS[@]}"; do
  require_text "$file" ".agents/skills/"
  require_text "$file" "唯一の手順正本"
done

for product in "Codex" "Claude Code" "Cursor"; do
  require_text "AGENTS.md" "$product"
  require_text "docs/agent-harness.md" "$product"
  require_text "docs/ai-governance/13-maintenance-policy.md" "$product"
done

for path in \
  ".agents/skills/ui-ux-review/SKILL.md" \
  ".agents/skills/github-delivery/SKILL.md" \
  ".agents/skills/production-investigation/SKILL.md" \
  ".agents/skills/security-publication/SKILL.md" \
  "docs/agent-harness.md" \
  "python/**" \
  "python/dashboard/**" \
  "node-bot/**" \
  "bridge-plugin/**" \
  "tests/**" \
  ".github/workflows/**"; do
  require_text "AGENTS.md" "$path"
done

require_text ".claude/rules/python.md" "python/**/*"
require_text ".claude/rules/dashboard.md" "python/dashboard/**/*"
require_text ".claude/rules/node-bot.md" "node-bot/**/*"
require_text ".claude/rules/bridge-plugin.md" "bridge-plugin/**/*"
require_text ".claude/rules/tests.md" "tests/**/*"
require_text ".cursor/rules/python.mdc" 'globs: "python/**"'
require_text ".cursor/rules/dashboard.mdc" 'globs: "python/dashboard/**"'
require_text ".cursor/rules/node-bot.mdc" 'globs: "node-bot/**"'
require_text ".cursor/rules/bridge-plugin.mdc" 'globs: "bridge-plugin/**"'
require_text ".cursor/rules/tests.mdc" 'globs: "tests/**"'

for file in \
  "AGENTS.md" \
  "docs/agent-harness.md" \
  "docs/agent-principles.md" \
  "docs/ai-governance/03-evidence-and-completion-gates.md" \
  "docs/ai-governance/13-maintenance-policy.md" \
  "docs/ai-governance/15-agent-harness-compatibility.md" \
  ".github/pull_request_template.md" \
  "scripts/verify-ai-governance.sh"; do
  reject_text "$file" "コードレビュー往復は最大 10 回"
  reject_text "$file" "P0 または P1 を含まないレビュー結果が 3 回連続"
  reject_text "$file" "codex/<目的>"
  reject_text "$file" "Codex 自動コードレビュー"
  reject_text "$file" "Cursor専用ルールは作らない"
  reject_text "$file" 'require_absent ".cursor"'
done

require_text "docs/agent-harness.md" "Hard gateとheuristic"
require_text "docs/agent-harness.md" "Instruction budget"
require_text "docs/agent-harness.md" "clean review"
require_text "docs/agent-principles.md" "重複回数だけで抽象化を強制しない"
require_text "python/AGENTS.md" "Minecraft worldは停止中に変化し得る"

echo "Agent harness verification: PASS"
