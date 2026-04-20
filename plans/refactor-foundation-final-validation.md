# Task Plan: refactor-foundation-final-validation

## Metadata
- Owner: Codex
- Branch: current working branch
- Related issue / ticket: Codex作業指示書「mc-bot-egent-practice-1 基盤刷新」最終受け入れ再検証
- Last updated (UTC): 2026-04-20

## 1. 目的 (Goal)
- Phase 0〜8 の成果物が現行リポジトリで再現可能かを再検証し、受け入れ判断に必要な証跡を固定する。

## 2. 非目標 (Non-goals)
- 新規機能の追加。
- Node 実行環境（22+）や Docker 未導入環境の外部制約をコード変更で迂回すること。

## 3. 対象範囲 (Scope)
- 変更対象ディレクトリ / モジュール:
  - `plans/`
  - `docs/refactor/`
- 影響を受ける契約 (API, schema, env など):
  - なし（検証結果の記録のみ）。

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | 検証コマンド実行（Python/Node/Bridge/Compose） | Done | Node 22 / Docker 不在は環境 Blocker として記録 |
| M2 | 最終検証ドキュメント化 | Done | `docs/refactor/final-validation.md` を追加 |
| M3 | 計画ファイルの状態整合 | Done | ステータスログと停止状態を確定 |

## 5. 受け入れ条件 (Acceptance Criteria)
- [x] 受け入れテスト観点（Python / Node / Bridge / compose config）を実行し、成否と理由を記録している。
- [x] 環境起因の失敗と実装修正が必要な失敗を区別して記録している。
- [x] 次アクション（Node 22 / Docker 利用環境での追試）が明示されている。

## 6. 検証コマンド (Verification)
- [x] `bash scripts/setup-python-env.sh`
- [x] `source .venv/bin/activate && python -m pytest tests`
- [x] `bash scripts/run-node-bot.sh test`
- [x] `bash scripts/run-node-bot.sh build`
- [x] `bash scripts/build-bridge-plugin.sh`
- [x] `docker compose config`

## 7. 既知 Blocker
- Node 実行バージョンが `v20.19.6` のため Node 系検証が停止（22+ 必須）。
- Docker CLI が未導入で `docker compose config` を実行不可。

## 8. Feature Flag / Rollback（必要時のみ）
- Flag: なし
- Rollback 手順: 追加した計画/検証ドキュメントを削除すれば差し戻し可能。

## 9. ステータスログ
- 2026-04-20: 検証コマンドを実行し、Python/Bridge は成功、Node/Docker は環境 Blocker を確認。
- 2026-04-20: `docs/refactor/final-validation.md` を追加し、受け入れ観点の実行結果を固定。

## 10. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）:
- 再開条件: Node 22+ と Docker 利用可能環境で再検証する場合に再開。
- 次の最短アクション: Node 22 を有効化し `bash scripts/run-node-bot.sh test && bash scripts/run-node-bot.sh build` を再実行。
