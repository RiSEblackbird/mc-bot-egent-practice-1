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

## 追加スライス (2026-04-19, 4)
- 変更概要:
  - planner の legacy normalize 境界に対する回帰テストを追加し、`arguments.coordinates` の数値 coercion・`notes` の辞書化・`clarification_needed` enum 補正を固定化。
  - top-level `clarification_needed` が不正値でも `data_gap` へ補正され、parse 主経路が制御された挙動を維持することを検証。
- 主な変更ファイル:
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - 実装変更なし（テスト追加のみ）。
  - legacy normalize を縮小する際に壊しやすい境界（型揺れ・enum 揺れ）を先に固定。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（9 passed）。
- 残課題:
  - `_normalize_plan_json()` の適用範囲を旧 state / 外部 legacy データ境界へ限定し、新規 LLM 出力での常用経路から段階的に除去する。

## 追加スライス (2026-04-19, 5)
- 変更概要:
  - planner の parse 経路で Responses API の structured output (`output_parsed` / `content[].parsed`) を優先して検証するように変更し、schema-first の主経路を文字列 JSON parse に依存しない形へ整理。
  - `structured_output` が取得できる場合は legacy `_normalize_plan_json()` を経由しないようにし、normalize は文字列出力のみの後方互換境界へ限定。
  - 回帰テストを追加し、`output_text` が壊れていても `output_parsed` が正常なら `PlanOut` を復元できることを固定化。
- 主な変更ファイル:
  - `python/planner/prompts.py`
  - `python/planner/graph.py`
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - schema-first 経路の堅牢性が向上し、structured output を返すモデル応答で JSON repair 依存が減少。
  - legacy normalize は後方互換（非構造化文字列）に限定され、段階的除去に向けた境界が明確化。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（10 passed）。
- 残課題:
  - `structured_output` 自体が schema 不整合だった場合の error taxonomy を planner/domain で明確化する。
  - `_normalize_plan_json()` の責務を旧 state migration へさらに限定する。


## 追加スライス (2026-04-20)
- 変更概要:
  - planner の parse 失敗を可観測化するため、`parse_error_code` を導入し `structured_output_schema_mismatch` / `plan_json_decode_failed` などの安定コードへ分類するように更新。
  - `parse_plan` ノードの structured output 失敗・JSON 失敗の両経路で同一形式のエラー分類を返し、`record_structured_step` の outputs にも分類コードを記録するようにした。
  - 回帰テストを追加し、schema 不整合な `output_parsed` と不正 JSON 文字列の双方で `parse_error_code` が期待どおり返ることを固定化した。
- 主な変更ファイル:
  - `python/planner/graph.py`
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - planner のフォールバック動作（安全な `PlanOut(plan=[], resp="了解しました。")`）は維持。
  - 失敗時に機械可読な taxonomy を追加したため、後続のメトリクス集計・障害切り分けが容易になった。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（12 passed）。
- 残課題:
  - `_normalize_plan_json()` を旧 state migration / 外部 legacy 境界へさらに限定し、主経路から段階的に除去する。

## 追加スライス (2026-04-20, 2)
- 変更概要:
  - planner の parse 経路で legacy `_normalize_plan_json()` を発火させる条件を見直し、JSON として壊れている payload (`json_invalid`) では normalize を試みず即時に制御された失敗へ分類するようにした。
  - `_should_use_legacy_normalize` を追加し、legacy normalize を「schema mismatch を持つ JSON 文字列境界」に限定した。
  - 回帰テストを追加し、非 JSON payload 時に normalize が呼ばれないことを monkeypatch で固定化した。
- 主な変更ファイル:
  - `python/planner/graph.py`
  - `tests/test_planner_responses_payload.py`
- 互換性影響:
  - schema-first の正常系と既存 fallback (`PlanOut(plan=[], resp="了解しました。")`) は維持。
  - 非 JSON 文字列に対する不要な normalize 試行がなくなり、legacy fallback の責務が狭まった。
- 実行したコマンド:
  - `PYTHONPATH=python python -m pytest tests/test_planner_responses_payload.py`
- テスト結果:
  - 成功（13 passed）。
- 残課題:
  - `_normalize_plan_json()` の責務を、旧 state migration / 外部 legacy boundary coercion のみへさらに限定する。
