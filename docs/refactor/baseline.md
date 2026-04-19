# Baseline Report (Phase 0)

最終更新日: 2026-04-19 (UTC)

## 1. 実行コマンドと結果

| コンポーネント | コマンド | 結果 | 補足 |
|---|---|---|---|
| Python tests (without setup) | `python -m pytest tests` | ❌ 失敗 | 25 件が import エラーで収集失敗（`agent`, `actions`, `runtime` などの `ModuleNotFoundError`）。 |
| Python tests (after setup) | `.venv/bin/python -m pytest tests` | ✅ 成功 | `bash scripts/setup-python-env.sh` 実行後は 97 passed。`OTEL_EXPORTER_OTLP_ENDPOINT` 未起動による export warning のみ発生。 |
| Node tests | `bash scripts/run-node-bot.sh test` | ❌ 失敗 | 実行環境の Node が `v20.19.6` のため、スクリプト要件 (`22+`) で停止。 |
| Node build | `bash scripts/run-node-bot.sh build` | ❌ 失敗 | 上記と同じく Node 22 未満で停止。 |
| Bridge build | `bash scripts/build-bridge-plugin.sh` | ✅ 成功 | `shadowJar` まで成功（UP-TO-DATE 含む）。 |
| Bridge test | `gradle test` (in `bridge-plugin/`) | ✅ 成功 | `:test` タスク成功。 |
| Compose config | `docker compose config` | ❌ 失敗 | 実行環境に `docker` コマンド無し。 |

## 2. 依存と entrypoint の現状一覧

### Python

- 依存定義:
  - `requirements.txt` + `constraints.txt`（`scripts/setup-python-env.sh` で利用）
  - `pyproject.toml`（editable install を前提に併用）
- 実行 entrypoint:
  - `bash scripts/run-python-agent.sh`
  - `python -m mc_bot_agent_entrypoint`（`scripts/run-python-agent.sh` の実行実体）
  - `python -m python`（互換エントリとして残存）
- 補足:
  - `scripts/run-python-agent.sh` は `sys.path` hack なしで起動する。
  - テスト実行は editable install 前提（`bash scripts/setup-python-env.sh`）で安定する。

### Node (`node-bot/`)

- 依存定義: `node-bot/package.json` + `node-bot/package-lock.json`
- 実行 entrypoint:
  - `bash scripts/run-node-bot.sh start|dev|build|test`
  - `npm run dev` / `npm start`（`node-bot/package.json`）
- 補足:
  - スクリプト側で Node.js 22+ を強制チェック。
  - 初回依存解決は `npm ci`（`node_modules` 不在時）で実行される。

### Bridge (`bridge-plugin/`)

- 依存定義: `bridge-plugin/build.gradle.kts`
- 実行/ビルド entrypoint:
  - `bash scripts/build-bridge-plugin.sh`（`shadowJar`）
  - `gradle test`（`bridge-plugin/`）
- 補足:
  - 現在の baseline では build/test ともに成功。
  - 手置き jar 依存が CI で問題化しないかは Phase 1 継続確認対象。

## 3. `.env.example` / README / Compose の差異（Phase 0 観測）

1. 環境テンプレートは `env.dev.example` と `env.prod.example` が追加済みで、`env.example` は互換用として残置されている。
2. README は `cp env.dev.example .env` を初期導線にし、dev/prod の使い分けを明記している。
3. `docker-compose.yml` では起動時 install がまだ残っている。
   - Node: `npm ci && npm run dev`
   - Python: `pip install -r requirements.txt -c constraints.txt && watchfiles ...`
4. Compose 側で `PYTHONPATH=/app:/app/python` が設定されており、旧 import 経路との互換レイヤが残存している。

## 4. 主要実行経路ファイル（差分評価用の参照リスト）

### Python planner/runtime

- `python/mc_bot_agent_entrypoint.py`
- `python/__main__.py`
- `python/runtime/bootstrap.py`
- `python/runtime/unified_agent_graph.py`
- `python/runtime/websocket_server.py`
- `python/runtime/chat_queue.py`
- `python/planner/graph.py`
- `python/planner_config.py`
- `pyproject.toml`
- `requirements.txt`
- `constraints.txt`

### Node bot runtime

- `node-bot/bot.ts`
- `node-bot/runtime/bootstrap.ts`
- `node-bot/runtime/server.ts`
- `node-bot/runtime/agentBridge.ts`
- `node-bot/runtime/services/chatBridge.ts`
- `node-bot/runtime/transportEnvelope.ts`
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

### 契約/起動/運用共通

- `contracts/transport-envelope.schema.json`
- `scripts/run-python-agent.sh`
- `scripts/run-python-agent-watch.sh`
- `scripts/setup-python-env.sh`
- `scripts/run-node-bot.sh`
- `scripts/build-bridge-plugin.sh`
- `docker-compose.yml`
- `docker-compose.host-services.yml`
- `README.md`
- `env.dev.example`
- `env.prod.example`
- `env.example`

## 5. 既知問題（Phase 0 時点）

- Python テストは依存導入前だと import エラーで失敗するが、`bash scripts/setup-python-env.sh` 後は `.venv/bin/python -m pytest tests` で 97 passed。
- Node の baseline 実行は Node 22+ 前提のため、CI/開発環境でのバージョン固定が必須。
- Compose baseline は実行環境依存（docker 未インストール）で検証不能。
- Compose の Python サービスに `PYTHONPATH` 依存が残り、package 化方針との整合確認が必要。

## Phase 0 完了報告
- 変更概要:
  - 現時点の実行結果を再採取し、成功/失敗理由を更新。
  - Python/Node/Bridge の依存・entrypoint・既知リスクを現ツリー基準で棚卸し。
  - `.env` テンプレート分離済み状態と Compose 実行方式の差分を再確認。
- 主な変更ファイル:
  - `docs/refactor/baseline.md`
- 互換性影響:
  - ドキュメント更新のみ（実行挙動の変更なし）。
- 実行したコマンド:
  - `python -m pytest tests`
  - `bash scripts/setup-python-env.sh`
  - `.venv/bin/python -m pytest tests`
  - `bash scripts/run-node-bot.sh test`
  - `bash scripts/run-node-bot.sh build`
  - `bash scripts/build-bridge-plugin.sh`
  - `cd bridge-plugin && gradle test`
  - `docker compose config`
- テスト結果:
  - Python: 条件付き（未セットアップ時は import エラー 25 件 / セットアップ後は 97 passed）
  - Node test/build: 失敗（Node.js 22+ 不足）
  - Bridge build/test: 成功
  - Compose config: 失敗（docker コマンド無し）
- 残課題:
  - Python テストは環境セットアップ前提のため、ローカル実行手順を README/CI と常に同期する必要がある。
  - Node 実行環境を 22 系に統一する仕組み（CI/開発双方）の継続確認が必要。
  - Docker 不在環境でも確認可能な代替チェックの整備が必要。
