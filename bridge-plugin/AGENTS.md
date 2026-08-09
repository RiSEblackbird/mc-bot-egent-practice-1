# AGENTS.md

## 適用範囲

- このファイルは `bridge-plugin/` 以下に適用する。

## 作業進行

- 共通の進行、GitHub配送、公開安全性、完了報告はルート `AGENTS.md` と発動したtask Skillに従う。
- このファイルはPaper plugin固有の実装規約と検証観点だけを正本化する。

## このディレクトリの前提

- Java 21、Gradle Kotlin DSL、Paper API 1.21.1、JUnit 5を前提とする。
- plugin loadingは `plugin.yml` と `JavaPlugin` で成立している。`paper-plugin.yml` やbootstrapperへの移行は明確な必要性がある場合だけ検討する。
- HTTP層は `com.sun.net.httpserver.HttpServer` を使い、requestはPaper main thread以外で処理される。

## 実装ルール

- `AgentBridgePlugin` はlifecycle管理に集中させ、HTTP transport、job管理、外部plugin連携の詳細を抱え込ませない。
- HTTP handlerは認証、method、JSON変換、response整形などtransportの責務に留め、game logicや永続状態は別classへ分ける。
- WorldGuard、CoreProtect、LangGraph retry hookなど外部統合はfacade / adapter越しに扱い、handlerからSDK詳細を直接触らせない。
- HTTP worker threadからBukkit / Paper APIを無造作に触らず、thread安全性が不明なworld・entity操作は適切なscheduler境界へ戻す。
- `api_key` が無効な場合はfail closedを維持し、認証を迂回する便宜的変更を入れない。
- SSEや長寿命接続ではkeepalive、購読解除、例外時の後始末を明示し、接続leakを残さない。

## 設定とリソース

- 新しいcommand、permission、設定項目を追加した場合は、Java実装だけでなく `src/main/resources/plugin.yml` と `src/main/resources/config.yml` も更新する。
- `compileOnly` と `implementation` の差を意識し、配布jarに何が入るかを明確に保つ。
- Docker Compose前提の `build/libs` 連携を壊さない。shadowJar周辺の出力仕様を変える場合はrepo全体への影響を確認する。
- Python agentまたはNode botとのHTTP / event契約を変える場合は、相手側、test、docsを同じ変更で整合させる。

## テスト

- Java testは `src/test/java/` のJUnit 5へ追加する。
- 外部plugin APIには実体依存せず、mockや軽量fakeで振る舞いを固定する。
- registryやhandlerは並行access、認証境界、SSE cleanupの回帰に注意する。
