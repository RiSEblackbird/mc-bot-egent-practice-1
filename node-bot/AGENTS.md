# AGENTS.md

## 適用範囲

- このファイルは `node-bot/` 以下に適用する。

## 作業進行

- 共通の進行、GitHub配送、公開安全性、完了報告はルート `AGENTS.md` と発動したtask Skillに従う。
- このファイルはNode bot固有の実装規約と検証観点だけを正本化する。

## このディレクトリの前提

- Node.js 22系、TypeScript、ESM、Vitestを前提とする。
- repoの既定Minecraft versionは `1.21.1` で、Mineflayer側のprotocol解決は `runtime/config.ts` に集約している。
- `mineflayer`、`minecraft-data`、`minecraft-protocol` などはupstream更新の影響を受けやすいため、互換性を軽視した変更を避ける。

## 実装ルール

- `bot.ts` は起動配線とhandler組み立てに集中させ、個別command、service、設定解決、event処理の詳細を抱え込ませない。
- 新しい環境変数は `runtime/env.ts` や `runtime/config.ts` に集約し、`process.env` の直接参照を各所へ増やさない。
- MineflayerとMinecraftの互換性回避logicは `runtime/config.ts` などで一元管理し、各command handlerで個別にfallbackしない。
- CommonJS依存との相互運用は、既存のimport patternに合わせて明示的に扱う。
- `NavigationController`、chat bridge、telemetryなど既存serviceに寄せ、bot instanceへ密結合したlogicを増やしすぎない。
- command失敗は構造化した応答とlogで返し、無限retryや曖昧な成功扱いで隠さない。
- 既存のOpenTelemetry span、metric、counterを削る変更を避ける。

## 契約と設定

- Python agentまたはBridge pluginとのpayload / event契約を変える場合は、相手側、test、docsを同じ変更で整合させる。
- config変更では `package-lock.json`、README、`env.example`群、Node testの連動範囲を確認する。

## テスト

- testは `node-bot/tests/` のVitestを使う。
- 新しい処理はDIしやすい形へ寄せ、Minecraft serverの実接続を必要としないunit testを先に書けるようにする。
- 設定解決、環境変数解釈、navigation制御、chat bridgeなど壊れやすい境界には回帰testを用意する。

## コメント

- commentは現行の理由、制約、契約を補い、改修メモや一時的な連絡事項を残さない。
