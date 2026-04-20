# Task Plan: refactor-foundation-phase7-8

## Metadata
- Owner: Codex
- Branch: current working branch
- Related issue / ticket: Codex作業指示書「mc-bot-egent-practice-1 基盤刷新」Phase 7-8
- Last updated (UTC): 2026-04-20

## 1. 目的 (Goal)
- Phase 7/8 の完了に向け、可観測性・重複整理・最終ドキュメント化と研究拡張ポイントの最低限導入を、安全側で段階的に進める。

## 2. 非目標 (Non-goals)
- 既存 transport/planner の全面作り直し。
- 本番 secret、LICENSE、本番 checkpointer backend の決定。

## 3. 対象範囲 (Scope)
- 変更対象ディレクトリ / モジュール:
  - `docs/refactor/`
  - `plans/`
- 影響を受ける契約 (API, schema, env など):
  - 既存 `contracts/transport-envelope.schema.json` の運用ルール（仕様追加なし）。

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | Phase 7 の現状整理と architecture 文書の新規作成 | Done | `docs/refactor/architecture.md` を追加 |
| M2 | progress/計画の durable 更新 | Done | `docs/refactor/progress.json` と本計画を更新 |
| M3 | Phase 7 実装（可観測性/重複整理）残件の切り出し | Done | architecture と phase7 で残課題を明示 |

## 5. 受け入れ条件 (Acceptance Criteria)
- [x] `docs/refactor/architecture.md` が存在し、新設計（envelope, planner schema-first, interrupt/resume, ID 意味, dev/prod env 差分）を説明している。
- [x] `docs/refactor/progress.json` に現在フェーズの状態が反映されている。
- [x] 次セッションで迷わないよう、未完了の可観測性/研究要素を残課題として構造化している。

## 6. 検証コマンド (Verification)
- [x] `python -m json.tool docs/refactor/progress.json >/dev/null`
- [x] `rg -n "Phase 7|trace_id|run_id|message_id|interrupt|thread_id|env.dev.example|env.prod.example" docs/refactor/architecture.md`
- [x] `git diff -- docs/refactor/progress.json docs/refactor/architecture.md plans/refactor-foundation-phase7-8.md`

## 7. 既知 Blocker
- なし（本スライスはドキュメントと進捗正規化に限定）。

## 8. Feature Flag / Rollback（必要時のみ）
- Flag: なし
- Rollback 手順: 追加ドキュメント削除 + progress 差し戻し

## 9. ステータスログ
- 2026-04-20: Phase 6 完了時点の `progress.json` を確認し、Phase 7 の docs 着手。
- 2026-04-20: `docs/refactor/architecture.md` を作成し、現行アーキテクチャと残課題を記録。
- 2026-04-20: `progress.json` を Phase 7 `in_progress` に更新し、次アクションを固定。

## 10. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）: 
- 再開条件: Phase 7 のコード実装（ID 伝搬強化・metrics 整備・重複除去）に着手する。
- 次の最短アクション: `docs/refactor/phase7.md` を追加し、実装差分と検証結果を積み上げる。
