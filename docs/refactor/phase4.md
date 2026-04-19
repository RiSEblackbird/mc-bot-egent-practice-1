# Phase 4 進捗報告

## Phase 4 完了報告
- 変更概要:
  - planner の Responses payload 生成に `json_schema` 指定を導入し、`PlanOut` / `BarrierNotification` を schema-first で要求するように更新。
  - parse 経路を「まず schema を直接検証」へ変更し、旧来の `_normalize_plan_json()` は legacy fallback として限定利用に縮小。
  - payload の契約退行を防ぐ unit test（schema 指定時 / 非指定時）を追加。
- 主な変更ファイル:
  - `python/planner/__init__.py`
  - `python/planner/graph.py`
  - `tests/test_planner_responses_payload.py`
  - `docs/refactor/progress.json`
- 互換性影響:
  - planner 主経路は schema-first へ移行。
  - 非構造化 JSON 揺れは一時的に normalize fallback で吸収しつつ、warning ログで可視化。
- 実行したコマンド:
  - `python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（2 passed）。
- 残課題:
  - planner prompt から出力フォーマット強制文言をさらに削減し、計画品質重視へ寄せる。
  - `_normalize_plan_json()` を旧 state 移行用途まで段階的に縮小する。
  - refusal / 空応答 / 不完全応答の回帰テストを拡充する。
