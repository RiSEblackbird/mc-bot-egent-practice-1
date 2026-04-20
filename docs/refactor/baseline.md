# Phase 0 Baseline (2026-04-20)

## 目的

Phase 1 以降で変更による退行を判定できるよう、現行の実行入口・依存・環境差異・検証結果を固定する。

## セッション足場チェック

- 作業ディレクトリ: `/workspace/mc-bot-egent-practice-1`
- ブランチ: `work`
- 直近コミット:
  - `79d4ad3` Merge pull request #154
  - `0fb1b8d` planner失敗分類にLLM呼び出しエラーコードを追加
  - `81be104` Merge pull request #153
- ルート AGENTS.md とサブディレクトリ AGENTS.md の存在を確認

## ベースライン実行結果

| 区分 | コマンド | 結果 | メモ |
| --- | --- | --- | --- |
| Python 環境構築 | `bash scripts/setup-python-env.sh` | ✅ 成功 | `.venv` 再作成、`requirements.txt + constraints.txt`、editable install (`mc-bot-agent`) 完了 |
| Python テスト | `source .venv/bin/activate && python -m pytest tests` | ✅ 成功 | `102 passed`。終了時に OTLP exporter の localhost:4318 接続失敗ログあり（テスト自体は成功） |
| Node テスト | `bash scripts/run-node-bot.sh test` | ❌ 失敗 | 環境の Node が `v20.19.6` のためガードで停止（22+ 必須） |
| Node build | `bash scripts/run-node-bot.sh build` | ❌ 失敗 | 同上（Node 22+ 必須） |
| Bridge build | `bash scripts/build-bridge-plugin.sh` | ✅ 成功 | `shadowJar` 成功 |
| Bridge test/build | `cd bridge-plugin && (./gradlew \|\| gradle) test build` | ✅ 成功 | `test`, `build` 成功 |
| Compose 構成確認 | `docker compose config` | ❌ 失敗 | 実行環境に `docker` コマンドが存在しない |

## 既知問題（Phase 0 時点）

1. Node 系コマンドは Node 22 以上が前提。CI/ローカルでのバージョン固定が必要。
2. `docker compose config` は Docker 非導入環境では検証不可。CI job または Docker 有効環境で補完が必要。
3. Python テスト成功後に OTLP 送信リトライログが出る。ローカル Collector 非起動時の既知ノイズ。

## 依存とエントリポイント（現状正）

### Python

- 依存正本: `requirements.txt` + `constraints.txt`
- パッケージ定義: `pyproject.toml`
- 実行入口:
  - `bash scripts/run-python-agent.sh`
  - `python -m mc_bot_agent_entrypoint`
- テスト入口:
  - `python -m pytest tests`

### Node

- 依存正本: `node-bot/package-lock.json`（`npm ci` 前提）
- 実行入口:
  - `bash scripts/run-node-bot.sh start`
  - `bash scripts/run-node-bot.sh dev`
- 検証入口:
  - `bash scripts/run-node-bot.sh test`
  - `bash scripts/run-node-bot.sh build`

### Bridge Plugin

- ビルド設定: `bridge-plugin/build.gradle.kts`
- 実行入口:
  - `bash scripts/build-bridge-plugin.sh`（`shadowJar`）
  - `cd bridge-plugin && ./gradlew test build`

### Compose

- 定義ファイル: `docker-compose.yml`, `docker-compose.host-services.yml`
- 入口:
  - `docker compose up --build`
  - `docker compose -f docker-compose.yml -f docker-compose.host-services.yml up --build`

## `.env.example` / README / Compose 差異メモ

1. README と運用導線は `env.dev.example` / `env.prod.example` を正本として案内しており、`env.example` は互換残置の位置づけ。
2. `docker-compose.yml` の `node-bot` は起動時に `npm ci`、`python-agent` は起動時に `pip install` を実行する構成（Phase 6 で build 時解決へ寄せる対象）。
3. Compose 内部接続は `COMPOSE_*` 系環境変数で上書きする設計。

## 主要実行経路ファイル一覧（Phase 0 凍結）

- Orchestration / env
  - `README.md`
  - `Makefile`
  - `env.example`
  - `env.dev.example`
  - `env.prod.example`
  - `docker-compose.yml`
  - `docker-compose.host-services.yml`
- Python
  - `pyproject.toml`
  - `requirements.txt`
  - `constraints.txt`
  - `scripts/setup-python-env.sh`
  - `scripts/run-python-agent.sh`
  - `python/mc_bot_agent_entrypoint.py`
- Node
  - `node-bot/package.json`
  - `node-bot/package-lock.json`
  - `scripts/run-node-bot.sh`
  - `node-bot/bot.ts`
- Bridge
  - `scripts/build-bridge-plugin.sh`
  - `bridge-plugin/build.gradle.kts`
  - `bridge-plugin/src/main/java/com/example/bridge/AgentBridgePlugin.java`

## 機械更新向けステータス

```json
{
  "phase": 0,
  "captured_at": "2026-04-20",
  "checks": {
    "python_setup": "passed",
    "python_tests": "passed",
    "node_tests": "failed_node_version",
    "node_build": "failed_node_version",
    "bridge_build": "passed",
    "bridge_test_build": "passed",
    "docker_compose_config": "failed_docker_missing"
  },
  "next_shortest_action": "Phase 1 で CI に Node 22 / Java 21 / Python 3.12 を固定し、Docker 構成検証を自動化する"
}
```

## Phase 0 完了条件に対する判定

- ✅ 「後続 Phase が何を壊したか判定できる」ための実行結果・既知失敗理由・主要経路を記録済み。
