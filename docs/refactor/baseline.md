# Baseline Report (Phase 0)

最終更新日: 2026-04-18 (UTC)

## 1. 実行コマンドと結果

| コンポーネント | コマンド | 結果 | 補足 |
|---|---|---|---|
| Python tests | `python -m pytest tests` | ✅ 成功 | 88 passed。終了時に OTLP (`localhost:4318`) への span export リトライ警告あり。 |
| Node tests | `bash scripts/run-node-bot.sh test` | ❌ 失敗 | 実行環境の Node が `v20.19.6` のため、スクリプト要件 (`22+`) で停止。 |
| Node build | `bash scripts/run-node-bot.sh build` | ❌ 失敗 | 上記と同じく Node 22 未満で停止。 |
| Bridge build | `bash scripts/build-bridge-plugin.sh` | ✅ 成功 | `shadowJar` まで成功（UP-TO-DATE 含む）。 |
| Bridge test | `gradle test` (in `bridge-plugin/`) | ❌ 失敗 | 依存解決で `403 Forbidden`（`paperlib`, `jchronic`, `jlibnoise`）。 |
| Compose config | `docker compose config` | ❌ 失敗 | 実行環境に `docker` コマンド無し。 |

## 2. 依存と entrypoint の現状一覧

### Python

- 依存定義: ルート `requirements.txt`（固定バージョン pin）
- 実行 entrypoint:
  - `bash scripts/run-python-agent.sh`
  - `python -m python`（`python/__main__.py`）
- 補足:
  - `scripts/run-python-agent.sh` は `PYTHONPATH=$repo_root:$repo_root/python` を設定して実行。
  - `python/__main__.py` 内で `sys.path.insert(0, pythonディレクトリ)` を行っている。

### Node (`node-bot/`)

- 依存定義: `node-bot/package.json` + `node-bot/package-lock.json`
- 実行 entrypoint:
  - `bash scripts/run-node-bot.sh start|dev|build|test`
  - `npm run dev` / `npm start`（`node-bot/package.json`）
- 補足:
  - スクリプト側で Node.js 22+ を強制チェック。

### Bridge (`bridge-plugin/`)

- 依存定義: `bridge-plugin/build.gradle.kts`
- 実行/ビルド entrypoint:
  - `bash scripts/build-bridge-plugin.sh`（`shadowJar`）
  - `gradle test`（wrapper 未同梱のためシステム gradle）
- 補足:
  - `compileOnly(files("libs/CoreProtect-22.0.jar"))` のローカル jar 参照あり。

## 3. `.env.example` / README / Compose の差異（Phase 0 観測）

1. `.env` テンプレートは `env.example` 1 枚のみで、dev/prod 分離は未実施。
2. README は `cp env.example .env` を前提としており、環境分離の導線はない。
3. `docker-compose.yml` では起動時インストールが残っている。
   - Node: `npm install && npm run dev`
   - Python: `pip install -r requirements.txt && watchfiles ...`
4. Compose 側は `.env` をそのまま参照し、dev/prod 安全デフォルトの切り替え機構は未導入。

## 4. 主要実行経路ファイル（差分評価用の参照リスト）

### Python planner/runtime

- `python/__main__.py`
- `python/runtime/bootstrap.py`
- `python/runtime/unified_agent_graph.py`
- `python/runtime/websocket_server.py`
- `python/runtime/chat_queue.py`
- `python/planner_config.py`
- `requirements.txt`

### Node bot runtime

- `node-bot/bot.ts`
- `node-bot/runtime/bootstrap.ts`
- `node-bot/runtime/server.ts`
- `node-bot/runtime/agentBridge.ts`
- `node-bot/runtime/services/chatBridge.ts`
- `node-bot/runtime/env.ts`
- `node-bot/package.json`
- `node-bot/package-lock.json`

### Bridge plugin

- `bridge-plugin/build.gradle.kts`
- `bridge-plugin/src/main/java/com/example/bridge/AgentBridgePlugin.java`
- `bridge-plugin/src/main/java/com/example/bridge/http/BridgeHttpServer.java`
- `bridge-plugin/src/main/java/com/example/bridge/http/handlers/*.java`
- `bridge-plugin/src/main/java/com/example/bridge/util/CoreProtectFacade.java`
- `bridge-plugin/src/main/resources/config.yml`

### 起動/運用共通

- `scripts/run-python-agent.sh`
- `scripts/run-node-bot.sh`
- `scripts/build-bridge-plugin.sh`
- `docker-compose.yml`
- `docker-compose.host-services.yml`
- `README.md`
- `env.example`

## 5. 既知問題（Phase 0 時点）

- Node の baseline 実行は Node 22+ 前提のため、CI/開発環境でバージョン固定戦略が必須。
- Bridge test は依存レポジトリへのアクセス (`403`) で不安定。
- Compose baseline は実行環境依存（docker 未インストール）で検証不能。
- Python 側に `PYTHONPATH` / `sys.path` 依存の import 経路が残っている（Phase 2 対象）。
