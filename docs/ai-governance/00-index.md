# AIガバナンス文書インデックス

このディレクトリは、AIエージェントがUI/UX品質を一貫して扱うための詳細正本です。

## 読み方

UI/UX変更では、最低限次を読んでください。

1. `AGENTS.md`
2. `docs/ai-governance/00-index.md`
3. `docs/ai-governance/glossary.md`
4. `docs/ai-governance/01-agent-operating-contract.md`
5. `docs/ai-governance/02-uiux-review-framework.md`
6. `docs/ai-governance/03-evidence-and-completion-gates.md`

変更内容に応じて、次も読んでください。

- 認知負荷、初見理解、迷いやすさ: `04-cognitive-psychology-principles.md`
- アクセシビリティ: `05-accessibility-and-inclusive-design.md`
- 視覚階層、情報設計: `06-visual-hierarchy-and-information-architecture.md`
- コピー、エラー文、ラベル: `07-ui-copy-and-microcopy.md`
- 状態、エラー回復: `08-state-design-and-error-recovery.md`
- AIエージェントのレビュー手順: `09-ai-agent-review-protocol.md`
- ユーザー価値、目的適合: `10-utility-user-goal-and-product-fit.md`
- 熟練者効率、反復利用: `11-efficiency-and-expert-use.md`
- 満足感、安心感、信頼感: `12-satisfaction-trust-and-emotional-ux.md`
- ルール変更: `13-maintenance-policy.md`

## テンプレート

- `templates/uiux-review-report.md`: UI/UXレビュー報告
- `templates/state-matrix.md`: 状態表
- `templates/novice-simulation.md`: 初見シミュレーション
- `templates/counter-review.md`: 反証レビュー
- `templates/user-goal-assessment.md`: ユーザー価値評価
- `templates/efficiency-review.md`: 熟練者効率確認
- `templates/trust-satisfaction-review.md`: 満足感・信頼感確認
- `templates/completion-gate-report.md`: 完了ゲート報告

## チェックリスト

- `checklists/p0-p1-p2.md`
- `checklists/accessibility.md`
- `checklists/cognitive-walkthrough.md`
- `checklists/visual-hierarchy.md`
- `checklists/content-stress.md`
- `checklists/utility-user-goal.md`
- `checklists/efficiency.md`
- `checklists/satisfaction-trust.md`

## 用語

- `glossary.md`: ガバナンス文書に残る英語表記と標準用語の意味

## 原則

このガバナンスは、UIを「美しいか」だけで評価しません。

次を満たすかを評価します。

- 価値があるか。
- 初見で分かるか。
- 操作できるか。
- 状態が分かるか。
- 失敗から戻れるか。
- 誰にとっても使いやすいか。
- 慣れても速いか。
- 安心して使えるか。
- 証跡で説明できるか。

## 言語方針

ガバナンス本文は日本語を正式版とします。英語は、ファイル名、外部標準名、tool が認識する keyword、または業界でそのまま使う用語に限って残します。意味が分からない用語は `glossary.md` に追加し、英語本文をそのまま増やしません。
