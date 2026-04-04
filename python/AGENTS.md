# AGENTS.md

## 適用範囲

- このファイルは `python/` 以下に適用する。
- `python/dashboard/` では、より深い `python/dashboard/AGENTS.md` を優先する。

## このディレクトリの責務

- `planner/`: LangGraph のグラフ構築、プロンプト組み立て、プラン生成の中核。
- `orchestrator/`: Directive 実行、回復制御、役割連携など、プラン実行フェーズの調停。
- `runtime/`: ブートストラップ、WebSocket、アクショングラフ、イベント配線などの実行基盤。
- `services/`: MineDojo、reflection store、skill repository など外部連携や永続化の薄い境界。
- `actions/`: Node bot へ送る高レベル命令の組み立てとバリデーション。
- `llm/`: OpenAI SDK と gpt-5 系パラメータ解決の集約ポイント。

## Python 実装ルール

- OpenAI への呼び出しは、repo で既に採用している Responses API 中心の設計を維持し、リクエスト形状や gpt-5 系固有パラメータを各所に散らさない。
- モデル名、`reasoning.effort`、`verbosity`、`temperature`、`base_url` は `config.py`、`planner_config.py`、`llm/client.py` など既存の設定境界へ集約し、コードへ直書きしない。
- LangGraph のノードは状態遷移を追いやすい粒度で分け、ノード単体または部分グラフ単位でテストできる構造を優先する。
- 新しい外部連携を足す場合は `services/` か専用モジュールへ閉じ込め、planner や orchestrator から SDK 詳細を直接扱わせない。
- 非同期処理を基本とし、イベントループを長時間ブロックする同期 I/O や重い計算を素通しで入れない。
- エラー時は本質原因が追えるよう、例外、構造化ログ、フォールバック理由を残す。曖昧な `except Exception: pass` を避ける。
- ログは `setup_logger` と既存の構造化ログ文脈を使い、可観測性が必要な箇所では `span_context` など既存の OpenTelemetry 境界に乗せる。

## 設定と契約

- 新しい環境変数や設定値を追加する場合は、関連する設定クラス、既定値、バリデーション、`env.example`、README、docs を同じ変更で揃える。
- Python から Node bot や Bridge plugin へ渡す payload は後方互換性を意識し、片側の変更だけで契約を壊さない。
- planner 出力の JSON 正規化や recovery hint のような互換レイヤーは、散発的に増やさず一箇所へ集約する。

## テスト

- Python のテスト本体はこのディレクトリ内ではなく `/tests` にある。Python 実装を変えたら、対応する `/tests` 側も必ず確認する。
- 新しい回帰テストは、外部 API の生呼び出しではなくスタブ、フェイク、monkeypatch を使って失敗条件を再現する。
- async 処理のテストは既存の `pytest.mark.anyio` パターンに揃える。

## 変更時の着眼点

- planner は「何をするか」、orchestrator は「どう実行するか」、runtime は「どう動かすか」を分ける。
- 失敗時に握りつぶすより、再計画、短い失敗応答、ログ出力のどれを選ぶかを明示する。
- 仕様理解に役立つコメントは歓迎するが、改修メモや一時的な申し送りをコードコメントへ残さない。
