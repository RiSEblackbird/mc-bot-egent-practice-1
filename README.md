# Minecraft 自律ボット（Python + gpt-5-mini + Mineflayer）

本プロジェクトは、Minecraft Java Edition 1.21.1 + Paper 上で動作する **日本語対応の LLM 自律ボット** です。
Python 側が LLM（OpenAI **gpt-5-mini**）でチャット意図を解釈し、Node.js 側の Mineflayer ボットへ行動コマンドを送ります。

## 1. できること（初期）
- 農業：畑の整備/収穫/再植付け、パン作成
- 自動採掘：鉄/石炭/ダイヤのブランチマイニング等
- 探索：プレイヤー基準の周辺探索
- クラフト支援：素材収集→作業台でクラフト→受け渡し
- 自己防衛戦闘：敵対Mobの回避/迎撃
- 簡易建築：小屋/倉庫などの原始的建築
- プレイヤー随伴：「ついてきて」で追尾モード
- 装備持ち替え：ツール名の指示に従って適切な手へ装備
- マルチエージェント協調：防衛・補給など役割を LangGraph から切り替え、位置・状態イベントを共有メモリで同期

## バージョン方針

本プロジェクトは既定で **Minecraft Java / Paper 1.21.1** を使用します。Paper サーバーの JAR は `paper-1.21.1-*.jar` を取得して `paper.jar` にリネームし、`C:\mc\paper\paper.jar` など任意の配置先で保守してください。Bot 側の既定プロトコルも `MC_VERSION=1.21.1` を参照するため、サーバーとクライアントの齟齬を防ぎます。将来的に別バージョンを試す場合は `.env` の `MC_VERSION` や `paper.jar` を差し替えるだけで移行できます。

### クライアントとの互換性

原則として Minecraft クライアントも 1.21.1 を利用してください。1.21.1 以外のクライアントから接続する必要がある場合は、Paper サーバーに ViaVersion / ViaBackwards などの互換プラグインを導入して調整します（完全互換ではない点に留意）。

## 2. Paper サーバーの起動（前提）
Windows 例：
```powershell
cd C:\mc\paper
java -Xms4G -Xmx4G -jar .\paper.jar --nogui
```

```powershell
cd C:\mc\paper-1.21.1
java -Xms4G -Xmx4G -jar .\paper-1.21.1.jar --nogui
```

開発中は `server.properties` の `online-mode=false` を推奨。

### Bridge HTTP サーバーの構成と拡張手順

Paper プラグイン側の REST API は `bridge-plugin/src/main/java/com/example/bridge/http` 配下にまとめています。`BaseHandler` が認証・例外処理・JSON 変換を共通化し、`http/handlers/` 配下へ具体的なエンドポイントを 1 クラスずつ配置しています。サーバー起動やコンテキスト登録は `BridgeHttpServer` のみが担当するため、依存関係はすべてコンストラクタで注入し、ユニットテストではモックを差し替えやすい設計です。

新しい REST API を追加する場合は以下の手順に沿ってください。

1. `http/handlers/` にハンドラクラスを追加し、`BaseHandler` を継承して認証と共通レスポンス処理を再利用する。
2. 必要な依存（`JobRegistry` や `WorldGuardFacade` など）はコンストラクタ引数で受け取り、フィールドへ保持する。
3. `BridgeHttpServer#registerContexts` に新ハンドラのコンテキストを登録する。イベントストリームのようなオプショナル機能は設定フラグで有効化可否を分岐させる。
4. 追加したエンドポイントの想定リクエスト/レスポンス例をテストし、Mineflayer や Python クライアントからの利用手順を README/ドキュメントへ追記する。

## 3. セットアップ

### 3.1 Node（Mineflayer ボット）

Node 側のボット実装は TypeScript 化しており、`npm start` を実行すると自動的にビルドと起動を行います。
なお Mineflayer v4.33 系は Node.js 22 以降を要求するため、開発環境の Node バージョンが古い場合は `nvm` などでのアップデートを強く推奨します。
本リポジトリ直下の `.nvmrc` は 22 系を指しているので、`nvm use` でバージョンを切り替えられます。

Mineflayer 起動時の環境変数は `node-bot/runtime/config.ts` へ集約しており、Docker 実行時の `MC_HOST` 補正や `MC_VERSION` のフォールバック、`MOVE_GOAL_TOLERANCE` の上下限チェックを一括で行います。
`PATHFINDER_ALLOW_PARKOUR` や `PATHFINDER_DIG_COST_ENABLED` などの移動系チューニングも同じレイヤーで正規化され、掘削コストやスプリント許可・強制移動リトライの閾値を `.env` から安全に切り替えられます。
移動系の拡張ポイントや `forcedMove` リトライ設計、設定追加時のメンテナンス手順は `docs/movement_extension_design.md` にまとめています。移動戦略を変更する際は、設定の追加・ログ出力・テストの更新をこのドキュメントに沿って反映してください。
WebSocket サーバーの起動・接続管理は `node-bot/runtime/server.ts` に分離し、OpenTelemetry の初期化は `node-bot/runtime/telemetryRuntime.ts` へ整理しました。コマンド/レスポンス型も `node-bot/runtime/types.ts` へ集約しているため、IDE から追跡しやすくなっています。
設定変更のテストは `node-bot/tests/config.test.ts` を実行すると安全に回帰確認できます。

制御ループの設定値は `node-bot/bot.ts` の冒頭で初期化してからログへ出力するよう整理しました。`npm start` で Mineflayer ボットを再起動し、
標準出力に `mode=... tick=... maxSeq=...` が表示されクラッシュが発生しないことを確認してください。未初期化定数を参照した際の例外はこの変更で解消されます。

2025年11月のアップデートでは `gatherStatus` に `environment` 種別を追加し、近傍エンティティ・照度・液体/空洞ヒートマップなどの観測値を一括取得できるようになりました。
Mineflayer 側は一定間隔で `perception` イベントを WebSocket へ push し、Python エージェントは最新の認知スナップショットを常に共有メモリへ保持します。
`PERCEPTION_*` 環境変数でスキャン範囲やブロードキャスト間隔を細かく調整できます（既定: `PERCEPTION_ENTITY_RADIUS=12`, `PERCEPTION_BLOCK_RADIUS=4`,
`PERCEPTION_BLOCK_HEIGHT=2`, `PERCEPTION_BROADCAST_INTERVAL_MS=1500`）。暗い坑道や液体検知の頻度が高い場合はこれらの値を上げることで安全性を優先した認知に切り替えられます。

LangGraph 共有メモリへ送る `agentEvent` 系の WebSocket 配信はセッション常駐化し、`AGENT_WS_*` と `AGENT_EVENT_*` でヘルスチェック間隔・接続/送信タイムアウト・リトライ回数・バッチ間隔・キュー上限を細かく調整できます。位置やステータス更新がバーストした場合でも一度確立したセッションとバッチャを介してまとめて配送するため、接続確立コストが増大せず、ログにも送信結果とエラー内容が構造化 JSON で残ります。

2025 年 10 月のアップデートでは `node-bot/runtime/roles.ts` に役割カタログを追加し、`setAgentRole` コマンドで LangGraph から防衛/補給/偵察などのロールへ即座に切り替えられるようになりました。Mineflayer から `agentEvent` チャネル経由で位置・体力・役割のスナップショットを Python 側へ push するため、イベント駆動で共有メモリが更新されます。

`bot.ts` には `gatherStatus` WebSocket コマンドを実装しており、Python エージェントが現在位置・インベントリ・体力/満腹度と掘削許可のスナップショットを即座に取得できます。Mineflayer の `canDig` 設定やゲームモードから地下採掘の可否を判定し、所持ツルハシのエンチャント情報と併せて JSON で返すため、チャット経由で逐一質問せずとも自律的な計画を立てられます。

```bash
cd node-bot
npm install
npm start
# TypeScript ソースを確認したい場合は npm run build で dist/bot.js を生成
# ユニットテストは vitest で実行可能
npm test
```

### 3.2 Python（LLM エージェント）

```bash
cd python
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r ../requirements.txt
cp ../env.example ../.env
# .env を編集（OpenAIキーや接続設定）
python -m runtime.bootstrap
```

#### 3.2.1 動作確認済み環境と依存バージョン

- OS: Ubuntu 24.04.3 LTS（ローカル検証コンテナ）
- Python: 3.12.12
- Node.js: 22.x（`.nvmrc` の指定に従う）
- Python 依存（固定版）: `openai==1.109.1` / `python-dotenv==1.0.1` / `websockets==12.0` / `httpx==0.27.2` / `pydantic==2.8.2` / `watchfiles==0.21.0` / `langgraph==0.1.16` / `opentelemetry-api==1.27.0` / `opentelemetry-sdk==1.27.0` / `opentelemetry-exporter-otlp-proto-http==1.27.0`
 / `langsmith==0.1.147`

`pip install -r ../requirements.txt` で上記バージョンへ統一すると、`python/runtime/bootstrap.py` を経由したエージェント起動と Responses API 型の解決（`openai.types.responses`）がエラーなく通ることを確認済みです。`python -m runtime.bootstrap` で起動できます。

#### 3.2.2 依存更新時のチェックリスト

