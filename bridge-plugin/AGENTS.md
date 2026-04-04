# AGENTS.md

## 適用範囲

- このファイルは `bridge-plugin/` 以下に適用する。

## このディレクトリの前提

- Java 21、Gradle Kotlin DSL、Paper API 1.21.1、JUnit 5 を前提とする。
- 現在の plugin loading は `plugin.yml` と `JavaPlugin` ベースで成立している。`paper-plugin.yml` や bootstrapper への移行は、明確な必要性がある場合だけ検討する。
- HTTP 層は `com.sun.net.httpserver.HttpServer` を使っており、リクエストは Paper のメインスレッド以外で処理される。

## 実装ルール

- `AgentBridgePlugin` はライフサイクル管理に集中させ、HTTP transport、ジョブ管理、外部プラグイン連携の詳細を抱え込ませない。
- HTTP handler は認証、HTTP method、JSON 変換、レスポンス整形など transport の責務に留め、ゲームロジックや永続状態の詳細は別クラスへ分ける。
- WorldGuard、CoreProtect、LangGraph retry hook のような外部統合は facade / adapter 越しに扱い、handler から SDK の詳細を直接触らせない。
- HTTP worker thread から Bukkit / Paper API を無造作に触らず、スレッド安全性が怪しいワールド操作やエンティティ操作は適切な scheduler 境界へ戻して扱う。
- `api_key` が無効な場合は fail closed を維持し、認証を迂回する便宜的変更を入れない。
- SSE や長寿命接続では keepalive、購読解除、例外時の後始末を明示し、接続リークを残さない。

## 設定とリソース

- 新しいコマンド、権限、設定項目を追加した場合は、Java 実装だけでなく `src/main/resources/plugin.yml` と `src/main/resources/config.yml` も更新する。
- `compileOnly` 依存と `implementation` 依存の差を意識し、配布 jar に何が入るかを明確に保つ。
- Docker Compose 前提の `build/libs` 連携を壊さないよう、shadowJar 周辺の出力仕様を変更する場合は repo 全体への影響を確認する。

## テスト

- Java テストは `src/test/java/` の JUnit 5 へ追加する。
- 外部プラグイン API には実体依存せず、モックや軽量フェイクで振る舞いを固定する。
- レジストリや handler は並行アクセスや認証境界の回帰に注意する。
