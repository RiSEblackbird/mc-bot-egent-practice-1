# AIガバナンス文書インデックス

このdirectoryは、このリポジトリのAIエージェント支援開発で、UI/UX品質、証跡、Issue品質、完了条件を扱う詳細正本です。企業全体のAI統制、法務、倫理審査、model監査を意味しません。

エージェントルール全体の配置とCodex・Claude Code・Cursorへの接続は [`docs/agent-harness.md`](../agent-harness.md) を正本とします。

## 読み方

すべての文書を毎回読む必要はありません。

1. ルート `AGENTS.md` と変更対象に最も近い `AGENTS.md` を読む。
2. UI/UX変更では `.agents/skills/ui-ux-review/SKILL.md` を発動する。
3. Skillが対象面を分類し、`02-uiux-review-framework.md` と `03-evidence-and-completion-gates.md` を読む。
4. 変更内容に直接関係する詳細文書だけを追加で読む。
5. Issue作成、rule変更など、UI以外の目的では該当する正本だけを読む。

## 中心文書

| 文書 | 責務 |
|---|---|
| `01-agent-operating-contract.md` | UI/UX作業の基本契約と証跡の考え方 |
| `02-uiux-review-framework.md` | 対象面、品質定義、P0/P1/P2、review観点 |
| `03-evidence-and-completion-gates.md` | 対象面別の証跡と完了条件 |
| `13-maintenance-policy.md` | ガバナンスとエージェントハーネスの保守 |
| `14-issue-quality-gate.md` | Issueの理由、根拠、現在と目標、受け入れ条件 |
| `15-agent-harness-compatibility.md` | 3製品の発見、scope、fallback、instruction budget |

## 詳細文書

- `04-cognitive-psychology-principles.md`: 認知負荷、初見理解、記憶負荷
- `05-accessibility-and-inclusive-design.md`: アクセシビリティと包摂性
- `06-visual-hierarchy-and-information-architecture.md`: 視覚階層と情報設計
- `07-ui-copy-and-microcopy.md`: copy、label、error message
- `08-state-design-and-error-recovery.md`: 状態、失敗、回復
- `09-ai-agent-review-protocol.md`: AI reviewの役割分離と限界
- `10-utility-user-goal-and-product-fit.md`: ユーザー価値と目的適合
- `11-efficiency-and-expert-use.md`: 熟練者効率と反復利用
- `12-satisfaction-trust-and-emotional-ux.md`: 満足感、安心感、信頼感

## テンプレート

- `templates/uiux-review-report.md`
- `templates/state-matrix.md`
- `templates/novice-simulation.md`
- `templates/counter-review.md`
- `templates/user-goal-assessment.md`
- `templates/efficiency-review.md`
- `templates/trust-satisfaction-review.md`
- `templates/completion-gate-report.md`
- `templates/agent-task-prompt.md`

## チェックリスト

- `checklists/p0-p1-p2.md`
- `checklists/accessibility.md`
- `checklists/cognitive-walkthrough.md`
- `checklists/visual-hierarchy.md`
- `checklists/content-stress.md`
- `checklists/utility-user-goal.md`
- `checklists/efficiency.md`
- `checklists/satisfaction-trust.md`

## 原則

UIを見た目だけで評価せず、対象ユーザーが目的を達成でき、状態を理解し、失敗から回復でき、慣れれば効率よく、安心して使えることを評価します。主張は、実際の画面、test、DOM / accessibility tree、差分、手動確認などの証跡で支えます。

本文は日本語を正式版とし、file名、標準名、tool keyword、業界で一般的な用語だけ英語を残します。