1. **クリーン環境での検証**: `.venv` を作り直し、`pip install -r ../requirements.txt` を実行して新しい制約でも解決できるか確認する。
2. **自動テスト**: 依存追加・更新後は少なくとも `pytest tests/test_agent_config.py tests/test_structured_logging.py` を走らせ、設定読み込みと構造化ログの互換性を確認する。LangGraph/LLM 周辺を触った場合は `pytest tests/test_langgraph_scenarios.py` も追加で実行する。
3. **手動起動チェック**: `cp ../env.example ../.env` の後に `python -m python` を実行し、WebSocket バインドまで到達すること、`openai.types.responses.Response` などの型解決が通ることを目視する。
4. **破壊的変更のサイン検知**: `client.responses.create` 呼び出しシグネチャや `EasyInputMessageParam` のフィールドに差分が出ていないかを確認し、変更があれば `python/planner/__init__.py`・`python/llm/client.py`・`python/planner_config.py` で一元化したペイロード生成ロジックと設定読込を追従させる。

Python エージェントは `AGENT_WS_HOST` / `AGENT_WS_PORT` で指定したポートに WebSocket サーバーを公開します。
Node 側が接続に使うのは `AGENT_WS_URL` です。`0.0.0.0` は待受専用のため接続先には `python-agent`（Docker Compose）や `host.docker.internal` / `127.0.0.1` など、実際に到達可能なホスト名を指定してください。接続拒否が出る場合は Python エージェントの起動と `AGENT_WS_URL` の解決可否を確認します。
Mineflayer 側（Node.js）がチャットを受信すると、自動的にこのサーバーへ `type=chat` の JSON を送信し、
Python 側で LLM プランニングとアクション実行が行われます。`DEFAULT_MOVE_TARGET` を変更すると、
「移動」系のステップで座標が指定されなかった場合のフォールバック座標を調整できます。なお Python エージェント
は、直前に検出した座標付きステップを記憶し、直後に続く「移動」「向かう」などの抽象ステップでは同じ目的地を
再利用するため、計画途中で座標の記述が省略されても同じ地点へ向かい続けます。
2025/12 のモジュール分割では `python/orchestrator/plan_executor.py`・`action_analyzer.py`・`skill_detection.py`
を追加し、`AgentOrchestrator` 本体は依存注入とハンドオフに専念するようになりました。
`PlanExecutor` が LangGraph からの ActionDirective 実行と再計画フローを一手に担い、
`ActionAnalyzer` が自然言語ステップからカテゴリ・座標・装備/採掘パラメータを抽出します。
`python/orchestrator/role_perception_adapter.py` には役割切替と perception 系のラッパーを集約した
`RolePerceptionAdapter` を用意し、`AgentOrchestrator` からは `role_perception` 経由で
`BridgeRoleHandler` / `PerceptionCoordinator` を操作します。構造化ログと例外ハンドリングを
アダプタ内へ一本化しているため、テストでは `RolePerceptionAdapter.apply_role_switch()` の成否や
`collect_block_evaluations()` のログを直接確認するだけで役割更新・環境認識フローを検証できます。
検出レポートや MineDojo スキル処理は `SkillDetectionCoordinator` にまとめられたため、
`python/agent.py` の責務はチャットキューとメモリ更新に絞られ、1 ファイルのコンテクスト量を大幅に削減しています。
2026/01 以降は `orchestrator/task_router.py` に新設した `TaskRouter` が `ChatPipeline` と
`SkillDetectionCoordinator` の連携を肩代わりし、分類（行動/検出）・スキル探索・
未実装アクション backlog の整理を単一のファサードで扱います。ActionAnalyzer の
キーワード設定を拡張したい場合は TaskRouter に差し替えるだけで plan 実行系へ影響
を伝播でき、MineDojo 側の探索/再生ハンドリングもここに集約されています。
2026/02 では `agent_bootstrap.initialize_agent_runtime()` を追加し、`AgentOrchestrator`
 のコンストラクタはファクトリが返す束ねられた依存セットをフィールドへ割り当てる
 だけの構造になりました。`runtime_settings` や `skill_repository` を差し替えたい場合
 はコンストラクタ引数へ渡すだけで同ファクトリ経由の注入経路が選択され、
 `PlanRuntimeContext` にも設定値が一括で伝搬します。LangGraph の閾値や MineDojo
 クライアントの差し替えは `python/agent_bootstrap.py` を 1 箇所読めば追跡できるため、
 新規メンバーでも初期化フローを把握しやすくなっています。
移動および障壁通知については `services/movement_service.py` に委譲し、`AgentOrchestrator`
のプライベートメソッドを廃止しました。`MovementService` は Actions 依存を明示的に
受け取り、移動成功時の `Memory.last_destination` 更新と構造化ログ出力をセットで実施
します。LangGraph ノード側では `await orchestrator.movement_service.move_to_coordinates((x,
y, z))` のように呼び出すだけでログ/メモリ/失敗通知を統一フォーマットで処理できる
ため、新規メンバーでも副作用の流れを追いやすくなりました。
ランタイムは `python/runtime` 配下へ分割しており、`bootstrap.py` が設定読込と依存組み立て、`websocket_server.py` が WebSocket 受信ループ、`minedojo.py` が自己対話やスキル登録ヘルパーを担います。`python/__main__.py` からはこれらを束ねて `python -m python` で起動できます。
設定値の読み込みは `python/config.py` に統合しており、ポート番号やデフォルト座標のバリデーションを一括で処理します。
ユニットテスト `tests/test_agent_config.py` で挙動を確認できるため、環境変数を追加した場合も回帰チェックが容易です。
Responses API のタイムアウトは `LLM_TIMEOUT_SECONDS` で制御でき、既定値 30 秒を過ぎるとフォールバックプランへ即時切り替えます。
チャット処理キューは `AGENT_QUEUE_MAX_SIZE` で上限を指定でき、混雑時は最古のタスクを破棄して最新指示を優先します。
`WORKER_TASK_TIMEOUT_SECONDS` を超えても応答がないチャットタスクはリトライ上限まで再投入され、超過時は自動的にスキップされます。
さらにチャット指示は **検出報告タスク**（座標・所持品などの報告指示）と **行動系タスク**（移動・採掘・建築など）に
分類され、Mineflayer で実行可能なアクションと未実装のタスクを丁寧に切り分けます。未対応カテゴリは Python 側で
待機リストとして整理し、「農作業」「建築作業」などのカテゴリ名を添えてプレイヤーに状況を説明するため、余計な
テンプレート文が差し込まれることなく自然な応答フローを維持できます。
建築カテゴリについては `docs/building_state_machine.md` に LangGraph ノードが従うべき
フェーズ定義・遷移条件・ロールバック指針を整理しており、長期ジョブを中断しても
安全に再開できるようチェックポイント設計の前提を共有しています。

#### 3.2.4 LangGraph 可視化ヘルパー

Python 側で LangGraph の流れを確認したい場合は `UnifiedAgentGraph.render_mermaid()` を呼び出すと、
「意図解析 → プラン生成 → アクションディスパッチ → Mineflayer 連携」の順に並んだ Mermaid 文字列を出力できます。
`python/runtime/action_graph.py` に定義されている統合グラフを `graph = UnifiedAgentGraph(orchestrator)` で初期化し、
`print(graph.render_mermaid())` を実行するだけでステップ一覧を図式化できるため、曖昧な指示を受けた際の
ルーティング確認や新人メンバー向けの説明資料作成に活用してください。

#### 3.2.3 アクションコマンドとチャット対応表

Python 側の `python/actions.py` では、以下の WebSocket コマンドを構造化ログ付きで組み立てます。ペイロードは Node 側の
`node-bot/commands.md` のスキーマに合わせ、`event_level`・`context` を含む JSON ログを出力するため、チャット指示からの変換過程を後追いしやすくなっています。

| チャット例 | 送信コマンド | ペイロード例 |
| --- | --- | --- |
| 「この座標まで掘り進んで」 | mineBlocks | `{ "type": "mineBlocks", "args": { "positions": [{"x":1,"y":64,"z":-3}] } }` |
| 「このブロックをここに置いて」 | placeBlock | `{ "type": "placeBlock", "args": { "block": "oak_planks", "position": {"x":2,"y":65,"z":5}, "face": "north", "sneak": true } }` |
| 「たいまつ置いて」 | placeTorch | `{ "type": "placeTorch", "args": { "x": 4, "y": 64, "z": 0 } }` |
| 「私についてきて」 | followPlayer | `{ "type": "followPlayer", "args": { "target": "Taishi", "stopDistance": 2, "maintainLineOfSight": true } }` |
| 「ゾンビを攻撃して」 | attackEntity | `{ "type": "attackEntity", "args": { "target": "zombie", "mode": "melee", "chaseDistance": 6 } }` |
| 「木材を 3 個クラフトして」 | craftItem | `{ "type": "craftItem", "args": { "item": "oak_planks", "amount": 3, "useCraftingTable": false } }` |

入力値は座標の整数性・空文字列の拒否・数量の正数チェックなどでバリデーションし、問題があれば `ActionValidationError`
例外として即座に返します。Mineflayer 側への送信時には `event_level=progress/success/fault` とコマンド ID を `context` に含める
ため、Node 側の構造化ログと突き合わせれば「どのチャット指示がどのペイロードに変換され、どう応答したか」を時系列で確認できます。

