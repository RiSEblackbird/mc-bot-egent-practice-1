# Task Plan: import-wordpack-rules

## Metadata
- Owner: Codex
- Branch: codex/sync-wordpack-governance-rules
- Related issue / ticket: #166
- Last updated (UTC): 2026-06-28
- Source status file（必要時）: なし

## 1. 目的 (Goal)
- `stillshore-chirp/wordpack-for-english` の最新 `main` にあるエージェント / ハーネス / ガバナンス関連ルールを確認し、本リポジトリへ適用可能なものを全て反映する。
- 競合する開発ルールは WordPack 側を正として扱い、本リポジトリ固有の技術境界に合わせて矛盾なく統合する。

## 2. 非目標 (Non-goals)
- WordPack 固有の英語学習アプリ仕様、Firebase / Cloud Run / Firestore 固有手順、フロントエンド構成をそのまま移植しない。
- Minecraft bot / Python planner / Node bot / Bridge plugin の実装挙動は変更しない。
- Draft PR を完了成果として扱わない。

## 3. 対象範囲 (Scope)
- 変更候補: `AGENTS.md`, `CLAUDE.md`, `.agents/skills/ui-ux-review/`, `docs/agent-principles.md`, `docs/ai-governance/`, `docs/process/task-execution.md`, `plans/TEMPLATE.md`, `plans/import-wordpack-rules.md`, サブディレクトリ `AGENTS.md`, `scripts/verify-ai-governance.sh`, GitHub PR / Issue / CI ハーネス文書。
- 影響を受ける契約 (API, schema, env など): 原則なし。ルール文書・検証ハーネスのみ。

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | WordPack 最新ルールを取得し、現行ルールとの差分を整理する | Done | `stillshore-chirp/wordpack-for-english` `main` を `459bd3d001b8c6b73cf6cf0c485fd65841d4375a` として取得し、governance core は source と一致（reports/evidence は除外）。 |
| M2 | 適用可能なルールを本リポジトリへ反映する | Done | WordPack 固有のプロダクト / インフラ手順は除外し、開発・完了・レビュー・UI/UX・公開安全性ゲート、Issue/PR templates、V4 governance を反映した。 |
| M3 | 文書・ハーネス整合性を検証する | Done | `bash scripts/verify-ai-governance.sh` と `git diff --check` が PASS。WordPack 固有語は計画の比較元説明以外に残存なし。 |
| M4 | 完了ゲートを確認する | pending | commit / push / 通常 PR / CI / review 状態を確認する。 |

## 5. 優先度付き小タスク
- [x] P0: target repo の cwd / branch / worktree / 直近履歴を確認する。
- [x] P0: target `main` を `origin/main` へ fast-forward 済みであることを確認する。
- [x] P0: ユーザー指定の WordPack repo から最新 `main` を取得する。
- [x] P0: `AGENTS.md`, `docs/agent-principles.md`, `docs/ai-governance/`, `.agents/skills/`, `scripts/verify-ai-governance.sh`, PR / Issue ハーネスの差分を棚卸しする。
- [x] P0: WordPack 側が正となる完了報告ゲート、Issue-first、review thread、PR title、公開安全性ルールを反映する。
- [x] P1: UI/UX governance V4 の不足ファイル、テンプレート、チェックリスト、検証 script map を反映する。
- [x] P1: 本リポジトリ固有の Python / Node / Bridge / Minecraft ルールと衝突しないようルート規約へ統合する。
- [x] P2: README / docs / gitignore / AGENTS の更新要否を確認する。

## 6. 受け入れ条件 (Acceptance Criteria)
- [x] WordPack 最新 `main` のエージェント / ハーネス関連ファイルを実ファイル名ベースで確認している。
- [x] 適用可能な汎用ルールが本リポジトリへ反映されている。
- [x] WordPack 固有のアプリ仕様やクラウド運用を、本リポジトリの仕様として誤って移植していない。
- [x] 競合する完了・PR・CI・レビュー・Issue ルールは WordPack 側の強い条件へ揃っている。
- [x] 検証スクリプトが新しいファイル構成を確認している。
- [x] README / docs / gitignore / AGENTS の更新要否を確認済み。

## 7. 検証コマンド (Verification)
- [x] `bash scripts/verify-ai-governance.sh`（PASS）
- [x] `git diff --check`（PASS）
- [x] WordPack 固有語 / 旧 `10-maintenance-policy.md` / conflict marker の grep 確認

## 8. 基本スモークテスト
- 手順: ガバナンス検証スクリプトを実行し、必須ファイル、薄い入口、Cursor rules 不在、skill metadata、UI/UX governance map を確認する。
- 期待結果: `AI governance verification: PASS`
- 結果: PASS

## 9. 再開コマンド
- `git status --short`
- `git diff --stat`
- `git -C /Users/Taishi/Documents/GitHub/wordpack-for-english rev-parse FETCH_HEAD`
- `bash scripts/verify-ai-governance.sh`
- `git diff --check`

## 10. 既知 Blocker
- なし

## 11. Feature Flag / Rollback（必要時のみ）
- Flag: なし
- Rollback 手順: この変更で追加・編集した Markdown / shell / GitHub template ファイルを元に戻す。

## 12. ステータスログ
- 2026-06-28: 作業分類をガバナンス変更 / 文書・ハーネス設定変更として開始。
- 2026-06-28: target repo は `main` clean、`origin/main` へ fast-forward 済み。作業ブランチ `codex/sync-wordpack-governance-rules` を作成。
- 2026-06-28: ユーザー指定の `stillshore-chirp/wordpack-for-english` `main` を取得し、`459bd3d001b8c6b73cf6cf0c485fd65841d4375a` を比較元に設定。
- 2026-06-28: `docs/ai-governance/` core を WordPack V4 へ同期。`reports/` と `evidence/` は WordPack 固有証跡のため除外した。
- 2026-06-28: Issue-first、PR title、非 draft PR、CI後 code review / review thread 確認、公開安全性ゲートを `AGENTS.md` と GitHub templates へ反映した。
- 2026-06-28: `docs/documentation-structure.md` と `docs/security-publication-checklist.md` を target 向けに追加し、検証スクリプトの file map を更新した。
- 2026-06-28: `bash scripts/verify-ai-governance.sh` と `git diff --check` が PASS。conflict marker なし、WordPack 固有語は計画内の比較元説明だけ。
- 2026-06-28: Issue-first ルールに従い、GitHub Issue #166 を作成。

## 13. 停止時の最終状態
- 最終状態: in_progress
- 停止理由（Blocked/Cancelled の場合は必須）:
- 再開条件: 差分棚卸しから継続。
- 次の最短アクション: WordPack の governance / harness ファイル一覧を target と比較し、適用可能な不足分を反映する。
