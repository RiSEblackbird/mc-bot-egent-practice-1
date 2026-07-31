# Task Plan: issue-168-luna-high

## Metadata
- Owner: Codex
- Branch: `codex/issue-168-luna-high`
- Related issue / ticket: GitHub Issue #168
- Last updated (UTC): 2026-07-31
- Source status file（必要時）: N/A

## 1. 目的 (Goal)
- すべての OpenAI Responses API 呼び出しを `gpt-5.6-luna` / reasoning `high` / verbosity `low` に固定し、旧モデル設定を廃止する。
- 計画、確認、障壁通知、再計画の各呼び出しについて、用途、トークン利用量、レイテンシ、成否、再計画深度を観測可能にする。

## 2. 非目標 (Non-goals)
- モデルルーティング、旧モデルへのフォールバック、Sol / Terra、xhigh / max / pro mode、multi-agent の導入。
- `PlanOut` 全体またはプロンプト全体の大規模再設計。
- OpenAI API 単価のコードへの埋め込み。

## 3. 対象範囲 (Scope)
- 変更対象ディレクトリ / モジュール: `python/planner_config.py`, `python/llm/`, `python/planner/`, `python/orchestrator/`, `tests/`, `env*.example`, `README.md`, `docs/`
- 影響を受ける契約 (API, schema, env など): Responses API payload、旧モデル環境変数、計画・確認・障壁通知の Structured Outputs、構造化ログ

## 4. マイルストーン
| ID | マイルストーン | 状態 (Done/Blocked/Cancelled) | メモ |
| --- | --- | --- | --- |
| M1 | 固定設定と旧環境変数廃止 | Done | 単一正本と fail-fast を実装 |
| M2 | 全 LLM 経路の payload/schema/観測統一 | Done | plan/replan/review/barrier を共通化 |
| M3 | 文書・テスト・静的確認 | Done | 119テストとローカル代表シナリオを完了 |
| M4 | commit / push / PR / CI / review | Done | PR #169、初回headのCI成功、review thread 0件を確認 |

## 5. 優先度付き小タスク
- [x] P0: Luna High 固定値を単一箇所へ定義し、旧モデル環境変数を fail-fast する。
- [x] P0: 全 Responses API payload から temperature を除き、reasoning / verbosity を必須化する。
- [x] P0: 確認質問専用 schema を追加し、PlanOut schema の誤用を解消する。
- [x] P0: plan / replan / pre_action_review / barrier_notification の観測情報を統一する。
- [x] P1: 回帰テスト、静的検索、代表シナリオを実行する。
- [x] P1: README、環境テンプレート、技術文書を同期する。
- [x] P2: PR、CI、Codex review thread を確認する。

## 6. 受け入れ条件 (Acceptance Criteria)
- [x] 全 Responses API 呼び出しが固定モデル、固定 reasoning、固定 verbosity を利用し、temperature / pro mode を送信しない。
- [x] 旧モデル環境変数・旧モデル参照・ランタイムフォールバックが現行設定から除去される。
- [x] PlanOut / BarrierNotification / PreActionReview の出力契約が呼び出し payload と一致する。
- [x] timeout / refusal / parse failure / barrier / 最大2回の再計画の既存安全策を維持する。
- [x] 用途、モデル、推論強度、verbosity、usage、latency、outcome、replan depth を観測できる。
- [x] 必須テスト、代表5シナリオ、静的検索が成功する。

## 7. 検証コマンド (Verification)
- [x] `python -m pytest tests/test_agent_config.py`
- [x] `python -m pytest tests/test_langgraph_scenarios.py`
- [x] `python -m pytest tests/test_planner_responses_payload.py tests/test_agent_replan.py`
- [x] `python -m pytest tests`
- [x] `git diff --check`
- [x] 旧モデル文字列と固定 payload の `rg` 静的確認

## 8. 基本スモークテスト
- 手順: OpenAI 呼び出しを fake client へ差し替え、単純移動、数量付き採掘、確認要求、再計画、障壁通知の5シナリオを実行する。
- 期待結果: 全 payload が Luna High 固定で、各 schema と観測結果が一致し、外部 API や Minecraft サーバーへ接続しない。

## 9. 再開コマンド
- `git switch codex/issue-168-luna-high`
- `python -m pytest tests/test_planner_responses_payload.py tests/test_langgraph_scenarios.py tests/test_agent_replan.py`

## 10. 既知 Blocker
- なし

## 11. Feature Flag / Rollback（必要時のみ）
- Flag: なし。Issue 要件により旧モデルへのランタイム切り戻しは実装しない。
- Rollback 手順: 変更 commit の revert を別 Issue / PR で判断する。

## 12. ステータスログ
- 2026-07-31: Issue、ルート/サブディレクトリ規約、OpenAI 公式モデル仕様と Responses API schema、既存コード・テストを確認。
- 2026-07-31: 固定設定、旧環境変数 fail-fast、専用 schema、共通観測処理、再計画深度の伝播を実装。
- 2026-07-31: README・環境テンプレート・技術文書を同期。119テスト、代表5シナリオ、静的確認が成功。
- 2026-07-31: commit `606ad52` を pushし、非ドラフト PR #169 を作成。Bridge / Node / Python CI 成功、review / thread / comment 0件を確認。

## 13. 停止時の最終状態
- 最終状態: Done
- 停止理由（Blocked/Cancelled の場合は必須）: N/A
- 再開条件: N/A
- 次の最短アクション: PR #169 のマージ判断