#### 3.2.5 自然言語→ActionDirective DSL

`python/planner/graph.py` の LangGraph ノードは Responses API の JSON 応答を `PlanOut` の DSL へ正規化し、以下のフィールドを追加しました。

- `goal_profile` … ゴール要約/カテゴリ/優先度を 1 つの構造体で共有。`success_criteria` にミッション完了条件、`blockers` に既知の障害を列挙します。
- `constraints` / `execution_hints` … 「夜は敵対モブが湧く」「在庫: torch=0」といった制約と、Memory 由来のヒントを配列化。LangGraph → Mineflayer の判断材料になります。
- `directives` … 各 plan ステップと 1:1 で結びつく `ActionDirective`。`executor`（`mineflayer` / `minedojo` / `chat`）と `args.coordinates` を指定すると、Python 側はヒューリスティックをスキップし、指示カテゴリ・座標をそのまま LangGraph へ渡します。
- `recovery_hints` … 直近の障壁や Reflexion プロンプトから引き継いだ教訓。`planner.graph.record_recovery_hints()`（既存の `langgraph_state` エイリアス経由でも可）を通じて再計画ノードへも共有され、同じ失敗の再発を防ぎます。

Python エージェントは directive メタデータを `Actions.begin_directive_scope()` → `_dispatch()` を経由して WebSocket ペイロードの `meta` に添付します。`node-bot/runtime/telemetryRuntime.ts` は `command.meta.directive_id` / `command.meta.executor` を span 属性へ記録し、`mineflayer.directive.received` カウンターとしてメトリクス化するため、OpenTelemetry 上で「どの目的の指示がどの executor へ渡ったか」を直接観測できます。
Directive の解析・メタ生成・スコープ管理は `python/orchestrator/directive_utils.py` に集約し、`AgentOrchestrator` / `PlanExecutor` は薄い委譲のみを保持する構成にしました。ハイブリッド指示のパースや座標抽出も同モジュール経由で共有することで、エントリポイントが 1 つに整理され、監査・保守が容易になります。

#### 3.2.6 周囲状況の即時共有

Node 側が push する `perception` イベントや `gatherStatus(kind=\"environment\")` の結果は Python 側の `Agent._ingest_perception_snapshot()`（`python/agent.py`）で正規化され、`perception_history` と `perception_summary` に蓄積されます。
要約には敵対モブ数・危険ブロック・照度・天候などが 1 行で記録され、LangGraph の状態・LLM プロンプト・障壁通知・ActionGraph のバックログ判断にそのまま利用されます。
`bridge_event_reports` に含まれる attributes（例: job_id, hazard, world）も summary へ取り込まれるため、Paper 側で発生した危険と Mineflayer 近傍の観測値をセットで追跡できるようになりました。


#### 3.2.7 Runtime モジュール構成

LangGraph の責務が増えたため、`python/runtime` 配下に以下のモジュールを切り出しています。依存方向は `runtime/* -> utils` のみになるよう整理し、Agent 本体とは依存注入で接続します。

- `python/runtime/action_graph.py`: LangGraph のノード定義とステート初期化をまとめたモジュール。`ActionGraph`/`UnifiedAgentGraph` がカテゴリごとの処理をハンドオフします。
- `python/runtime/inventory_sync.py`: Mineflayer からの所持品スナップショット取得と要約を担当。`InventorySynchronizer` をコンストラクタ引数に渡すことでオーケストレータの差し替えを容易にします。
- `python/runtime/reflection_prompt.py`: Reflexion 用プロンプト生成を共通化し、再計画ノードから安全に再利用できるようにしたユーティリティ。

#### 3.2.8 ダッシュボード監視（ブラウザ表示）

- Python エージェント起動中に `http://127.0.0.1:9100/`（既定）へアクセスすると、キュー長・現在ロール・最後のプラン要約・perception サマリ・構造化イベント・Reflexion 抜粋が 2 秒間隔で自動更新される簡易モニタを表示します。
- 有効化は `DASHBOARD_ENABLED=true`（既定）で、`DASHBOARD_HOST`/`DASHBOARD_PORT` でバインド先を変更可能。外部公開が不要な場合は 127.0.0.1 のままにしてください。
- `DASHBOARD_ACCESS_TOKEN` を設定すると Bearer 認証を要求します。ブラウザでは `?token=...` を付けてアクセスするか、手元のリクエストに `Authorization: Bearer <token>` を付与してください。
- UI は React/TypeScript で構成し、`/static/app.js` を CDN React とともに配信するだけのシンプル構成です。ビルド不要で、Python プロセスが静的アセットを返します。
- ログは `agent.dashboard` 名前空間へ出力されます。起動に失敗した場合もエージェント自体は稼働し続けるため、本番環境でポート競合が起きても計画実行は止まりません。

#### MineDojo ミッション連携

* Python エージェントは行動タスクの分類結果から MineDojo ミッション ID を推論し、該当カテゴリでは `python/services/minedojo_client.py` を介してミッション情報とデモを取得します。
* 取得したデモは `Actions.play_vpt_actions` に対して自動送信され、Mineflayer 側で低レベル操作を事前ロードします。同時に LLM コンテキストへミッション概要とデモ要約（アクション種別・件数）が注入されるため、計画生成時に具体的な事例を参照できます。
* MineDojo デモを受信すると `Actions.registerSkill` と `SkillRepository` へミッション ID・`minedojo` タグ付きで自動登録し、`mission:<id>` タグをキーに LangGraph 側から即時再利用できるようになりました。曖昧な「もう一度同じミッション」のような指示でもスキル呼び出しへ誘導され、再学習なしでデモ由来スキルを呼び出せます。
* API 経由で MineDojo を利用する場合は `.env`（または環境変数）へ `MINEDOJO_API_KEY` を設定してください。ローカルデータセットを参照する場合は `MINEDOJO_DATASET_DIR` に JSON の配置ディレクトリ（`missions/mission_id.json`・`demos/mission_id.json`）を指定します。
* キャッシュは `MINEDOJO_CACHE_DIR`（既定: `var/cache/minedojo`）へ保存されます。API 応答やデモ軌跡にはライセンス制限・個人データが含まれる可能性があるため、リポジトリへコミットしないでください。`.gitignore` にも除外設定を追加済みです。
* 具体的なディレクトリ構成やデータ利用ポリシーは `docs/minedojo_integration.md` を参照してください。MineDojo 利用規約に従い、商用利用可否や二次配布の扱いをチーム内で確認してください。
* `MINEDOJO_SIM_ENV` / `MINEDOJO_SIM_SEED` / `MINEDOJO_SIM_MAX_STEPS` で模擬環境パラメータを指定できます。`run_minedojo_self_dialogue` から自己対話フローを起動すると、これらの値とミッション/デモ情報を `MineDojoSelfDialogueExecutor` がまとめて参照し、スキル登録や学習実績の更新まで一気通貫で行います。

#### LangSmith トレース

