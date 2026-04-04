# AGENTS.md

## 適用範囲

- このファイルは `tests/` 以下に適用する。
- このディレクトリは主に Python 実装のテスト置き場であり、`node-bot/tests/` と `bridge-plugin/src/test/` はそれぞれのスコープで管理する。

## テスト構成

- `tests/`: Python の unit test。
- `tests/integration/`: モジュール間結合や adapter 境界の検証。
- `tests/e2e/`: クリティカルフローの高レベル確認。
- `tests/stubs/`: 外部依存や protocol を置き換えるための補助スタブ。

## ルール

- 既定は `pytest` とし、既存の `unittest` ベースケースは必要がない限り無理に書き換えない。
- 外部サービス、OpenAI API、Minecraft サーバー、Bridge HTTP の実ネットワーク呼び出しは避け、スタブ、フェイク、monkeypatch で再現する。
- 回帰テストは内部実装より観測可能な挙動とエラーシグナルを優先して検証する。
- async 処理のテストは `pytest.mark.anyio` など既存パターンに揃える。
- 重複する入力データやテストダブルはこのディレクトリ配下へ集約し、同じ失敗シナリオを別々に再実装しない。

## 変更時の着眼点

- planner / LangGraph 周りの変更では、成功パスだけでなく timeout、invalid output、recovery path の挙動も確認する。
- orchestrator / runtime の変更では、構造化ログや bridge event のような副次的シグナルも壊れていないかを見る。
- E2E は本当に価値の高い導線に絞り、unit / integration で十分な場合はそちらを優先する。
