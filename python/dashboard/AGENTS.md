# AGENTS.md

## 適用範囲

- このファイルは `python/dashboard/` 以下に適用する。

## このディレクトリの前提

- `server.py` は依存を極力増やさない軽量 HTTP サーバー。
- `frontend.tsx` は人が読みやすい UI ソース。
- `static/app.js` は実際に配信されるフロントエンド資産。

## 実装ルール

- ダッシュボードは内部状態の可視化専用とし、読み取り中心の設計を保つ。副作用を持つ操作系エンドポイントを安易に増やさない。
- `frontend.tsx` の挙動を変えた場合は、同じ変更で `static/app.js` も同期する。片方だけ直して配信資産を放置しない。
- まだ正式なフロントエンド build pipeline は入っていないため、重い bundler や複雑な依存を持ち込む前に費用対効果を確認する。
- `frontend.tsx` / `static/app.js` はグローバルな `React` / `ReactDOM` 前提で動いている。bundler 前提の import や runtime を当然視しない。
- API payload の shape を変える場合は `server.py`、`frontend.tsx`、`static/app.js` の 3 点を同時に揃える。
- 認証は `DASHBOARD_ACCESS_TOKEN` 前提の Bearer または query token 互換を維持し、公開環境で無防備にならないようにする。

## コメントと保守

- ダッシュボードはキャッチアップ用途が強いため、画面項目や JSON payload の意味が追いにくい箇所には現行仕様を補うコメントを付ける。
- 過去の改修経緯や一時運用メモはコードコメントへ残さない。