* LangSmith への送信は `LANGSMITH_ENABLED` が `true` のときに有効化され、`LANGSMITH_API_URL` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_TAGS` でエンドポイントやタグを指定します。
* `ThoughtActionObservationTracer` を `MineDojoSelfDialogueExecutor` が内部で利用し、ReAct の Thought/Action/Observation それぞれを親子 Run として送信します。CI ではダミークライアントを差し込んで外部依存なく検証するため、LangSmith が無効でもフロー全体は no-op で安全に実行されます。
* directive メタデータは span 属性（`command.meta.directive_id` / `command.meta.executor` など）にも自動付与され、MineDojo への委譲やチャット専用 directive を LangSmith 側で直接フィルタリングできるようになりました。

#### LangGraph 構造化ログとリカバリー

* `python/utils/logging.py` に構造化ロギングユーティリティを追加し、LangGraph ノード ID・チェックポイント ID・イベントレベルを JSON 形式で出力します。`log_structured_event` を利用すると、ノード固有の `context` メタデータを辞書で渡せます。
* `python/runtime/action_graph.py` の建築ノードはチェックポイント更新時に `action.handle_building` というノード名でログを記録し、`event_level="recovery"` かどうかでクラッシュ復旧か通常進行かを区別します。調達計画や配置バッチもログへ含めるため、資材不足の原因調査が簡単になります。
* `python/bridge_client.py` の HTTP 再試行も構造化ログへ統一し、最終的に失敗した場合は `event_level="fault"` を付けて LangGraph 側の再試行ノード連携に備えます。409 (液体検知) は `BridgeError` の `status_code`/`payload` に保存されるため、Mineflayer へ危険箇所を知らせて自律停止できます。
* `python/agent.py` の ReAct ループは、各ステップの Thought/Action/Observation を `react_step` イベントとして構造化ログに記録し、Mineflayer から得られた実行結果を Observation フィールドへ即座に反映します。
2025 年 2 月時点では `python/runtime/action_graph.py` に LangGraph ベースのステートマシンを導入し、採掘・建築・防衛の
モジュールをノード単位で独立させました。これにより再計画時の分岐が視覚化され、`mine` → `equip` のような連鎖的な
処理もグラフ上で明示されます。同様に `python/planner/graph.py` へ移動した LLM 呼び出しも LangGraph の条件分岐ノードに置き換え、
失敗時は優先度を `high` へ自動昇格、成功時は `normal` へ戻す優先度マネージャーと同期しています。閾値やモデル設定は `python/planner_config.py` に集約したため、環境変数を差し替えるだけでテスト環境でも一貫した挙動を再現できます。
`tests/test_langgraph_scenarios.py` では障害検知・並列進行・優先度遷移を網羅し、グラフ内で再計画が完結することを検証できます。さらに `tests/e2e/test_multi_agent_roles.py` では敵襲来時の防衛介入と補給合流のロール切替を E2E で確認し、共有メモリと役割ステートが期待通り同期されることを保証しています。

#### Reflexion ログの確認手順

* 失敗ステップと再試行結果は `var/memory/reflections.json` に JSON 形式で蓄積されます。Python/Node いずれのプロセスを再起動しても履歴を再利用できるため、長期的な改善状況を追跡できます。
* 直近の反省ログを確認するには以下のように `python -m json.tool` で整形すると見やすいです。

  ```bash
  python -m json.tool var/memory/reflections.json | less
  ```

* 改善提案と再試行結果のみを確認したい場合は `jq ' .entries[] | {failed_step, improvement, retry_result}' var/memory/reflections.json` のように抽出してください。
* plan() へ渡された要約は Python 側の `memory` ロガーに `recent_reflections` として出力されます。直近の Reflexion プロンプト原文は `last_reflection_prompt` キーに保存され、次回計画時のヒントとして利用されます。

Mineflayer から `ok=false` が返った場合は、障壁内容を `compose_barrier_notification` で LLM に共有し、
プレイヤーへチャット通知したのち自動的に再計画を依頼します。失敗ステップと残りの計画案をまとめて LLM に渡すため、
例えば「採掘が拒否された→ツール装備プランを立て直す」といった自律的なリカバリーが可能です。障壁発生時も既存の
検出レポートや backlog 通知が重複しないよう制御しており、チャットが過剰に騒がしくならないよう設計しています。

Python エージェントが呼び出す LLM は **OpenAI Responses API** を利用しています。従来の Chat Completions API では
`reasoning` パラメータが拒否されるため、Responses API の `reasoning.effort` と `text.verbosity` を併用し、
gpt-5 系モデルに対して安定した JSON 応答と推論強度の指定を両立させています。

#### スキルライブラリの活用

- Python 側では `python/skills/seed_library.json` を初期値として読み込み、`SKILL_LIBRARY_PATH`（既定: `var/skills/library.json`）で指定した JSON に学習済みスキルの使用履歴を永続化します。
- LangGraph のアクションノードはスキル名/カテゴリから既知スキルを検索し、再生可能な場合は `invokeSkill` コマンドを Mineflayer へ送信します。未習得の場合は `skillExplore` で探索モードへ切り替え、獲得候補を構造化ログへ残します。
- Mineflayer 側では `registerSkill` / `invokeSkill` / `skillExplore` コマンドを受け取り、`SKILL_HISTORY_PATH`（既定: `var/skills/history.ndjson`）にスキル獲得履歴を NDJSON で追記します。ログは `level/event/context` の構造化形式で `stdout` にも出力されるため、可観測性ツールへの取り込みが容易です。

### 3.3 Docker Compose（Python + Node 同時ホットリロード）

開発時に Python エージェントと Node ボットの両方をホットリロードで動かしたい場合は、プロジェクトルートに追加した `docker-compose.yml` を利用できます。

```bash
cp env.example .env  # まだ .env が無い場合
docker compose up --build
```

* Node サービスは `npm run dev`（`tsx` を利用）で TypeScript ソースの変更を検知し、自動的に再起動します。
* Python サービスはプロジェクトルートから `watchfiles --filter python --ignore-paths .venv -- "python -m python"` を実行します。`cd python` して実行すると `ModuleNotFoundError: No module named python` が発生し、WebSocket の待受が起動しません。
* `PYTHONPATH` は `/app:/app/python` を既定にしています。起動ログに `sys_path` と `pythonpath` が出力されるので、`runtime` モジュールが解決できているかを確認してください。
* 起動時にカレントディレクトリ・`sys.path`・`AGENT_WS_*` / `WS_URL` を JSON ログとして出力するので、モジュール解決や接続先が意図どおりかを最初に確認してください。
* CLI の仕様上、`watchfiles -- ...` に渡すコマンドは `"python -m python"` のように 1 引数へクォートしてください。クォート漏れは対話モード起動となりポートをリッスンせず、Node 側が `ECONNREFUSED` を出します。
* ホットリロード環境では依存ライブラリをコンテナ起動時に自動インストールするため、初回起動時は少し時間がかかります。
* Docker Compose は `host.docker.internal` をコンテナの hosts に追加しています。Windows / WSL / macOS から Paper サーバーを起動している場合でも、ボットがホスト OS 上の `25565` ポートへ接続できます。
* Node.js サービス用コンテナは `node:22` を採用し、最新の Mineflayer 系ライブラリが要求するエンジン条件を満たして `minecraft-protocol` の PartialReadError（`entity_equipment` の VarInt 解析失敗）を防止します。
* Python エージェントが `AGENT_WS_PORT` をリッスンし、Node 側が `AGENT_WS_URL` で指定した経路からチャットを転送します。Docker Compose では既定で `ws://python-agent:9000` に接続します。

* `.env` に `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` / `OTEL_RESOURCE_ENVIRONMENT` を指定すると、Node/Python 双方が Collector へ span/metric を送信します。Collector をホスト OS で動かす場合は既定値 `http://host.docker.internal:4318` をそのまま利用できます。
#### 3.3.1 1.21.x の PartialReadError 追加対策

- Paper / Vanilla 1.21.4 以降では ItemStack (Slot 型) に optional NBT が 2 セクション追加され、旧定義のままでは `entity_equipment` パケットで 2 バイトの読み残しが発生します。
- `node-bot/runtime/slotPatch.ts` で `customPackets` 用の Slot 定義を動的に生成し、1.21 ～ 1.21.x 系の亜種をまとめて上書きすることで `PartialReadError: Unexpected buffer end while reading VarInt` を解消しています。minecraft-data のバージョン一覧から自動検出しているため、新しい 1.21.x がリリースされても追従漏れを起こしません。
- 1.21.3 以前ではこれらのフィールドが送られないため、option タイプの 0 バイトだけが届き互換性が維持されます。
- `.env` で `MC_VERSION=1.21.1` のように **minecraft-data が認識するプロトコルラベル** を指定すると、Mineflayer がサーバーと同じ定義で通信を開始するため、`PartialReadError` の再発リスクを減らせます。未設定時は既定で 1.21.1 を採用し、未知の値が入力された場合は対応可能なバージョンへ自動フォールバックします。

### 3.4 AgentBridge HTTP プラグイン

`bridge-plugin/` ディレクトリに Paper 用の HTTP ブリッジプラグイン（AgentBridge）を追加しました。WorldGuard・CoreProtect と連携して継続採掘ジョブのリージョン管理やバルク環境評価を提供します。

1. `bridge-plugin/libs/` に CoreProtect の jar を配置します（`.gitkeep` のみコミット済み）。
2. Java 21 + Gradle を用意し、プラグイン直下で `./gradlew shadowJar` を実行すると `build/libs/AgentBridge-0.1.0.jar` が生成されます。
3. Paper サーバーの `plugins/` へ配置し、初回起動後に生成される `plugins/AgentBridge/config.yml` の `api_key` を `.env` の `BRIDGE_API_KEY` と一致させます。`api_key` が空や `CHANGE_ME` のままの場合は HTTP サーバーを起動せず、プラグインを自動的に無効化します。

HTTP サーバーは `config.yml` の `bind` / `port` で調整でき、`GET /v1/health` にアクセスすると WorldGuard/CoreProtect の有効状態を確認できます。`POST /v1/jobs/*` 系エンドポイントは必ず `X-API-Key` ヘッダーで保護してください。認証ヘッダーがない要求はすべて拒否され、api_key が設定されていない状態ではサーバー自体が立ち上がりません。
`langgraph.retry_endpoint` を設定すると、`POST /v1/events/disconnected` で接続断が通知された際に LangGraph リトライノードを HTTP 経由で呼び出し、Paper 側のログへノード ID とチェックポイント ID を構造化出力します。

`events.stream_enabled` を `true` にすると、`/v1/events/stream` で SSE によるイベント配信を有効化します。ジョブ開始・フロンティア更新・WorldGuard リージョン削除の進捗に加え、採掘領域内の液体検知や機能ブロック接近を `event_level` / `region` / `block_pos` 付きで push します。SSE も通常の HTTP API と同様に `X-API-Key` が必須で、`keepalive_seconds` 間隔で `event: keepalive` が送信されます。
Python 側では `BRIDGE_EVENT_STREAM_ENABLED` が `true` の場合に自動購読し、受信イベントを `detection_reports` として記憶・再計画プロンプトへ統合します。`.env` の `BRIDGE_EVENT_STREAM_PATH` や `BRIDGE_EVENT_STREAM_RECONNECT_DELAY` で経路とリトライ間隔を調整できます。

