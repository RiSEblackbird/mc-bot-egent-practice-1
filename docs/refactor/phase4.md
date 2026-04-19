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

## 追加スライス (2026-04-19)
- 変更概要:
  - planner の parse 経路に対する回帰テストを拡張し、`plan=[]` 応答と不正 JSON 応答の制御された失敗を固定化。
  - `build_plan_graph` をテストダブルで起動し、Responses API の実ネットワーク呼び出しなしで parse/fallback の契約を検証。
- 主な変更ファイル:
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - 実装変更なし（テスト追加のみ）。
  - 空手順時に `next_action=chat` / `clarification_needed=data_gap` を維持することを明示的に保証。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（4 passed）。
- 残課題:
  - refusal/空文字応答/必須キー欠落のケースをさらに網羅し、legacy normalize 依存領域を縮小する。

## 追加スライス (2026-04-19, 2)
- 変更概要:
  - planner プロンプトから JSON フォーマット強制の長大な例示を削除し、schema-first 前提で「計画品質・安全性・説明責務」を明示する方針へ整理。
  - parse 回帰テストを拡張し、空文字応答と必須キー欠落応答でも制御された safe fallback (`PlanOut(plan=[], resp="了解しました。")`) へ収束することを固定化。
- 主な変更ファイル:
  - `python/planner/prompts.py`
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - planner の外部 I/O 契約（`PlanOut` schema）は変更なし。
  - 不正・不完全レスポンス時のフォールバック挙動を明示的に回帰保証。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（6 passed）。
- 残課題:
  - Responses API の refusal シグナル（`response.output` 内の refusal content）に対するハンドリングと回帰テストを追加する。
  - `_normalize_plan_json()` の適用条件をさらに絞り、legacy state 移行用途へ段階的に限定する。

## 追加スライス (2026-04-19, 3)
- 変更概要:
  - planner の `call_llm` で Responses API の refusal を検知し、空文字応答時でも拒否メッセージを `PlanOut` の制御された chat フォールバックとして返すように更新。
  - refusal 検知ロジックを `planner/prompts.py` に追加し、`message.content[].type == refusal` と `item.type == refusal` の双方を安全に抽出可能にした。
  - 回帰テストを追加し、refusal 発生時に `next_action=chat`・`clarification_needed=confirmation`・`plan_refusal` backlog が維持されることを固定化。
- 主な変更ファイル:
  - `python/planner/prompts.py`
  - `python/planner/graph.py`
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - 正常系の schema-first 経路は変更なし。
  - refusal シグナル時は汎用 `"了解しました。"` ではなく、モデルが返した確認メッセージを優先して返すように改善。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（7 passed）。
  - OpenTelemetry exporter が `localhost:4318` 未起動のため warning ログが出るが、テスト自体は成功。
- 残課題:
  - `_normalize_plan_json()` の適用条件をさらに絞り、legacy state 移行用途へ段階的に限定する。
