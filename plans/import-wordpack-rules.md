# Task Plan: import-wordpack-rules

## Metadata
- Owner: Codex
- Branch: codex/import-wordpack-rules
- Related issue / ticket: なし
- Last updated (UTC): 2026-06-06
- Source status file（必要時）: なし

## 1. 目的 (Goal)
- `RiSEblackbird/wordpack-for-english` の最新ルールを確認し、本リポジトリへ矛盾なく導入できる汎用ルール、運用ルール、UI/UX ガバナンスを反映する。

## 2. 非目標 (Non-goals)
- WordPack 固有のアプリ仕様、ディレクトリ構成、起動コマンドを本リポジトリへ移植しない。
- 実装コード、依存関係、アプリ挙動は変更しない。
- 作業完了フローで Draft PR を作成しない。

## 3. 対象範囲 (Scope)
- 変更対象ディレクトリ / モジュール: `AGENTS.md`, `CLAUDE.md`, `.agents/skills/ui-ux-review/`, `docs/agent-principles.md`, `docs/ai-governance/`, `docs/process/task-execution.md`, `plans/TEMPLATE.md`, `plans/import-wordpack-rules.md`, `python/dashboard/AGENTS.md`, `tests/AGENTS.md`, `scripts/verify-ai-governance.sh`
- 影響を受ける契約 (API, schema, env など): なし。

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | 外部リポジトリの最新ルールを取得し、現行ルールとの差分を整理する | Done | GitHub の `main` を確認し、浅い clone で `b01e657abaf0c5a62b9aa4c31facdf02d502c14a` を取得した。 |
| M2 | 矛盾なく導入できるルールを本リポジトリのスコープへ合わせて反映する | Done | WordPack 固有のアプリ仕様・コマンドは除外し、hard gate、信頼境界、UI/UX governance、品質原則、検証スクリプトを導入した。 |
| M3 | 文書整合性を確認し、検証結果を記録する | Done | ガバナンス検証、差分チェック、末尾空白検索、参照一覧確認を実施した。 |
| M4 | WordPack 側の「ドラフトではない PR」完了条件を弱めず反映する | Done | 作業完了フローでは Draft PR を作成しない条件と、外部 publish workflow より本リポジトリルールを優先する条件を明記した。 |

## 5. 優先度付き小タスク
- [x] P0: 最新ルールの取得元と対象ファイルを確認する。
- [x] P0: ルート `AGENTS.md` を hard gate と正本定義中心へ再整理する。
- [x] P0: UI/UX ガバナンス文書、skill、検証スクリプトを導入する。
- [x] P1: 計画テンプレートとタスク実行フローを最新運用へ同期する。
- [x] P1: `python/dashboard/` と `tests/` の領域固有ルールへ関連補強を入れる。
- [x] P1: WordPack 側の「ドラフトではない PR」完了条件を本リポジトリへ弱めず導入する。
- [x] P2: `CLAUDE.md` を薄い入口として追加する。

## 6. 受け入れ条件 (Acceptance Criteria)
- [x] WordPack 側の最新 `AGENTS.md` と関連するルール入口を確認している。
- [x] 本リポジトリ固有の Python / Node bot / Bridge / Minecraft 運用と矛盾する内容を移植していない。
- [x] 導入対象のルールが重複しすぎず、既存ルールと優先順位が分かる形で `AGENTS.md` に反映されている。
- [x] WordPack 側の「作業完了時はドラフトではない PR」を弱めずに反映している。
- [x] README / docs / gitignore / AGENTS の更新要否を確認している。

## 7. 検証コマンド (Verification)
- [x] `sh scripts/verify-ai-governance.sh`（PASS）
- [x] `git diff --check`（PASS）
- [x] `rg -n "[ \t]$" ...`（対象ファイルに末尾空白なし）
- [x] `markdownlint` / `markdownlint-cli2` の有無確認（どちらも未導入のため未実行）

## 8. 基本スモークテスト
- 手順: 追加したガバナンス検証スクリプトを実行し、必須ファイル、薄い入口、Cursor rules 不在、skill metadata を確認する。
- 期待結果: `AI governance verification: PASS`
- 結果: PASS

## 9. 再開コマンド
- `git status --short`
- `sh scripts/verify-ai-governance.sh`
- `git diff --check`

## 10. 既知 Blocker
- なし

## 11. Feature Flag / Rollback（必要時のみ）
- Flag: なし
- Rollback 手順: この変更で追加・編集した Markdown / shell ファイルを元に戻す。

## 12. ステータスログ
- 2026-06-06: 作業計画を作成。外部ルール取得と差分整理から開始した。
- 2026-06-06: WordPack 最新 `main` の `b01e657abaf0c5a62b9aa4c31facdf02d502c14a` を取得し、`AGENTS.md`, `CLAUDE.md`, `.agents/skills/ui-ux-review/SKILL.md`, `docs/agent-principles.md`, `docs/ai-governance/`, `scripts/verify-ai-governance.sh` を確認した。
- 2026-06-06: WordPack 固有のアプリ仕様・コマンドを除外し、本リポジトリ向けに `AGENTS.md`、詳細原則、UI/UX governance、計画テンプレート、実行フロー、サブディレクトリ規約を更新した。
- 2026-06-06: `sh scripts/verify-ai-governance.sh` と `git diff --check` が PASS。`markdownlint` / `markdownlint-cli2` は未導入のため未実行。
- 2026-06-06: Draft PR を完了成果として扱わない条件が弱くなっていたため、`AGENTS.md` と `docs/process/task-execution.md` を WordPack 側の「作業完了時はドラフトではない PR」条件へ合わせて補正した。

## 13. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）:
- 再開条件: 追加レビューや CI 修正が必要になった場合。
- 次の最短アクション: 必要なら差分を確認して追加 commit / push し、ドラフトではない PR と CI を確認する。