#### 3.4.1 Bridge 準備手順（ダウンロード～起動まで）

1. 必要なダウンロード
   - **Java 21 JDK**（Adoptium など公式配布物）。
   - **Paper サーバー jar**（あなたのサーバーバージョンに合うもの。例: 1.21.1）。Paper 配下に `plugins/` フォルダを作成しておく。
   - **WorldEdit jar**（WorldGuard の前提プラグイン）。https://enginehub.org/worldedit から「Bukkit (1.14+)」版をダウンロードし、Paper の `plugins/` へ配置。例: `worldedit-bukkit-7.3.x.jar`。
   - **WorldGuard jar**（リージョン保護機能を提供）。https://enginehub.org/worldguard から「Bukkit」版をダウンロードし、Paper の `plugins/` へ配置。例: `worldguard-bukkit-7.0.x.jar`。
   - **CoreProtect jar**（例: `CoreProtect-22.0.jar` を公式配布ページから取得し、`bridge-plugin/libs/` へ配置）。`build.gradle.kts` はこのファイル名を参照するためリネームしない。
   - **Gradle 本体**（Wrapper は同梱していないため、手元にインストールが必要。Gradle 9 以降を推奨 ― Shadow 9.x と組み合わせると `shadowJar` が安定します。Windows なら winget/choco、macOS なら brew、Linux なら各ディストリのパッケージか公式 ZIP を展開）。

   > **バージョン互換性の目安（2024年12月時点）**
   > | Minecraft | WorldEdit | WorldGuard |
   > |-----------|-----------|------------|
   > | 1.21.x    | 7.3.x     | 7.0.x      |
   > | 1.20.x    | 7.2.x     | 7.0.x      |
2. ビルド（AgentBridge jar を作る）
   - `cd bridge-plugin`
   - `gradle shadowJar`
   - 成果物: `bridge-plugin/build/libs/AgentBridge-*.jar`
3. 配置
   - 生成した `AgentBridge-*.jar` を Paper サーバーの `plugins/` にコピー。
   - Paper 自体は通常どおり起動できる場所に jar と `server.properties` などを置いておく。
4. Paper 起動と設定
   - Paper を起動すると `plugins/AgentBridge/config.yml` が生成される。
   - `api_key` に十分長いランダム値を入れ、`.env` の `BRIDGE_API_KEY` と同じ値にする。空や `CHANGE_ME` のままでは HTTP サーバーが起動しない。
   - Docker からホスト OS 上の Paper へ繋ぐ場合は `bind: 0.0.0.0` / `port: 19071` を推奨し、`.env` の `BRIDGE_URL` は `http://host.docker.internal:19071` にする（Paper が別ホストならその IP/ホスト名に置き換える）。
   - SSE を使わない場合は `events.stream_enabled: false`、Python 側も `.env` で `BRIDGE_EVENT_STREAM_ENABLED=false` にしておくと接続リトライのログを抑制できる。
5. 動作確認
   - Paper コンソールに AgentBridge 起動ログが出ていることを確認。
   - 健康チェック: `curl -H "X-API-Key: <BRIDGE_API_KEY>" http://<bridge-host>:19071/v1/health` が 200 を返す。
6. Python 側環境変数（`.env`）
   - `BRIDGE_URL`: Paper へ到達可能な URL（Docker→ホストなら `http://host.docker.internal:19071`）。
   - `BRIDGE_API_KEY`: `config.yml` の `api_key` と同じ値。
   - Bridge をまだ使わない場合は `BRIDGE_EVENT_STREAM_ENABLED=false` にしておく。

#### 3.4.2 Docker Compose で AgentBridge を立ち上げる

Paper サーバーごとコンテナ化したい場合は、同梱の `docker-compose.yml` に `bridge` サービスを追加しています。以下の手順で利用できます。

1. CoreProtect の jar を `bridge-plugin/libs/CoreProtect-22.0.jar` に配置する。
2. AgentBridge をビルド: `cd bridge-plugin && gradle shadowJar`。生成物 `build/libs/AgentBridge-*.jar` は自動でコンテナの `/data/plugins` にマウントされる。
3. `.env` を更新: `MC_HOST=bridge`、`BRIDGE_URL=http://bridge:19071`（デフォルト値もこの組み合わせに合わせてあります）。
4. 起動: プロジェクトルートで `docker compose up --build`。初回起動時に `bridge-data/plugins/AgentBridge/config.yml` が生成されるので、`api_key` を `.env` の `BRIDGE_API_KEY` と揃える。
5. データ永続化: `bridge-data/` にワールドとプラグイン設定が保持されます（`.gitignore` 済み）。
6. ホストからワールドへ直接入らない場合は 25565 ポート公開を外しています。外部から接続したい場合は `docker-compose.yml` の `bridge` サービスで `ports` に `25566:25565` などのマッピングを追加してください（25565 が埋まっている環境が多いためホスト側をずらす運用を推奨）。

### 3.5 継続採掘モード CLI

Python 側に `python/cli.py` を追加し、継続採掘ジョブを CLI から起動できるようにしました。

```bash
# 明示的に方向を指定する
python -m python.cli tunnel --world world --anchor 100 12 200 --dir 1 0 0 --section 2x2 --len 64 --owner Taishi
# 近傍評価から自動で方向を推定する
python -m python.cli tunnel --world world --anchor 100 12 200 --dir auto --section 2x2 --len 64
```

`--dir` にはカードinal方向ベクトル（例: `1 0 0`）を直接渡すか、`auto` を指定すると Paper 側の AgentBridge から取得した `bulk_eval` / `is_player_placed_bulk` の結果をもとに安全な東西南北を自動推定します。自動モードでは液体や WorldGuard の機能ブロックに近い方向をペナルティ化し、スコアが最も高い方向を CLI が表示します。ジョブ開始後は AgentBridge 経由でバルク環境評価と CoreProtect チェックを行い、Mineflayer には `mineBlocks` / `placeTorch` コマンドを送信します。`.env` に追加した `BRIDGE_URL` などの変数で接続先やたいまつ間隔を調整できます。

AgentBridge からの危険通知やジョブ状態をチャットレスで追跡したい場合は、SSE ベースのウォッチャーを用意しました。

```bash
python -m python.cli agentbridge jobs watch --danger-only --format text
```

`--job-id` でジョブ単位にフィルタし、`--danger-only` で `warning`/`fault` レベルのみを表示可能です。`--format json` を指定すると `jq` などにパイプできるため、blazity 流の CLI から Paper の危険検知タイムラインを即座に確認できます。

## 4. .env

`env.example` を `.env` にコピーしてから、下記のカテゴリごとに値を調整してください。表にない項目は既定値のままでも安全に起動できますが、利用する機能に応じて明示設定を推奨します。

### 4.1 OpenAI / プランナー
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_TEMPERATURE`, `OPENAI_VERBOSITY`, `OPENAI_REASONING_EFFORT`  
  Responses API の接続情報と推論パラメータ。gpt-5-mini のような温度固定モデルでは `OPENAI_TEMPERATURE` を送信しないため、値を入れてもログに警告が出るだけで無視されます。
- `LLM_TIMEOUT_SECONDS`  
  Responses API 呼び出しを強制的に打ち切る秒数。デフォルトは 30 秒。
- `PLAN_CONFIDENCE_REVIEW_THRESHOLD`, `PLAN_CONFIDENCE_CRITICAL_THRESHOLD`  
  LangGraph の `pre_action_review` ノードが自動確認に切り替わる確信度しきい値。前者はチャット確認へ誘導する境界値、後者は必ず確認を挟む危険域を定義します。

### 4.2 LangSmith / 可観測性
- `LANGSMITH_ENABLED`, `LANGSMITH_API_URL`, `LANGSMITH_PROJECT`, `LANGSMITH_API_KEY`, `LANGSMITH_TAGS`  
  LangSmith トレース送信のフラグと接続先。
- `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER_RATIO`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ENVIRONMENT`  
  OpenTelemetry の送信先とサンプリング率。Collector が無い環境ではデフォルト (`http://localhost:4318`, ratio=1.0) のままでも問題ありません。

### 4.3 Python ↔ Node WebSocket / キュー設定
- `WS_URL`, `WS_HOST`, `WS_PORT`  
  Node（Mineflayer）が公開する WebSocket（Python→Node のコマンド送信用）。
- `AGENT_WS_HOST`, `AGENT_WS_PORT`, `AGENT_WS_URL`  
  Python エージェントが受信する WebSocket（チャット/イベント送信用）。ホスト/ポート指定に失敗すると Docker 検知ロジックで `python-agent:9000` へフォールバックします。
- `AGENT_WS_CONNECT_TIMEOUT_MS`, `AGENT_WS_SEND_TIMEOUT_MS`, `AGENT_WS_HEALTHCHECK_INTERVAL_MS`, `AGENT_WS_RECONNECT_DELAY_MS`, `AGENT_WS_MAX_RETRIES`  
  Mineflayer から Python への接続維持ポリシー。
- `AGENT_EVENT_BATCH_INTERVAL_MS`, `AGENT_EVENT_BATCH_MAX_SIZE`, `AGENT_EVENT_QUEUE_MAX_SIZE`  
  Node 側で multi-agent イベントをまとめて送信する際のバッチング設定。
