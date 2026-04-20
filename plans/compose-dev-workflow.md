# Task Plan: compose-dev-workflow

## Metadata
- Owner: Codex
- Branch: current
- Related issue / ticket: なし
- Last updated (UTC): 2026-04-20

## 1. 目的 (Goal)
- Docker Compose を正本とし、`make dev` / `make dev-host-paper` で開発起動を 1 コマンド化する。

## 2. 非目標 (Non-goals)
- AgentBridge の機能実装自体の変更。
- Node/Python の業務ロジック変更。

## 3. 対象範囲 (Scope)
- 変更対象ディレクトリ / モジュール: `docker-compose.yml`, `docker-compose.paper.yml`, `scripts/dev-up.sh`, `Makefile`, `README.md`
- 影響を受ける契約 (API, schema, env など): Compose 起動モード、開発起動コマンド

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | Compose を paper/profile と host-paper モードに分離 | Done | `docker-compose.paper.yml` を追加 |
| M2 | `make dev*` と `scripts/dev-up.sh` を追加 | Done | `.env` 自動作成と Linux override 対応 |
| M3 | README のクイックスタートを更新 | Done | 最短起動手順を追加 |

## 5. 受け入れ条件 (Acceptance Criteria)
- [x] `make dev` で Paper + Node + Python を起動できる構成が定義されている。
- [x] `make dev-host-paper` で Node + Python を起動できる構成が定義されている。
- [x] README に新しい最短手順と接続先が記載されている。

## 6. 検証コマンド (Verification)
- [x] `bash -n scripts/dev-up.sh`
- [x] `docker compose -f docker-compose.yml -f docker-compose.paper.yml config >/tmp/compose.paper.out`
- [x] `make -n dev && make -n dev-host-paper && make -n dev-down`

## 7. 既知 Blocker
- なし

## 8. Feature Flag / Rollback（必要時のみ）
- Flag: `--profile paper`
- Rollback 手順: `docker-compose.paper.yml` と `make dev*` を戻し、既存 `compose-up*` を利用。

## 9. ステータスログ
- 2026-04-20: Compose モード分離・起動スクリプト・README を更新し、静的検証を実施。

## 10. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）:
- 再開条件:
- 次の最短アクション:
