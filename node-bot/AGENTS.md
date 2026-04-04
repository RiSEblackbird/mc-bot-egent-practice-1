# AGENTS.md

## 適用範囲

- このファイルは `node-bot/` 以下に適用する。

## このディレクトリの前提

- Node.js 22 系、TypeScript、ESM、Vitest を前提とする。
- repo の既定 Minecraft バージョンは `1.21.1` で、Mineflayer 側のプロトコル解決は `runtime/config.ts` に集約している。
- `mineflayer`、`minecraft-data`、`minecraft-protocol` など一部依存は upstream の更新影響を受けやすいため、互換性を軽視した変更を避ける。

## 実装ルール

- `bot.ts` は起動配線とハンドラ組み立てに集中させ、個別コマンド、サービス、設定解決、イベント処理の詳細を抱え込ませない。
- 新しい環境変数は `runtime/env.ts` や `runtime/config.ts` に集約し、`process.env` の直接参照を各所へ増やさない。
- Mineflayer と Minecraft の互換性回避ロジックは `runtime/config.ts` や関連モジュールで一元管理し、各コマンドハンドラで個別にフォールバックしない。
- `mineflayer-pathfinder` のような CommonJS 依存との相互運用は、既存の import パターンに合わせて明示的に扱う。
- `NavigationController`、chat bridge、telemetry など既存サービスに寄せ、直接 bot インスタンスへ密結合したロジックを増やしすぎない。
- コマンド失敗は構造化された応答とログで返し、無限再試行や曖昧な成功扱いで隠さない。
- OpenTelemetry の span、metric、counter を既に使っている箇所では、観測可能性を削る変更を避ける。

## テスト

- テストは `node-bot/tests/` の Vitest を使う。
- 新しい処理は DI しやすい形へ寄せ、Minecraft サーバーの実接続を必要としない単体テストを先に書けるようにする。
- 設定解決、環境変数解釈、ナビゲーション制御、チャット橋渡しのような不具合が出やすい境界には回帰テストを用意する。

## 変更時の着眼点

- config の変更では `package-lock.json`、README、`env.example`、Node test のどこまで連動するかを確認する。
- 仕様理解を助けるコメントは残してよいが、改修メモや「あとで消す」前提の連絡事項をソースへ残さない。
