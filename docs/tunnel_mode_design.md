# 継続採掘モード設計サマリー

本ドキュメントは `Codex 向け開発指示書` を踏まえ、実装した構成要素の概要と補足事項を整理したものです。

## Java: AgentBridge プラグイン

- `bridge-plugin/` に Gradle プロジェクトを追加し、Paper 1.20 API を `compileOnly` で参照。
- `com.sun.net.httpserver.HttpServer` を用いた軽量 HTTP サーバーを起動し、`X-API-Key` 認証を必須化。
- `/v1/jobs/*` エンドポイントで継続採掘ジョブの開始・前進・停止を提供。
- `/v1/blocks/bulk_eval` と `/v1/coreprotect/is_player_placed_bulk` で断面評価と CoreProtect 照会をまとめて返却。
- WorldGuard 操作はメインスレッドで行うため、`BukkitScheduler#callSyncMethod` を利用して同期実行。
- CoreProtect API は実行時リフレクションで解決し、jar をリポジトリへ含めなくてもビルドできるように配慮。
- `config.yml` にフロンティアの窓長や安全設定、HTTP タイムアウトを集約。

## Python: Bridge クライアントと Tunnel モード

- `python/bridge_client.py` で HTTP クライアントを実装。指数バックオフ付きで再試行し、JSON パースを一元管理。
- `python/heuristics/artificial_filters.py` で自然ブロック判定と採掘マスク生成ロジックを定義。
- `python/modes/tunnel.py` の `TunnelMode` がメインループ。AgentBridge からバルク評価を取得し、Mineflayer へ `mineBlocks` / `placeTorch` コマンドを送信。
- `python/cli.py` に `tunnel` サブコマンドを追加し、CLI から継続採掘ジョブを起動可能にした。
- `.env` でブリッジ URL やたいまつ間隔などのパラメータを調整。

## テスト

- `tests/stubs/bridge_stub.py` で AgentBridge のテストダブルを用意。
- `tests/test_tunnel_mode.py` で採掘マスクのユニットテストと TunnelMode の簡易統合テストを実施。

## 今後の拡張候補

- `--dir auto` オプションの実装（CoreProtect ログ解析による方向推定）。
- 液体検知時の自動封止やたいまつ設置位置の柔軟化。
- WebSocket による進捗通知や停止理由のリアルタイム配信。
