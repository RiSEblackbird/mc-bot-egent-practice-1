# Minecraft 自律ボット（Python LLM + Node(Mineflayer) + Paper）

Minecraft Java Edition（既定: **1.21.1 + Paper**）上で動作する **日本語対応の LLM 自律ボット**です。  
プレイヤーのチャットを起点に、Python 側（LLM / LangGraph）が意図解析とタスク分解を行い、Node.js 側（Mineflayer）がゲーム内アクションを実行します。必要に応じて Paper プラグイン（AgentBridge）が保護領域チェックや継続採掘の評価 API / 危険通知を提供します。

> 重要: **`.env` や API キー等の秘匿情報をリポジトリへコミットしない**でください。

## 目次

- [できること（現行）](#できること現行)
- [対応バージョン / 互換性](#対応バージョン--互換性)
- [アーキテクチャ概要](#アーキテクチャ概要)
- [クイックスタート（ローカル実行）](#クイックスタートローカル実行)
- [Docker Compose（開発用ホットリロード）](#docker-compose開発用ホットリロード)
- [設定（.env）](#設定env)
- [Paper 連携: AgentBridge（任意）](#paper-連携-agentbridge任意)
- [継続採掘 CLI（任意）](#継続採掘-cli任意)
- [ダッシュボード（任意）](#ダッシュボード任意)
- [VPT 操作再生モード（任意）](#vpt-操作再生モード任意)
- [使い方（ゲーム内）](#使い方ゲーム内)
- [Tips / トラブルシューティング](#tips--トラブルシューティング)
- [開発者向け](#開発者向け)
- [ドキュメント（docs）](#ドキュメントdocs)
- [参考理論（URL必須）](#参考理論url必須)

## できること（現行）

- **農業**: 畑の整備/収穫/再植付け、パン作成
- **採掘**: ブランチマイニング等（指示に応じて採掘・たいまつ設置）
- **探索**: プレイヤー基準の周辺探索
- **クラフト支援**: 素材収集→クラフト→受け渡し
- **自己防衛**: 敵対 Mob の回避/迎撃
- **簡易建築**: 小屋/倉庫などの段階的建築（状態遷移に基づく）
- **随伴**: 「ついてきて」で追尾
- **装備持ち替え**: ツール名に応じた装備
- **マルチエージェント協調**: ロール切替とイベント共有（共有メモリ）

## 対応バージョン / 互換性

- **Minecraft / Paper**: 既定で **1.21.1**
- **クライアント**: 原則 **1.21.1** を推奨  
  別バージョンのクライアントから接続が必要な場合は、Paper 側に ViaVersion / ViaBackwards 等で調整してください（完全互換ではありません）。

## アーキテクチャ概要

```
[Player Chat (日本語)]
        │
        ▼
   Python(LLM) ──WS(JSON)──▶ Node(Mineflayer) ──▶ Paper Server
    ├─ planner/（LangGraph 入口）
    ├─ planner/graph.py（タスク分解）
    ├─ planner_config.py（LLM 設定/閾値）
    ├─ actions.py（高レベル→低レベルコマンド）
    └─ memory.py（座標/在庫/履歴）

  （任意）Paper: AgentBridge ──HTTP/SSE──▶ Python（保護領域/危険通知/継続採掘）
```

## クイックスタート（ローカル実行）

前提:
- Java 21（Paper / プラグインビルド用）
- Node.js 22.x（Mineflayer v4.33 系の要件）
- Python 3.12.x

### 1) Paper サーバー起動（別途用意）

Windows 例:

```powershell
cd C:\mc\paper
java -Xms4G -Xmx4G -jar .\paper.jar --nogui
```

開発中は `server.properties` の `online-mode=false` を推奨します。

### 2) `.env` 作成（プロジェクトルート）

```bash
cp env.example .env
```

最低限 `OPENAI_API_KEY` と、Minecraft 接続先（`MC_HOST` / `MC_PORT`）を設定してください。

### 3) Node（Mineflayer）起動

```bash
cd node-bot
npm install
npm start
```

### 4) Python（LLM エージェント）起動

```bash
cd python
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r ../requirements.txt
python -m runtime.bootstrap
```

### 5) Minecraft でチャットする

Paper に参加して、チャットで指示します（例は [使い方](#使い方ゲーム内)）。

## Docker Compose（開発用ホットリロード）

Python と Node を同時にホットリロードで動かしたい場合:

```bash
cp env.example .env  # まだ .env が無い場合
docker compose up --build
```

- **Node**: `npm run dev`（`tsx`）で自動再起動
- **Python**: ルートから `watchfiles --filter python --ignore-paths .venv -- "python -m python"` を実行  
  注意: `cd python` して `python -m python` を実行すると `ModuleNotFoundError: No module named python` になるため、**必ずルート基準で起動**してください。
- 注意: `watchfiles -- ...` のコマンドは `"python -m python"` のように **1 引数へクォート**してください。クォート漏れは対話モードになり、待受が起動しません（Node 側で `ECONNREFUSED` が出ます）。

## 設定（.env）

`.env` は `env.example` を一次情報として扱ってください。ここでは「運用でよく触る項目」と「落とし穴」を要約します。

### OpenAI / プランナー

- **`OPENAI_API_KEY`**: 必須
- **`OPENAI_MODEL`**: 既定は gpt-5 系（例: `gpt-5-mini`）
- **`OPENAI_TEMPERATURE`**: 温度固定モデルの場合は送信を抑止します（[Tips](#openai-設定で温度を変更したい場合)）
- **`OPENAI_REASONING_EFFORT` / `OPENAI_VERBOSITY`**: Responses API の推論/冗長度
- **`LLM_TIMEOUT_SECONDS`**: タイムアウト（既定 30 秒）

### Python ↔ Node WebSocket（混同しやすい）

- **`AGENT_WS_HOST` / `AGENT_WS_PORT`**: Python エージェントの **待受**
- **`AGENT_WS_URL`**: Node（Mineflayer）が接続する **Python 側の接続先**
  - `0.0.0.0` は待受専用です。接続先には `127.0.0.1` / `host.docker.internal` / `python-agent`（Compose）等、到達可能なホスト名を指定してください。

### Minecraft / Mineflayer

- **`MC_HOST` / `MC_PORT`**: Paper の接続先
  - Docker で `localhost` を指定すると `host.docker.internal` へ補正されます。
- **`MC_VERSION`**: プロトコル（既定 `1.21.1`）
- **`BOT_USERNAME` / `AUTH_MODE`**: `offline` / `microsoft`

### たいまつ・移動など（運用で効く閾値）

- **`MOVE_GOAL_TOLERANCE`**: 目的地の許容範囲（GoalNear）。「到達しているのに失敗扱い」になりやすい場合に調整します。
- **`PERCEPTION_*`**: 周辺認知（スキャン範囲/周期）
- **`LOW_FOOD_THRESHOLD`**: 空腹警告しきい値

### 可観測性（任意）

- **`OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_TRACES_SAMPLER_RATIO`**: OpenTelemetry
- **`LANGSMITH_*`**: LangSmith

## Paper 連携: AgentBridge（任意）

Paper 上で保護領域や継続採掘の評価を扱う HTTP プラグインです（ディレクトリ: `bridge-plugin/`）。

### セキュリティ方針（重要）

- **`X-API-Key` 認証が必須**です。
- `plugins/AgentBridge/config.yml` の `api_key` が空または `CHANGE_ME` の場合は、意図的に **HTTP サーバーを起動せずプラグインを無効化**します。

### ビルドと配置（概要）

1. 依存 jar（例: CoreProtect）を `bridge-plugin/libs/` へ配置します（リポジトリには `.gitkeep` のみコミット）。
2. Java 21 + Gradle を用意し、以下を実行します。

```bash
cd bridge-plugin
gradle shadowJar
```

3. 生成された `bridge-plugin/build/libs/AgentBridge-*.jar` を Paper の `plugins/` に配置します。
4. Paper を起動し、生成された `plugins/AgentBridge/config.yml` に `api_key` を設定し、`.env` の `BRIDGE_API_KEY` と一致させます。

> WorldEdit / WorldGuard の取得はそれぞれ [WorldEdit](https://enginehub.org/worldedit) / [WorldGuard](https://enginehub.org/worldguard) を参照してください。

### SSE（危険通知/進捗の push）

- `events.stream_enabled: true` で `/v1/events/stream` を有効化できます（SSE）。
- Python 側は `BRIDGE_EVENT_STREAM_ENABLED=true` の場合に購読します。

## 継続採掘 CLI（任意）

Python の CLI から継続採掘ジョブを開始できます（`python/cli.py`）。

```bash
# 明示的に方向を指定
python -m python.cli tunnel --world world --anchor 100 12 200 --dir 1 0 0 --section 2x2 --len 64 --owner Taishi

# 自動推定（東西南北から安全度をスコア化）
python -m python.cli tunnel --world world --anchor 100 12 200 --dir auto --section 2x2 --len 64
```

危険通知やジョブ状態をチャットレスで追う場合:

```bash
python -m python.cli agentbridge jobs watch --danger-only --format text
```

## ダッシュボード（任意）

Python エージェント起動中に `http://127.0.0.1:9100/`（既定）へアクセスすると、キュー長・ロール・最新プラン要約・perception サマリなどを確認できます。

- **`DASHBOARD_ENABLED`**: `true` で有効（既定 true）
- **`DASHBOARD_HOST` / `DASHBOARD_PORT`**: バインド
- **`DASHBOARD_ACCESS_TOKEN`**: 任意。設定すると Bearer 認証を要求します（ブラウザは `?token=...` でも可）。

## VPT 操作再生モード（任意）

Mineflayer 側の低レベル操作を逐次再生する経路です（環境により未使用でも問題ありません）。

- **切替**: `CONTROL_MODE=command | vpt | hybrid`
- **主要設定**: `VPT_TICK_INTERVAL_MS`, `VPT_MAX_SEQUENCE_LENGTH`

モデル取得・依存要件・ライセンス確認などは README では要点のみとし、実装側は `python/services/vpt_controller.py` を参照してください。

## 使い方（ゲーム内）

プレイヤーがチャットで日本語の自然文を送ります。例:

- 「パンが無い」 → 小麦収穫→パン作成→手渡し or チェスト格納
- 「鉄が足りない」 → ツール確認→採掘計画→ブランチマイニング
- 「ついてきて」 → 追尾モード
- 「ここに小屋を建てて」 → 建材確認→不足なら収集→建築
- 「現在値教えて」 → 現在位置 (X/Y/Z) を即座に報告

## Tips / トラブルシューティング

### デバッグログで「どこで止まったか」を切る

「チャットを送ったのに何も起きない」場合は、次を時系列で突き合わせます。

1. **Node（Mineflayer）**: `node-bot` の標準出力に `[Chat] ...` と、直後の Python 転送ログ（例: `[ChatBridge] ...`）が出るか
2. **Python エージェント**: `WS send/recv`、`queue chat`、`plan_step ...`、`execution barrier detected` 等が出るか

これにより「チャット受信→転送→LLM→コマンド送信→応答」のどこで止まったかを切り分けられます。

### OpenAI 設定で温度を変更したい場合

一部モデルは API 側で温度固定です。`OPENAI_TEMPERATURE` を設定しても、温度変更不可モデルでは **送信を抑止**し、理由をログへ出します。

- 対応: 温度可変モデルへ切替、または `.env` の値とモデルの組み合わせを見直してください。

### 自動移動が「到達したのに失敗」になりやすい

Mineflayer の移動完了判定は環境で揺れます。`MOVE_GOAL_TOLERANCE` を調整して `GoalNear` の許容範囲を広げると、段差/水流で「ほぼ到達」なのに失敗扱いになる頻度を下げられます。

また、空腹が原因で移動/採掘が止まる場合があるため、`LOW_FOOD_THRESHOLD` や補給フローも併せて確認してください。

### 座標指定の表記ゆれ

「X=-36, Y=73, Z=-66」「{XYZ: -36 / 73 / -66}」など多様な記法から座標を抽出します。抽出できない場合は既定座標へフォールバックするため、ログ/チャットに「座標が含まれていない」旨が出たら、座標の書き方を見直してください。

### Paper の警告（`HelperBot moved wrongly!` 等）

高機動（ダッシュ/パルクール）を許可する設定では警告が出ることがあります。サーバーが位置補正した場合でも、目的地を再セットして移動を継続します。属性名の互換（`minecraft:movement_speed` → `minecraft:generic.movement_speed`）は内部で置換します。

### 1.21.x の `PartialReadError` が出る

`MC_VERSION` を **minecraft-data が認識するプロトコルラベル**（例: `1.21.1`）で揃えてください。加えて、`node-bot/runtime/slotPatch.ts` が Slot 定義の差分を吸収します（新しい 1.21.x で再発する場合はパッチの適用状況を確認してください）。

### Docker/Compose で `ECONNREFUSED` が出る

Python 側の待受（`AGENT_WS_HOST` / `AGENT_WS_PORT`）と、Node 側の接続先（`AGENT_WS_URL`）がズレているケースが多いです。`0.0.0.0` は待受専用なので、接続先には `python-agent` / `host.docker.internal` / `127.0.0.1` を指定してください。

## 開発者向け

### テスト

- **Node**:

```bash
cd node-bot
npm test
```

- **Python**（例）:

```bash
pytest tests/test_agent_config.py tests/test_structured_logging.py
```

LLM/LangGraph 周辺を触った場合はシナリオテストも実行してください（例: `tests/test_langgraph_scenarios.py`）。

### 依存更新のチェック（Python）

1. `.venv` を作り直して `pip install -r ../requirements.txt` が解決できるか確認
2. テスト実行（上記）
3. `python -m runtime.bootstrap` で起動確認（型解決/待受まで）
4. 破壊的変更があれば `python/planner_config.py` 等の設定集約点へ追従

## ドキュメント（docs）

詳細設計・拡張方針は `docs/` に集約しています（README は入口と手順に集中します）。

- `docs/tech_stack_diagram.md`: 技術スタックと相関図（全体俯瞰）
- `docs/movement_extension_design.md`: 移動拡張ポイント/forcedMove リトライ設計
- `docs/building_state_machine.md`: 建築ステートマシン（フェーズ/遷移）
- `docs/tunnel_mode_design.md`: 継続採掘モードの設計サマリー
- `docs/minedojo_integration.md`: MineDojo 連携（データ配置/ポリシー）

## 参考理論（URL必須）

本プロジェクトが参照する理論/手法（各 URL を必ず記載）:

| 理論/手法 | 主な狙い | 主な適用箇所（例） |
| --- | --- | --- |
| Voyager | 自律探索・ツール発見 | `python/planner/graph.py`, `python/memory.py`, `docs/building_state_machine.md` |
| ReAct | 推論と行動の往復 | `python/agent.py`, `python/actions.py` |
| Reflexion | 失敗からの自己評価・再計画 | `python/runtime/reflection_prompt.py`, `python/runtime/action_graph.py` |
| VPT | 操作シーケンスの模倣 | `python/services/vpt_controller.py`, `node-bot/bot.ts` |
| MineDojo | タスク/デモ参照 | `docs/minedojo_integration.md`, `docs/tunnel_mode_design.md` |

- Voyager — [https://arxiv.org/abs/2305.16291](https://arxiv.org/abs/2305.16291)
- ReAct — [https://arxiv.org/abs/2210.03629](https://arxiv.org/abs/2210.03629)
- Reflexion — [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366)
- VPT — [https://arxiv.org/abs/2206.04615](https://arxiv.org/abs/2206.04615)
- MineDojo — [https://arxiv.org/abs/2206.08853](https://arxiv.org/abs/2206.08853)

