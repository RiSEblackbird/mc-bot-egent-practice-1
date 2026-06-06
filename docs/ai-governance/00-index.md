# AI Governance Index

このディレクトリは、AI エージェントによる UI/UX ガバナンスの詳細な source of truth である。

## 読む順序

UI/UX 作業では、まず次を読む。

1. `glossary.md`
2. `01-agent-operating-contract.md`
3. `02-uiux-review-framework.md`
4. `03-evidence-and-completion-gates.md`

必要に応じて、次の詳細文書を読む。

- `04-cognitive-psychology-principles.md`
- `05-accessibility-and-inclusive-design.md`
- `06-visual-hierarchy-and-information-architecture.md`
- `07-ui-copy-and-microcopy.md`
- `08-state-design-and-error-recovery.md`
- `09-ai-agent-review-protocol.md`
- `10-maintenance-policy.md`

## テンプレート

レビュー報告と証跡作成には `templates/` 配下のテンプレートを使う。

## チェックリスト

重点監査には `checklists/` 配下のチェックリストを使う。

## 標準参照

この枠組みを更新するときは `references/canonical-sources.md` を使う。標準、研究根拠、観測済みの欠陥分類、またはリポジトリ固有の事例に紐づかない新ルールを追加しない。

## 言語方針

このリポジトリのガバナンス本文は日本語を正式版とする。英語は、ファイル名、外部標準名、tool が認識する keyword、または業界でそのまま使う用語に限って残す。意味が分からない用語は `glossary.md` に追加し、英語本文をそのまま増やさない。
