# リファクタ後アーキテクチャ（Phase 7 時点）

## 1. 目的
本書は `mc-bot-egent-practice-1` の基盤刷新で、**どの契約を正本として運用するか**を明確化する。

- 見た目の多機能化より、契約の正本化・再現性・再開可能性を優先する。
- 実装詳細より、責務境界と拡張ポイントを先に共有する。

## 2. 全体構成

- `python/`:
  - planner（Structured Outputs / LangGraph 実行）
  - runtime（transport envelope validate、WebSocket ingress）
- `node-bot/`:
  - Minecraft 操作実行、Python との長寿命 WebSocket 連携
  - transport envelope 生成/検証
- `bridge-plugin/`:
  - Paper plugin + HTTP ブリッジ
  - Bridge 起点のイベント受け渡し（Phase 7 では envelope 統合の監視対象）
- `contracts/transport-envelope.schema.json`:
  - Node / Python / Bridge で共有する transport 契約

## 3. Transport Envelope 契約

### 3.1 正本
- 正本: `contracts/transport-envelope.schema.json`
- 必須キー:
  - `version`
  - `trace_id`
  - `run_id`
  - `message_id`
  - `timestamp`
  - `source`
  - `kind` (`command` / `event` / `status` / `error`)
  - `name`
  - `body`

### 3.2 バージョン運用
- 受信側は envelope validate を必須とする。
- version 不一致は握りつぶさず、`error/status` で明示する。
- legacy payload は adapter 1 箇所で受ける（最終的に削除対象）。

## 4. Planner の schema-first 契約

- planner 出力は Pydantic schema を正本にする。
- プロンプトは「JSON 強制」ではなく、計画品質（分解/制約/優先度）を担保する責務へ寄せる。
- refusal / 欠損 / 型不整合は「修理前提」ではなく制御された失敗として扱う。

## 5. 実行永続化・interrupt/resume

### 5.1 run/thread の責務
- `thread_id`: LangGraph の会話/実行スレッド単位（再開キー）。
- `run_id`: 単一実行フローの識別子（ログ/トレース相関）。
- `trace_id`: サービス横断で 1 リクエスト連鎖を追跡する相関キー。
- `message_id`: transport メッセージ単位の識別子。

### 5.2 persistence
- LangGraph 実行は checkpointer 前提で設計する。
- local/dev は SQLite 系、test は in-memory を既定にする。
- production backend は設定で差し替え可能に保つ。

### 5.3 confirmation
- 確認待ちは `interrupt()` ベースで停止し、resume 入力を受けて継続する。
- queue は ingress バッファに寄せ、実行状態の唯一正本にしない。

## 6. dev/prod 設定分離

- `env.dev.example`:
  - 開発効率重視（ローカル開発前提）
- `env.prod.example`:
  - 安全側デフォルト（公開範囲抑制、認証前提）

運用時は「どちらを正本として参照するか」を README と Compose で一致させる。

## 7. CI / Docker / Compose の正本

- CI は Python / Node / Bridge の最低 build/test を自動実行。
- Node install は `npm ci` を正本とし、lockfile を再現性の基準にする。
- Docker は build 時に依存解決し、起動時 install 前提を避ける。
- 開発時は `docker compose up --build --watch` を基準フローとする。

## 8. 可観測性の運用方針（Phase 7 残課題を含む）

### 8.1 必須方針
- 主要ログ/イベント/エラーに `trace_id` / `run_id` / `message_id` を付与する。
- 受信→planner→tool→送信を横断追跡可能にする。
- 機密値（token, secret, 生 prompt 全文）は redact する。

### 8.2 今後の実装タスク
- metrics:
  - queue/backlog length
  - planner latency
  - tool latency
  - interrupt 回数
  - resume 成功/失敗
  - transport error 数
- error taxonomy:
  - transport / planner / tool / infra で分類し、ダッシュボード化可能な形式で出力する。

## 9. 拡張ポイント（Phase 8 最小導入）

- `Skill` 永続表現インターフェース
- `Reflection` 保存先インターフェース
- plan 実行評価イベント記録
- 成功手順を再利用する registry 基盤

> 注意: 本刷新では自律学習系をフル実装しない。拡張点のみを低リスクで導入する。

## 10. Owner 判断が必要な項目

- LICENSE の最終選定
- 本番 secret 実値
- prod dashboard 公開可否
- 本番認証要件（Microsoft auth 等）
- 本番 checkpointer backend（例: Postgres）
