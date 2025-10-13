# docker-compose 実行ログ調査メモ（2025-10-14）

## 概要
- `docker-compose up --build` 実行時に発生した警告やエラーを整理した。
- 重大度を「エラー（処理継続不可）」「注意（対応推奨）」「情報」に分類して記載。

## エラー
- Node.js 実行時に `SyntaxError: The requested module 'mineflayer-pathfinder' does not provide an export named 'goals'` が発生し、`bot.ts` の起動が停止している。
  - `mineflayer-pathfinder` の ES モジュールには `goals` という名前付きエクスポートが存在しないため、`import { goals }` は失敗する。
  - 対応策としては、`import { goals }` を削除し、`pathfinder.goals` などの既存 API から参照する、もしくは `mineflayer-pathfinder` から提供される正しいインポート方法を確認する必要がある。

## 注意
- `docker-compose.yml` の `version` キーが非推奨になっている旨の警告が出力されている。Compose v2 以降では `version` の明示は不要のため、削除を検討する。
- 旧構成のコンテナが残っており、`--remove-orphans` フラグでの掃除が推奨されている。
- npm 依存関係で 5 件の「high severity」脆弱性が検出されている。`npm audit fix`、必要に応じて `--force` を検討する。
- `lodash.get@4.4.2` が非推奨になっている旨の警告が出ている。代替としてオプショナルチェイニング演算子の利用が推奨される。
- Python コンテナで root ユーザーとして `pip` を実行していることへの警告。コンテナ利用での実害は小さいが、気になる場合は仮想環境を利用する。

## 情報
- pip について新バージョンがあるとの通知。
- npm について新しいメジャーバージョンがあるとの通知。

## 推奨アクションまとめ
1. `bot.ts` の `mineflayer-pathfinder` からのインポート方法を修正する（最優先）。
2. Compose ファイルの `version` キー削除と孤立コンテナの整理を検討する。
3. npm の脆弱性修正と依存パッケージのアップデート方針を決める。
4. 必要に応じて Python / npm ツールチェーンのバージョン更新を行う。
