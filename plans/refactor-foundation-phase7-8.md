# Task Plan: refactor-foundation-phase7-8

## Metadata
- Owner: Codex
- Branch: current working branch
- Related issue / ticket: Codex作業指示書「mc-bot-egent-practice-1 基盤刷新」Phase 7-8
- Last updated (UTC): 2026-04-20

## 1. 目的 (Goal)
- Phase 7/8 を完了し、可観測性・ドキュメント・研究拡張ポイントの土台を durable に固定する。

## 2. 非目標 (Non-goals)
- 既存 transport/planner の全面作り直し。
- 本番 secret、LICENSE、本番 checkpointer backend の最終決定。

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
| M4 | Phase 8 最小導入の成果固定（owner decision 境界含む） | Done | `phase8.md` / `owner-decisions.md` を追加 |

## 5. 受け入れ条件 (Acceptance Criteria)
- [x] `docs/refactor/architecture.md` が存在し、新設計（envelope, planner schema-first, interrupt/resume, ID 意味, dev/prod env 差分）を説明している。
- [x] `docs/refactor/progress.json` に Phase 7/8 の完了状態が反映されている。
- [x] Phase 8 の最小導入結果を `docs/refactor/phase8.md` で追跡可能。
- [x] 所有者判断待ち項目を `docs/refactor/owner-decisions.md` に明文化。

## 6. 検証コマンド (Verification)
- [x] `python -m json.tool docs/refactor/progress.json >/dev/null`
- [x] `rg -n "owner-decisions|Phase 8|SkillRepository|ReflectionStore|registry" docs/refactor plans/refactor-foundation-phase7-8.md`
- [x] `git diff -- docs/refactor/phase8.md docs/refactor/owner-decisions.md docs/refactor/progress.json plans/refactor-foundation-phase7-8.md`

## 7. 既知 Blocker
- なし（Phase 7/8 の本計画範囲は達成済み）。

## 8. Feature Flag / Rollback（必要時のみ）
- Flag: なし
- Rollback 手順: 追加ドキュメント削除 + progress 差し戻し

## 9. ステータスログ
- 2026-04-20: Phase 6 完了時点の `progress.json` を確認し、Phase 7 の docs 着手。
- 2026-04-20: `docs/refactor/architecture.md` を作成し、現行アーキテクチャと残課題を記録。
- 2026-04-20: `progress.json` を Phase 7 `in_progress` に更新。
- 2026-04-20: `docs/refactor/phase8.md` と `docs/refactor/owner-decisions.md` を追加し、Phase 8 完了状態を記録。

## 10. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）:
- 再開条件: 新規要件または owner decision の反映が必要になった時点で再開する。
- 次の最短アクション: owner decision が確定した項目を `env.prod.example` / 運用 docs / 実装設定へ反映する。
