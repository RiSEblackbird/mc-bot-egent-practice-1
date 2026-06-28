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

require_absent() {
  [[ ! -e "$1" ]] || fail "forbidden path exists: $1"
}

require_grep() {
  local pattern="$1"
  local file="$2"
  local message="$3"
  grep -q "$pattern" "$file" || fail "$message"
}

require_file "AGENTS.md"
require_file "CLAUDE.md"
require_file ".agents/skills/ui-ux-review/SKILL.md"
require_file "docs/agent-principles.md"
require_file "docs/documentation-structure.md"
require_file "docs/process/task-execution.md"
require_file "docs/security-publication-checklist.md"

require_file "docs/ai-governance/00-index.md"
require_file "docs/ai-governance/glossary.md"
require_file "docs/ai-governance/01-agent-operating-contract.md"
require_file "docs/ai-governance/02-uiux-review-framework.md"
require_file "docs/ai-governance/03-evidence-and-completion-gates.md"
require_file "docs/ai-governance/04-cognitive-psychology-principles.md"
require_file "docs/ai-governance/05-accessibility-and-inclusive-design.md"
require_file "docs/ai-governance/06-visual-hierarchy-and-information-architecture.md"
require_file "docs/ai-governance/07-ui-copy-and-microcopy.md"
require_file "docs/ai-governance/08-state-design-and-error-recovery.md"
require_file "docs/ai-governance/09-ai-agent-review-protocol.md"
require_file "docs/ai-governance/10-utility-user-goal-and-product-fit.md"
require_file "docs/ai-governance/11-efficiency-and-expert-use.md"
require_file "docs/ai-governance/12-satisfaction-trust-and-emotional-ux.md"
require_file "docs/ai-governance/13-maintenance-policy.md"
require_absent "docs/ai-governance/10-maintenance-policy.md"

require_file "docs/ai-governance/templates/uiux-review-report.md"
require_file "docs/ai-governance/templates/state-matrix.md"
require_file "docs/ai-governance/templates/novice-simulation.md"
require_file "docs/ai-governance/templates/counter-review.md"
require_file "docs/ai-governance/templates/completion-gate-report.md"
require_file "docs/ai-governance/templates/agent-task-prompt.md"
require_file "docs/ai-governance/templates/user-goal-assessment.md"
require_file "docs/ai-governance/templates/efficiency-review.md"
require_file "docs/ai-governance/templates/trust-satisfaction-review.md"
require_file "docs/ai-governance/checklists/p0-p1-p2.md"
require_file "docs/ai-governance/checklists/accessibility.md"
require_file "docs/ai-governance/checklists/cognitive-walkthrough.md"
require_file "docs/ai-governance/checklists/visual-hierarchy.md"
require_file "docs/ai-governance/checklists/content-stress.md"
require_file "docs/ai-governance/checklists/utility-user-goal.md"
require_file "docs/ai-governance/checklists/efficiency.md"
require_file "docs/ai-governance/checklists/satisfaction-trust.md"
require_file "docs/ai-governance/references/canonical-sources.md"

require_file ".github/pull_request_template.md"
require_file ".github/ISSUE_TEMPLATE/bug.md"
require_file ".github/ISSUE_TEMPLATE/feature.md"
require_file ".github/ISSUE_TEMPLATE/investigation.md"
require_file ".github/ISSUE_TEMPLATE/operations.md"

CLAUDE_CONTENT="$(tr -d '\r' < CLAUDE.md | sed '/^[[:space:]]*$/d')"
[[ "$CLAUDE_CONTENT" == "@AGENTS.md" ]] || fail "CLAUDE.md must contain only @AGENTS.md"

require_absent ".cursor"
require_absent ".cursorrules"

require_grep "ユーザー価値" "AGENTS.md" "AGENTS.md must include user value gate"
require_grep "熟練者" "AGENTS.md" "AGENTS.md must include expert efficiency gate"
require_grep "満足感" "AGENTS.md" "AGENTS.md must include satisfaction/trust gate"
require_grep "反証レビュー" "AGENTS.md" "AGENTS.md must include counter-review"
require_grep "Issue-first" "AGENTS.md" "AGENTS.md must include Issue-first rules"
require_grep "Code review result" "AGENTS.md" "AGENTS.md must include code review result gate"
require_grep "ドキュメント公開セキュリティ" "AGENTS.md" "AGENTS.md must include publication security gate"
require_grep "^---" ".agents/skills/ui-ux-review/SKILL.md" "Skill frontmatter missing"
require_grep "name: ui-ux-review" ".agents/skills/ui-ux-review/SKILL.md" "Skill name missing"
require_grep "description:" ".agents/skills/ui-ux-review/SKILL.md" "Skill description missing"
require_grep "ユーザー価値" ".agents/skills/ui-ux-review/SKILL.md" "Skill must include user value review"
require_grep "熟練者効率" ".agents/skills/ui-ux-review/SKILL.md" "Skill must include expert efficiency review"
require_grep "満足感" ".agents/skills/ui-ux-review/SKILL.md" "Skill must include satisfaction/trust review"

require_grep "^## Issue" ".github/pull_request_template.md" "PR template must include Issue section"
require_grep "Codex review" ".github/pull_request_template.md" "PR template must include code review section"
require_grep "公開安全性" ".github/ISSUE_TEMPLATE/operations.md" "Operations issue template must include publication safety"

echo "AI governance verification: PASS"
