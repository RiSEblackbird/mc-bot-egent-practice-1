# AGENTS.md

## 適用範囲

- このファイルは `python/dashboard/` 以下に適用する。
- `python/AGENTS.md` のPython共通契約も併せて適用する。

## 作業進行

- 共通の進行、GitHub配送、公開安全性、完了報告はルート `AGENTS.md` と発動したtask Skillに従う。
- UI/UX、アクセシビリティ、画面文言、loading / empty / error / disabled状態を変える場合は `.agents/skills/ui-ux-review/SKILL.md` を発動する。
- このファイルはdashboard固有の実装規約と検証観点だけを正本化する。

## このディレクトリの前提

- `server.py` は依存を極力増やさない軽量HTTP server。
- `frontend.tsx` は人が読みやすいUI source。
- `static/app.js` は実際に配信されるfrontend asset。

## 実装ルール

- dashboardは内部状態の可視化専用とし、読み取り中心の設計を保つ。副作用を持つ操作endpointを安易に増やさない。
- 初見の運用者が画面目的、対象、状態の意味、次に取れる行動を判断できる表示を保つ。内部状態名を出す場合は意味を追える補助情報を添える。
- `frontend.tsx` の挙動を変えた場合は、同じ変更で `static/app.js` も同期する。片方だけ直して配信assetを放置しない。
- 正式なfrontend build pipelineは未導入である。重いbundlerや複雑な依存を持ち込む前に費用対効果を確認する。
- `frontend.tsx` / `static/app.js` はglobalな `React` / `ReactDOM` 前提で動く。bundler前提のimportやruntimeを当然視しない。
- API payload shapeを変える場合は `server.py`、`frontend.tsx`、`static/app.js` を同時に揃える。
- 認証は `DASHBOARD_ACCESS_TOKEN` 前提のBearerまたはquery token互換を維持し、公開環境で無防備にしない。

## コメントと保守

- 画面項目やJSON payloadの意味が追いにくい箇所には、現行仕様を補うcommentを付ける。
- 過去の改修経緯や一時運用メモはcode commentへ残さない。