- `AGENT_QUEUE_MAX_SIZE`, `WORKER_TASK_TIMEOUT_SECONDS`  
  Python 側のチャット処理キューの上限と、1 タスクあたりの最大処理時間。
- `DEFAULT_MOVE_TARGET`  
  LLM プランで座標が省略された移動ステップのフォールバック座標（例: `0,64,0`）。
- `STRUCTURED_EVENT_HISTORY_LIMIT`, `PERCEPTION_HISTORY_LIMIT`  
  LangGraph へ保持する構造化イベント/認知スナップショットの件数。
- `PERCEPTION_ENTITY_RADIUS`, `PERCEPTION_BLOCK_RADIUS`, `PERCEPTION_BLOCK_HEIGHT`, `PERCEPTION_BROADCAST_INTERVAL_MS`  
  Mineflayer が push する `perception` イベントのスキャン範囲。狭い坑道ほど値を下げるとコストを抑えられます。
- `LOW_FOOD_THRESHOLD`  
  Bot の満腹度がこの値を下回ったら LangGraph が警告を付与し、補給タスクを優先します。

### 4.4 Mineflayer / Minecraft 実行環境
- `MC_HOST`, `MC_PORT`, `MC_VERSION`, `MC_RECONNECT_DELAY_MS`  
  Paper サーバーへの接続先と再接続ポリシー。Docker で `localhost` を指定すると自動的に `host.docker.internal` へ置き換わります。
- `BOT_USERNAME`, `AUTH_MODE` (`offline` / `microsoft`)  
  ボットの表示名と認証方式。
- `CONTROL_MODE` (`command` / `vpt` / `hybrid`), `VPT_TICK_INTERVAL_MS`, `VPT_MAX_SEQUENCE_LENGTH`  
  VPT 再生と通常コマンドの切り替え方。`hybrid` では LangGraph の directive 単位で VPT を差し込めます。
- `SKILL_LIBRARY_PATH`, `SKILL_HISTORY_PATH`  
  Python 側のスキルライブラリ JSON と、Mineflayer 側の NDJSON ログの保存パス。共有ドライブを使う場合はここで一元管理します。

### 4.5 AgentBridge / Paper 連携
- `BRIDGE_URL`, `BRIDGE_API_KEY`, `BRIDGE_HTTP_TIMEOUT`, `BRIDGE_HTTP_RETRY`  
  Paper の HTTP プラグイン（AgentBridge）へ接続するためのエンドポイントと再試行ポリシー。
- `BRIDGE_EVENT_STREAM_ENABLED`, `BRIDGE_EVENT_STREAM_PATH`, `BRIDGE_EVENT_STREAM_RECONNECT_DELAY`  
  SSE ベースの危険通知ストリームを購読する際の設定。チャットレス運用時もジョブ進捗や液体警告を push で受け取れます。

### 4.6 MineDojo / シミュレーション
- `MINEDOJO_API_BASE_URL`, `MINEDOJO_API_KEY`, `MINEDOJO_DATASET_DIR`, `MINEDOJO_CACHE_DIR`, `MINEDOJO_REQUEST_TIMEOUT`  
  MineDojo API とローカルデータセットの配置。API キーを空にするとローカル JSON のみ参照します。
- `MINEDOJO_SIM_ENV`, `MINEDOJO_SIM_SEED`, `MINEDOJO_SIM_MAX_STEPS`  
  自己対話シミュレーション（` MineDojoSelfDialogueExecutor`）の既定パラメータ。

### 4.7 トンネルモード / CLI
- `TUNNEL_TORCH_INTERVAL`, `TUNNEL_FUNCTIONAL_NEAR_RADIUS`, `TUNNEL_LIQUIDS_STOP`, `TUNNEL_WINDOW_LENGTH`  
  `python/cli.py tunnel` コマンドの既定値。たいまつ間隔や液体検知で停止するかどうかを環境ごとに変更できます。

### 4.8 OpenTelemetry / LangGraph の補足
LangGraph のノード実行、Responses API 呼び出し、AgentBridge HTTP 通信では OpenTelemetry の span を自動で開始します。`OTEL_EXPORTER_OTLP_ENDPOINT` と `OTEL_TRACES_SAMPLER_RATIO` を設定すると、`langgraph_node_id` や `checkpoint_id` を含むトレースを収集できます。Mineflayer 側も WebSocket 受信ループや `gatherStatus` / `invokeSkill` コマンドを span・メトリクスに記録するため、Collector が OTLP/HTTP を受け付ける状態で起動すれば、実行時間ヒストグラムや再接続カウンターをまとめて可視化できます。

### 4.9 ダッシュボード (HTTP モニタ)
- `DASHBOARD_ENABLED` … true で HTTP ダッシュボードを起動（既定 true）
- `DASHBOARD_HOST` … バインドアドレス（既定 127.0.0.1）
- `DASHBOARD_PORT` … バインドポート（既定 9100）
- `DASHBOARD_ACCESS_TOKEN` … 任意。設定すると Bearer 認証を要求。ブラウザは `?token=...` 付きでアクセスするか、ヘッダーに `Authorization: Bearer <token>` を付与してください。
- フロントエンドは React/TypeScript で実装し、CDN の React UMD 版 + `/static/app.js` を読み込むだけで動作します。

## 5. 使い方（ゲーム内）

プレイヤーがチャットで日本語の自然文を送ります。例：

* 「パンが無い」 → 小麦収穫→パン作成→手渡し or チェスト格納
* 「鉄が足りない」 → ツール確認→採掘計画→ブランチマイニング
* 「ついてきて」 → 追尾モード
* 「ここに小屋を建てて」 → 建材確認→不足なら収集→建築
* 「現在値教えて」 → Mineflayer が現在位置 (X/Y/Z) をチャットで即座に報告

ボットは進捗を日本語で逐次報告します。

### 5.1 デバッグログでチャット処理を追跡する

Paper サーバーでチャットを送信した際に「何も起こらない」ケースでも、Docker/コンソール上のログを確認すれば、
次の観点でフローを把握できます。

1. **Node（Mineflayer）**: `node-bot` の標準出力に `[Chat] <player> メッセージ` が記録され、
   直後に `[ChatBridge] ...` ログが続き、Python エージェントへチャットを転送した結果を確認できます。
   WebSocket 経由のコマンド受信時は `id=...` 付きの詳細ログが表示され、どの負荷元からどのコマンドが届き、
   どのレスポンスを返したか追跡できます。
2. **Python エージェント**: `python-agent-1` のログに `WS send/recv`、`queue chat`、`moveTo` などの INFO ログが出力され、
   LLM からの計画生成と Mineflayer への指示送出の成否を即座に確認できます。2025 年 10 月時点では、
   `command[001]` のようなコマンド ID 付きでチャット/移動コマンドの送信・応答・処理時間が逐次表示されるほか、
   プランニング結果の各ステップ（`plan_step index=1/3 ...`）がログへ詳細記録されるため、
   「どのステップをどの条件で実行／スキップしたか」を追跡可能です。Mineflayer 側のログと突き合わせれば、
   問題発生時の原因を秒単位で切り分けられます。
   さらに未実装ステップや Mineflayer 側で拒否されたアクションを検知した場合は、ゲーム内チャットで
   「手順〇〇で問題が発生しました」と即時通知し、Python ログにも `execution barrier detected` 警告を
   出力するため、プレイヤーへのフィードバックと開発者の原因特定が容易になりました。障壁が同じ
   ステップで繰り返し発生しても毎回チャットとログへ通知され、どの操作が停滞しているかを逐次
   把握できます。また、LLM 計画から座標を抽出できず既定座標へ移動した場合も、「指示に座標が含まれ
   ていない」旨を即座に知らせるため、座標指定の不足にすぐ気付けます。加えて、障壁通知メッセージは
   直前のログ状況や記憶している座標情報を LLM へ送り、丁寧な日本語で確認事項をまとめた上でプレイヤー
   へ送信するため、チャット内容がより状況に即した文面になります。

これらのログを突き合わせることで、「チャットが受信されたか」「Python 側がコマンドを送ったか」「Mineflayer が応答したか」を
時系列で把握でき、問題の切り分けが容易になります。

### 5.2 OpenAI 設定で温度を変更したい場合

一部の OpenAI モデル（特に gpt-5-mini 系）は API 側で温度が固定されており、`temperature` パラメータを送信するとリクエストが拒否されます。
本リポジトリでは `OPENAI_TEMPERATURE` を設定しても、そのモデルが温度変更不可であれば自動的に送信を抑止し、警告ログを出力します。

### 5.3 レッドストーン採掘の自律行動

2025 年 10 月時点で、Python エージェントは「レッドストーンを集めて」のような採掘指示を受けると、
LLM 計画の中に含まれる「採掘」ステップを自動的に `mineOre` コマンドへ変換して Node 側の Mineflayer へ送ります。
Mineflayer は周囲 12 ブロック（指示文に「広範囲」等が含まれる場合は最大 18 ブロック）をスキャンし、
`redstone_ore` / `deepslate_redstone_ore` を優先的に探索します。見つかった場合は自動で接近→掘削を行い、
結果は Python ログとゲーム内チャットへ反映されます。採掘ログは `[MineOreCommand] mined ...` として出力されるため、
実際にどの座標でどの鉱石を回収したかを追跡可能です。採掘対象が見つからなかった場合や Mineflayer が移動に失敗した場合は、
`Target ore not found within scan radius` 等のエラーが返り、Python 側では障壁通知としてプレイヤーへ状況が共有されます。

