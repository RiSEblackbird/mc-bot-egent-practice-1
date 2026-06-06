#!/usr/bin/env sh
set -eu

fail=0
check_file() {
  if [ ! -f "$1" ]; then
    echo "MISSING: $1"
    fail=1
  else
    echo "OK: $1"
  fi
}

check_absent() {
  if [ -e "$1" ]; then
    echo "SHOULD_NOT_EXIST: $1"
    fail=1
  else
    echo "OK absent: $1"
  fi
}

check_file AGENTS.md
check_file CLAUDE.md
check_file .agents/skills/ui-ux-review/SKILL.md
check_file docs/agent-principles.md
check_file docs/ai-governance/00-index.md
check_file docs/ai-governance/glossary.md
check_file docs/ai-governance/01-agent-operating-contract.md
check_file docs/ai-governance/02-uiux-review-framework.md
check_file docs/ai-governance/03-evidence-and-completion-gates.md
check_file docs/ai-governance/04-cognitive-psychology-principles.md
check_file docs/ai-governance/05-accessibility-and-inclusive-design.md
check_file docs/ai-governance/06-visual-hierarchy-and-information-architecture.md
check_file docs/ai-governance/07-ui-copy-and-microcopy.md
check_file docs/ai-governance/08-state-design-and-error-recovery.md
check_file docs/ai-governance/09-ai-agent-review-protocol.md
check_file docs/ai-governance/10-maintenance-policy.md
check_file docs/ai-governance/checklists/p0-p1-p2.md
check_file docs/ai-governance/checklists/accessibility.md
check_file docs/ai-governance/checklists/cognitive-walkthrough.md
check_file docs/ai-governance/checklists/visual-hierarchy.md
check_file docs/ai-governance/checklists/content-stress.md
check_file docs/ai-governance/templates/uiux-review-report.md
check_file docs/ai-governance/templates/state-matrix.md
check_file docs/ai-governance/templates/novice-simulation.md
check_file docs/ai-governance/templates/counter-review.md
check_file docs/ai-governance/templates/completion-gate-report.md
check_file docs/ai-governance/templates/agent-task-prompt.md
check_file docs/ai-governance/references/canonical-sources.md

check_absent .cursor/rules
check_absent .cursorrules

if ! grep -q '^@AGENTS.md$' CLAUDE.md; then
  echo "INVALID: CLAUDE.md must contain @AGENTS.md"
  fail=1
else
  echo "OK: CLAUDE.md imports AGENTS.md"
fi

if ! grep -q '^name: ui-ux-review' .agents/skills/ui-ux-review/SKILL.md; then
  echo "INVALID: skill name missing"
  fail=1
else
  echo "OK: skill name"
fi

if ! grep -q '^description:' .agents/skills/ui-ux-review/SKILL.md; then
  echo "INVALID: skill description missing"
  fail=1
else
  echo "OK: skill description"
fi

if [ "$fail" -ne 0 ]; then
  echo "AI governance verification: FAIL"
  exit 1
fi

echo "AI governance verification: PASS"
