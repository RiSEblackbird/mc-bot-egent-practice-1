# AGENTS.md

## 適用範囲

- このファイルは `python/` 以下に適用する。
- `python/dashboard/` では、より深い `python/dashboard/AGENTS.md` を追加で適用する。

## 作業進行

- 共通の進行、GitHub配送、公開安全性、完了報告はルート `AGENTS.md` と発動したtask Skillに従う。
- このファイルはPython領域固有の実装規約と検証観点だけを正本化する。

## このディレクトリの責務

- `planner/`: LangGraphのgraph構築、prompt組み立て、plan生成の中核。
- `orchestrator/`: Directive実行、回復制御、役割連携など、plan実行phaseの調停。
- `runtime/`: bootstrap、WebSocket、action graph、event配線などの実行基盤。
- `services/`: MineDojo、reflection store、skill repositoryなど外部連携や永続化の境界。
- `actions/`: Node botへ送る高レベル命令の組み立てとvalidation。
- `llm/`: OpenAI SDKとmodel parameter解決の集約point。

## Python実装ルール

- OpenAI呼び出しは、repoで採用しているResponses API中心の設計を維持し、request shapeやmodel固有parameterを各所へ散らさない。
- model名、reasoning、verbosity、temperature、base URLは、既存の設定・client境界へ集約し、呼び出し元へ直書きしない。
- LangGraph nodeは状態遷移を追いやすい責務で分け、node単体または部分graph単位でtestできる構造を優先する。
- 新しい外部連携は `services/` または専用moduleへ閉じ込め、plannerやorchestratorからSDK詳細を直接扱わせない。
- 非同期処理を基本とし、event loopを長時間blockする同期I/Oや重い計算を素通しで入れない。
- error時は本質原因が追えるよう、例外、構造化log、fallback理由を残す。曖昧な `except Exception: pass` を避ける。
- logは既存のloggerと構造化contextを使い、観測可能性が必要な箇所では既存のOpenTelemetry境界に乗せる。

## 長時間処理と再開

- Responses APIやLangGraphの再開・cancel・polling境界は既存のruntime / orchestratorへ集約し、各呼び出し側へ再開logicを散らさない。
- 再実行され得る副作用ではidempotency、checkpoint、deduplicationを検討し、`recovery_hints`、structured event、durable stateを再開判断に使える形で保つ。
- Minecraft worldは停止中に変化し得る。再開直後はinventory、位置、周辺block、保護領域、接続状態を再観測し、checkpointとの差を確認してから再計画する。
- code上のcheckpointだけで実worldの安全性を断定しない。

## 設定と契約

- 新しい環境変数や設定値を追加する場合は、関連する設定class、既定値、validation、`env.example`群、README、docsを同じ変更で揃える。
- PythonからNode botやBridge pluginへ渡すpayloadは後方互換性を意識し、片側の変更だけで契約を壊さない。
- planner出力のJSON正規化やrecovery hintなどのcompatibility layerは、散発的に増やさず一箇所へ集約する。

## テスト

- Pythonのtest本体は `/tests` にある。Python実装を変えたら `tests/AGENTS.md` と対応testを確認する。
- 回帰testは外部APIの生呼び出しを避け、stub、fake、monkeypatchで失敗条件を再現する。
- async処理のtestは既存の `pytest.mark.anyio` patternに揃える。

## 変更時の着眼点

- plannerは「何をするか」、orchestratorは「どう実行するか」、runtimeは「どう動かすか」を分ける。
- 失敗時に再計画、短い失敗応答、停止、log出力のどれを選ぶかを明示する。
- commentは現行の理由、制約、契約を補い、改修メモや一時的な申し送りを残さない。