- **温度変更が許可されているモデルに切り替える**: `OPENAI_MODEL` を温度可変モデルに変更すると、`OPENAI_TEMPERATURE` の値が 0.0～2.0 の範囲で反映されます。
- **無効な温度指定をした場合**: 数値以外や範囲外の値を設定すると、INFO/ WARNING ログにフォールバックの旨が表示され、既定値 `0.3` が自動的に利用されます。
- **デバッグ方法**: `python/planner/__init__.py` / `python/llm/client.py` のログに `OPENAI_TEMPERATURE` を無視した理由や、フォールバックが発生した詳細が記録されるため、再発防止に活用してください。

温度変更に失敗する場合は、まずログに出力される警告メッセージを確認し、`OPENAI_MODEL` と `OPENAI_TEMPERATURE` の組み合わせがサポート対象か見直してください。

### 5.3 自動移動の許容範囲と空腹対策

2025 年 10 月時点の Mineflayer ではブロックへ完全一致しないと移動完了とみなされず、段差や水流の影響で「目的地付近に到達しているのに失敗扱い」になるケースが散見されました。本リポジトリでは `moveTo` コマンドを受け取った際に `GoalNear` を採用し、目的座標から ±3 ブロック以内（環境変数 `MOVE_GOAL_TOLERANCE` で 1～30 に調整可能）なら完了として扱います。これにより、プレイヤー指定の XYZ 座標へ到達したにもかかわらず障壁報告が発生する不具合を低減できます。

さらに、ボットが飢餓ダメージを受け始めたタイミング（満腹度 0 の状態）ではインベントリを自動確認し、食料を所持していればその場で装備・摂取してスタミナを回復します。食料が存在しない場合は「空腹ですが食料を所持していません。補給をお願いします。」とチャット通知するため、プレイヤーが補給すべきタイミングを把握しやすくなります。

### 5.4 座標移動指示への即応

- プレイヤーが「X=-36, Y=73, Z=-66」や「{XYZ: -36 / 73 / -66}」といった多様な記法で座標を提示しても、Python エージェント側で正規表現を拡充して自動抽出します。従来のように既定座標へ退避したり、同じ座標の再提示をお願いする頻度が大幅に減りました。
- 元チャットから抽出した座標をプラン実行開始時点で保持し、LLM の出力ステップに数値が含まれなくてもそのまま移動を開始します。これにより「今から向かってよいか？」といった許可確認を挟まず、指示を受け取った直後に移動コマンドを Mineflayer へ送出できます。
- 位置確認やインベントリ確認だけを行うメタ的なステップは自動でスキップし、移動や採取などの実行ステップを優先するため、チャットへの応答が冗長にならずテンポよく行動を開始できます。
- 段差や足場の処理を促す抽象的な文でも「移動継続」のヒントとして扱い、既知の座標へ向かう行動を止めないヒューリスティックを導入しました。Mineflayer の pathfinder が吸収できる範囲は確認待ちを挟まずに処理されるため、「具体的な段差処理方法を教えてほしい」といった追加質問が大幅に減ります。


### 5.5 検出報告タスクの整理

- 現在位置や所持品など、アクションを伴わずに状況を報告するだけの指示は Python エージェント内で「検出報告タスク」として切り分けました。これにより、進捗報告テンプレートが誤って挿入されるケースを防ぎ、質問→回答という自然なチャットフローを維持します。
- LLM 応答が既に丁寧な返答を生成している場合は追加メッセージを抑制し、重複応答を避けながら Node 側の即時レポートと矛盾しないようにしています。検出報告タスクの内容は `Memory` に記録され、後続ステップで同じ確認を繰り返さないためのヒントとして活用されます。
- `gatherStatus` コマンド経由で現在位置・所持品・体力/満腹度と掘削許可を自動取得し、要約文をそのままプレイヤーへ共有します。これにより「座標を教えて」「ツルハシを持っているか」などの再質問が不要になり、採掘指示を受け取った直後に準備状況を報告した上で作業へ移行できます。


### 5.6 Paper サーバーの警告 (`HelperBot moved wrongly!` / `minecraft:movement_speed`) への方針

- Mineflayer の移動制御ではパルクールやダッシュをあえて有効化し、危険地帯での生存性と高機動を優先します。`HelperBot moved wrongly!` 警告が発生する場合がありますが、アクロバットなルート選択を阻害しないことを重視した意図的な設定です。サーバーが強制的に位置補正を行った場合でも、直近の目的地を再セットして移動を継続します。
- OpenAI からの移動命令が継続している際に `forcedMove` が発生した場合は、1 秒以内の連続補正を無視しつつログへ警告を残します。これによりプレイヤーはサーバーが移動を補正した事実を把握できます。
- 1.21.1 以降で属性名が `minecraft:generic.movement_speed` へ統一されたため、Mineflayer が旧名 `minecraft:movement_speed` を送出しても自動で置き換え、Paper 側の `Ignoring unknown attribute` 警告を防ぎます。
## 6. 参考理論（READMEにURLを**必ず**記載）

本プロジェクトは以下の理論/手法を採用します。**各論文のURLを本節に列挙してください。** 研究成果が適用される処理フローは README 冒頭のアーキテクチャ記述と同じくプレイヤーチャット起点の 3 層構造で整理しており、社内 Wiki「Architecture/Minecraft-Agent-2025Q2」に掲載している参照順序と揃えています。

| 理論/手法 | 主な狙い | 主な適用モジュール/ドキュメント | 現状適用度 |
| --- | --- | --- | --- |
| Voyager | LLM 主導での自律探索・ツール発見 | `python/planner/graph.py` / `python/planner_config.py` / `python/memory.py` / `docs/building_state_machine.md` | 部分対応 |
| ReAct | 推論 (Reason) と行動 (Act) の往復で環境を制御 | `python/agent.py` / `python/planner/graph.py` / `python/actions.py` | 実装済み |
| Reflexion | 失敗経験を自己評価して行動計画へ反映 | `python/utils/logging.py` / `python/runtime/reflection_prompt.py` / `python/runtime/action_graph.py` / `tests/test_langgraph_scenarios.py` | 部分対応 |
| VPT | 操作シーケンスの模倣学習による政策獲得 | `node-bot/bot.ts` / `node-bot/runtime/roles.ts` | 未対応 |
| MineDojo | Minecraft タスクの大規模データセット化 | `docs/tunnel_mode_design.md` / `tests/e2e/test_multi_agent_roles.py` | 部分対応 |

#### 研究適用フロー（社内 Wiki と共通の参照順）

1. **プレイヤーチャット受付**（`node-bot/bot.ts`）: ReAct の「Act」フェーズとして、Mineflayer がチャットを検知し環境観測を添えて Python へ転送します。
2. **LLM プランニング**（`python/planner/__init__.py` / `python/planner/graph.py`）: Voyager の探索指針と MineDojo のタスク分類をもとに LangGraph 内でステップを組み立て、Reason フェーズの思考をログ化します。
3. **アクション合成・実行**（`python/actions.py` → `node-bot/runtime/roles.ts`）: ReAct の決定結果を具体的な Mineflayer コマンドへ落とし込み、VPT で想定する操作トレースに近い粒度で命令を分解します。
4. **自己評価と再計画**（`python/runtime/reflection_prompt.py` / `python/runtime/action_graph.py`）: Reflexion の考え方で失敗ログを振り返り、必要に応じて Voyager 流の探索プランを再生成します。

#### Voyager — [https://arxiv.org/abs/2305.16291](https://arxiv.org/abs/2305.16291)

Voyager は Minecraft のようなオープンワールドで LLM に継続的な技能学習を促すため、行動履歴のライブラリ化と自律的なタスク発見を組み合わせた枠組みを提案しています。本プロジェクトでは LangGraph を通じてタスク分解とチェックポイントを管理し、建築ジョブや採掘ジョブを再利用できる形で蓄積することで、Voyager が示した「技能カタログ化」の利点を部分的に取り込んでいます。

- 関連モジュール: [`python/planner/__init__.py`](python/planner/__init__.py), [`python/planner/graph.py`](python/planner/graph.py), [`python/planner_config.py`](python/planner_config.py), [`python/memory.py`](python/memory.py), [`docs/building_state_machine.md`](docs/building_state_machine.md)
- 今後の改善ポイント: スキルライブラリの自動タグ付けと成功条件の定量評価を導入し、未達タスクの再挑戦優先度を自動算出できるようにする。

#### ReAct — [https://arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)

ReAct は言語モデルに推論（Reason）と行動（Act）の交互実行をさせることで、環境に応じた柔軟な意思決定を実現する手法です。本プロジェクトのチャット解析は ReAct を前提に、LLM が状況説明と行動コマンドを交互に生成し、その結果を Python エージェントが構造化ログとして保持して次の判断に反映する設計になっています。

