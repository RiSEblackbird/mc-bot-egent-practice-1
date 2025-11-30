# 継続採掘モード設計サマリー

本ドキュメントは `Codex 向け開発指示書` を踏まえ、実装した構成要素の概要と補足事項を整理したものです。

## Java: AgentBridge プラグイン

- `bridge-plugin/` に Gradle プロジェクトを追加し、Paper 1.21.1 API を `compileOnly` で参照。
- `com.sun.net.httpserver.HttpServer` を用いた軽量 HTTP サーバーを起動し、`X-API-Key` 認証を必須化。`config.yml` の `api_key` が空・`CHANGE_ME` の場合は起動時に警告を出してプラグインを停止し、誤公開を防ぐ。
- `/v1/jobs/*` エンドポイントで継続採掘ジョブの開始・前進・停止を提供。
- `/v1/blocks/bulk_eval` と `/v1/coreprotect/is_player_placed_bulk` で断面評価と CoreProtect 照会をまとめて返却。
- 液体を検知した場合は `/v1/blocks/bulk_eval` が HTTP 409 と停止座標を返し、`/v1/jobs/advance` も同様にブロック状態を明示する。
- WorldGuard 操作はメインスレッドで行うため、`BukkitScheduler#callSyncMethod` を利用して同期実行。
- CoreProtect API は実行時リフレクションで解決し、jar をリポジトリへ含めなくてもビルドできるように配慮。
- `config.yml` にフロンティアの窓長や安全設定、HTTP タイムアウトを集約。

## Python: Bridge クライアントと Tunnel モード

- `python/bridge_client.py` で HTTP クライアントを実装。指数バックオフ付きで再試行し、JSON パースを一元管理。
- Bridge 側からの 409(液体検知) 応答を `BridgeError` の `status_code` と `payload` に格納し、Mineflayer への追加命令を止められるようにした。
- `python/heuristics/artificial_filters.py` で自然ブロック判定と採掘マスク生成ロジックを定義。
- `python/modes/tunnel.py` の `TunnelMode` がメインループ。AgentBridge からバルク評価を取得し、Mineflayer へ `mineBlocks` / `placeTorch` コマンドを送信。
- `python/cli.py` に `tunnel` サブコマンドを追加し、CLI から継続採掘ジョブを起動可能にした。
- `.env` でブリッジ URL やたいまつ間隔などのパラメータを調整。`TUNNEL_TORCH_INTERVAL` / `TUNNEL_FUNCTIONAL_NEAR_RADIUS`
  / `TUNNEL_LIQUIDS_STOP` / `TUNNEL_WINDOW_LENGTH` は `TunnelMode` 内部の挙動を直接切り替えるため、CLI を再起動せずに
  安全マージンを変更できる。
- `TunnelMode._is_liquid_stop()` が 409 応答に含まれる `payload.error=liquid_detected` を検知し、`tunnel.stop_condition`
  ログを出した上で `BridgeClient.stop()` を必ず呼ぶ。液体検知後は `Bridge` 側の SSE にも `reason=liquid_detected` が流れるため、
  README の障害通知仕様と整合する。

## テスト

- `tests/stubs/bridge_stub.py` で AgentBridge のテストダブルを用意。
- `tests/test_tunnel_mode.py` で採掘マスクのユニットテストと TunnelMode の簡易統合テストを実施。

## 今後の拡張候補

- `--dir auto` オプションの実装（CoreProtect ログ解析による方向推定）。
- 液体検知時の自動封止やたいまつ設置位置の柔軟化。
- WebSocket による進捗通知や停止理由のリアルタイム配信。
