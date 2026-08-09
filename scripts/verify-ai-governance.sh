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

require_text() {
  local file="$1"
  local pattern="$2"
  grep -Fq -- "$pattern" "$file" || fail "$file must contain: $pattern"
}

require_line() {
  local file="$1"
  local line="$2"
  grep -Fxq -- "$line" "$file" || fail "$file must contain the exact line: $line"
}

bash scripts/verify-agent-harness.sh

REQUIRED_FILES=(
  "docs/ai-governance/00-index.md"
  "docs/ai-governance/glossary.md"
  "docs/ai-governance/01-agent-operating-contract.md"
  "docs/ai-governance/02-uiux-review-framework.md"
  "docs/ai-governance/03-evidence-and-completion-gates.md"
  "docs/ai-governance/04-cognitive-psychology-principles.md"
  "docs/ai-governance/05-accessibility-and-inclusive-design.md"
  "docs/ai-governance/06-visual-hierarchy-and-information-architecture.md"
  "docs/ai-governance/07-ui-copy-and-microcopy.md"
  "docs/ai-governance/08-state-design-and-error-recovery.md"
  "docs/ai-governance/09-ai-agent-review-protocol.md"
  "docs/ai-governance/10-utility-user-goal-and-product-fit.md"
  "docs/ai-governance/11-efficiency-and-expert-use.md"
  "docs/ai-governance/12-satisfaction-trust-and-emotional-ux.md"
  "docs/ai-governance/13-maintenance-policy.md"
  "docs/ai-governance/14-issue-quality-gate.md"
  "docs/ai-governance/15-agent-harness-compatibility.md"
  "docs/ai-governance/templates/uiux-review-report.md"
  "docs/ai-governance/templates/state-matrix.md"
  "docs/ai-governance/templates/novice-simulation.md"
  "docs/ai-governance/templates/counter-review.md"
  "docs/ai-governance/templates/completion-gate-report.md"
  "docs/ai-governance/templates/agent-task-prompt.md"
  "docs/ai-governance/templates/user-goal-assessment.md"
  "docs/ai-governance/templates/efficiency-review.md"
  "docs/ai-governance/templates/trust-satisfaction-review.md"
  "docs/ai-governance/checklists/p0-p1-p2.md"
  "docs/ai-governance/checklists/accessibility.md"
  "docs/ai-governance/checklists/cognitive-walkthrough.md"
  "docs/ai-governance/checklists/visual-hierarchy.md"
  "docs/ai-governance/checklists/content-stress.md"
  "docs/ai-governance/checklists/utility-user-goal.md"
  "docs/ai-governance/checklists/efficiency.md"
  "docs/ai-governance/checklists/satisfaction-trust.md"
  "docs/ai-governance/references/canonical-sources.md"
)

for file in "${REQUIRED_FILES[@]}"; do
  require_file "$file"
done

ISSUE_TEMPLATES=(
  ".github/ISSUE_TEMPLATE/feature.md"
  ".github/ISSUE_TEMPLATE/bug.md"
  ".github/ISSUE_TEMPLATE/investigation.md"
  ".github/ISSUE_TEMPLATE/operations.md"
)

for template in "${ISSUE_TEMPLATES[@]}"; do
  require_file "$template"
  require_line "$template" "## 現在のユーザー体験"
  require_line "$template" "## 対応後に目指すユーザー体験"
  require_text "$template" "根拠区分（該当するものを残す）: ユーザー申告 / 実ユーザー観察 / 観測事実からの推定 / 未確認の仮説"
done

require_text ".agents/skills/github-delivery/SKILL.md" "14-issue-quality-gate.md"
require_text ".agents/skills/ui-ux-review/SKILL.md" "name: ui-ux-review"
require_text ".agents/skills/ui-ux-review/SKILL.md" "アプリ本体UI"
require_text ".agents/skills/ui-ux-review/SKILL.md" "GitHub共同作業面"
require_text ".agents/skills/ui-ux-review/SKILL.md" "state matrix"
require_text "docs/ai-governance/00-index.md" "15-agent-harness-compatibility.md"
require_text "docs/ai-governance/02-uiux-review-framework.md" "P0"
require_text "docs/ai-governance/03-evidence-and-completion-gates.md" "前後screenshot"
require_text "docs/ai-governance/03-evidence-and-completion-gates.md" "latest meaningful change"
require_text "docs/ai-governance/14-issue-quality-gate.md" "体験が直接変わらないIssue"
require_text ".github/pull_request_template.md" "対象面"
require_text ".github/pull_request_template.md" "利用可能な自動・手動review"

echo "AI governance verification: PASS"