- 関連モジュール: [`python/agent.py`](python/agent.py), [`python/planner/graph.py`](python/planner/graph.py), [`python/actions.py`](python/actions.py)
- 今後の改善ポイント: Reason フェーズで取り扱う観測情報を Node 側のバイタルデータと統合し、Act の出力に安全制約を事前付与する仕組みを整える。

#### Reflexion — [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366)

Reflexion は失敗体験を言語モデル自身が振り返り、学習した方策を自己修正するアプローチです。本プロジェクトでは構造化ログと LangGraph の再計画ノードを通じて、障害発生時の原因・対応履歴を記録し、次のプラン生成で参照できるようにすることで Reflexion の自律改善を部分的に実現しています。

- 関連モジュール: [`python/utils/logging.py`](python/utils/logging.py), [`python/runtime/reflection_prompt.py`](python/runtime/reflection_prompt.py), [`python/runtime/action_graph.py`](python/runtime/action_graph.py), [`tests/test_langgraph_scenarios.py`](tests/test_langgraph_scenarios.py)
- 今後の改善ポイント: LLM が自己評価を行う際の成功/失敗ラベルを定量化し、再計画時に重み付けされたメモリ検索を行う仕組みを追加する。

#### VPT — [https://arxiv.org/abs/2206.04615](https://arxiv.org/abs/2206.04615)

VPT (Video PreTraining) は実プレイ映像と入力操作ログから模倣学習を行い、ゲーム内操作を一般化する方法論です。現状のボットは LLM による高レベル計画を Mineflayer のプリミティブへ写像しているのみで、操作シーケンスの模倣学習は未導入です。

- 関連モジュール: [`node-bot/bot.ts`](node-bot/bot.ts), [`node-bot/runtime/roles.ts`](node-bot/runtime/roles.ts)
- 今後の改善ポイント: 行動トレースを収集するロガーを追加し、将来的に VPT 互換のデータセットへ出力して Mineflayer の低レベル政策を強化できるようにする。

#### MineDojo — [https://arxiv.org/abs/2206.08853](https://arxiv.org/abs/2206.08853)

MineDojo は Minecraft の多様なタスクを大規模データセットとして整理し、汎用エージェントの学習を支援するプラットフォームを提案しています。本プロジェクトでは MineDojo のタスク分類を参照しつつ、継続採掘やマルチエージェント協調といった自前のジョブ設計を docs 配下に集約し、テストスイートでカテゴリごとの期待結果を検証する運用を行っています。

- 関連モジュール: [`docs/tunnel_mode_design.md`](docs/tunnel_mode_design.md), [`tests/e2e/test_multi_agent_roles.py`](tests/e2e/test_multi_agent_roles.py)
- 今後の改善ポイント: MineDojo の報酬設計を再評価し、LangGraph の優先度マネージャーにタスクカテゴリ別の KPI を組み込んで進捗を可視化する。

## 4. VPT 操作再生モード

Mineflayer 側で低レベル操作を逐次再生するため、VPT (Video PreTraining) モデルの推論結果をそのまま入力として扱える経路を整備しました。Python 側で観測値を収集して VPT 互換の特徴量へ整形し、Node.js 側が `setControlState` / `look` を組み合わせてアクション列を再生します。

### 4.1 モデル取得とライセンス確認

1. Hugging Face 上の [openai/vpt](https://huggingface.co/openai/vpt) リポジトリから `foundation-model-1x.model`（例）を取得します。`python/services/vpt_controller.py` の `VPTModelSpec` で `repo_id` と `filename` を指定すると、自動的に `var/vpt/` 配下へダウンロードされます。
2. 取得前に `pip install torch huggingface-hub` を実行して依存関係を導入してください。PyTorch 2.3 以上 + CUDA 11.8 以降であれば GPU 推論が利用できます。
3. VPT モデルは MIT License で公開されています。`VPTController.verify_model_license()` を呼び出すと Hugging Face のモデルカードを参照し、期待値（既定で `mit`）と一致しない場合は例外を送出します。再配布が禁止されているチェックポイントを扱う場合は `.gitignore` によりコミット対象外となるため、秘匿情報が混入しません。

### 4.2 GPU と依存関係の要件

- NVIDIA GPU（8GB 以上推奨） + CUDA 対応ドライバーがある場合は `torch` の CUDA ビルドをインストールしてください。GPU 非搭載環境でも CPU 推論は可能ですが、ヒューリスティック経路へフォールバックします。
- 追加依存関係: `torch`, `huggingface-hub`, `numpy`（PyTorch 依存で自動導入）。Python 側で VPT モデルを使用しない場合はインストール不要です。
- `python/services/vpt_controller.py` は PyTorch が見つからない場合でも安全にヒューリスティックを返すため、開発環境での回帰テストは軽量に保たれます。

### 4.3 Mineflayer 側の切り替え

Node.js 側の実行モードは `CONTROL_MODE` 環境変数で切り替えます。既定値は `command`（従来のコマンド駆動）で、`vpt` を指定すると VPT 再生ループ専用モードになります。`hybrid` を指定すると通常はコマンド駆動のままですが、LangGraph から `ActionDirective.executor == "hybrid"` を受け取ったタイミングで `playVptActions` を許可し、VPT とコマンドをステップ単位で切り替えられます。

```env
# .env または env.example を参照
CONTROL_MODE=hybrid
VPT_TICK_INTERVAL_MS=50
VPT_MAX_SEQUENCE_LENGTH=240
```

- `VPT_TICK_INTERVAL_MS` は 1 Tick あたりの待機時間（ミリ秒）です。既定で 50ms（Minecraft 標準）。
- `VPT_MAX_SEQUENCE_LENGTH` は 1 回の再生で受け付けるアクション数の上限です。安全のため 2000 Tick までに制限しています。
- Python 側からは `Actions.play_vpt_actions()` でアクション列を送信し、Mineflayer 側は `bot.setControlState` と `bot.look` を組み合わせて逐次実行します。再生中は pathfinder を停止し、終了後に入力状態を確実にリセットします。

### 4.4 テストとフォールバック

- `pytest tests/e2e/test_vpt_playback.py` を実行すると、VPT コントローラーのヒューリスティック出力と WebSocket 送信処理を検証できます。
- Node.js 側の設定は `npm test -- --runInBand node-bot/tests/config.test.ts` で回帰できます。`CONTROL_MODE` の正規化や tick 設定の境界値検証を含みます。
- モデルが未ロードの場合は、観測値から進行方向を推定して `look` → `forward` → `wait` の安全なヒューリスティック列を生成します。GPU を利用できる環境では `VPTController.load_pretrained()` を呼び出して TorchScript モデルを読み込み、推論結果をそのまま Mineflayer へ送信してください。

## 7. アーキテクチャ概要

```
[Player Chat (日本語)]
        │
        ▼
   Python(LLM) ──WS(JSON)──▶ Node(Mineflayer) ──▶ Paper Server
    ├─ planner/（LangGraph へのエントリポイント）
    ├─ planner/graph.py（gpt-5 系モデルでタスク分解）
    ├─ planner_config.py（Responses API 用設定と閾値）
    ├─ actions.py（高レベル→低レベルコマンド）
    └─ memory.py（座標/在庫/履歴）
```

## 8. 注意

* 本 README/コードは**プレーンテキストのみ**で完結（PDF/Word 不要）。
* 実運用前に安全策（溶岩/落下/爆発回避）や失敗時のリカバリを拡充してください。

## 9. 自律行動改善ロードマップ（自然言語インタラクション強化）

`docs/tech_stack_diagram.md` 6–7 章に詳細なギャップ分析と提案を書き下ろしました。ハイライトは次の 4 点です。

- **Context Fabric（LangGraph × Mineflayer × Paper × Minecraft）**  
  Mineflayer の `perception`、AgentBridge (Paper) の SSE、Minecraft 座標を 1 つの時系列ストアへ統合し、OpenAI へのプロンプトへ「状況の空気感」を渡す。座標付きメモリと新しい `/v1/events/ws` で曖昧な自然言語でも安全判断を自律化します。
- **Socratic Confidence Gate（LangGraph × OpenAI）**  
  Responses API が返す `confidence`/`clarification_needed` を LangGraph の分岐に接続し、低信頼タスクは自動的に `gatherStatus` や追加質問を挟む。`PlanPriorityManager` をしきい値制御へ拡張し、人間らしい確認プロセスを再現します。
- **Hybrid Directive Executor（Mineflayer × VPT × LangGraph）**  
- `ActionDirective.executor == "hybrid"` を利用し、LangGraph から渡された `args.vpt_actions` / `args.fallback_command` を Python 側で解析して `Actions.execute_hybrid_action()` 経由で実行します。`CONTROL_MODE=hybrid` の場合は Mineflayer が通常コマンドを維持しながら、指示単位で VPT 再生を許可します。
- **Skill Feedback Loop & CLI Telemetry（MineDojo × Paper × blazity CLI）**  
  `MineDojoSelfDialogueExecutor` に自動再学習フックを追加し、AgentBridge の危険通知と合わせてスキル登録を回す。`python/cli.py` へ blazity 流の `agentbridge jobs watch` を追加し、Paper 上の異常をチャット無しでも追えるようにします。

これらの改善は「自然言語だけで曖昧な指示を与え、人間同様の感覚で自律行動する」というゴールを段階的に達成するためのロードマップです。

