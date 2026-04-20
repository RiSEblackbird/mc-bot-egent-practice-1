# Final Validation Snapshot (2026-04-20)

基盤刷新（Phase 0〜8）後の受け入れ観点を、現行実行環境で再検証した記録。

## 実行結果サマリ

| 区分 | コマンド | 結果 | 補足 |
| --- | --- | --- | --- |
| Python 環境構築 | `bash scripts/setup-python-env.sh` | ✅ 成功 | `.venv` に依存導入 + editable install 完了 |
| Python テスト | `source .venv/bin/activate && python -m pytest tests` | ✅ 成功 | `105 passed` |
| Node テスト | `bash scripts/run-node-bot.sh test` | ⚠️ 環境制約 | Node `v20.19.6`。スクリプト要件は 22+ |
| Node build | `bash scripts/run-node-bot.sh build` | ⚠️ 環境制約 | 同上 |
| Bridge build | `bash scripts/build-bridge-plugin.sh` | ✅ 成功 | Gradle `shadowJar` 成功 |
| Compose config | `docker compose config` | ⚠️ 環境制約 | `docker: command not found` |

## 受け入れ観点に対する判定

1. CI green: この環境からは CI 実行不可。既存 workflow は `.github/workflows/ci.yml` に定義済みで、ローカル検証では代替として各入口を実行。
2. Python / Node / Bridge build/test:
   - Python と Bridge は成功。
   - Node は環境 Node バージョンにより未実施（要 Node 22+）。
3. planner schema-first, interrupt/resume, envelope, Docker/README/doc:
   - 既存の Phase ドキュメントと成果物により実装済みとして追跡可能（`docs/refactor/phase3.md`〜`phase8.md` 参照）。

## Phase 8 完了報告
- 変更概要: 基盤刷新の受け入れ再検証として、実行コマンドの最新結果を固定化。
- 主な変更ファイル:
  - `docs/refactor/final-validation.md`
  - `plans/refactor-foundation-final-validation.md`
- 互換性影響: なし（ドキュメント追加のみ）。
- 実行したコマンド:
  - `bash scripts/setup-python-env.sh`
  - `source .venv/bin/activate && python -m pytest tests`
  - `bash scripts/run-node-bot.sh test`
  - `bash scripts/run-node-bot.sh build`
  - `bash scripts/build-bridge-plugin.sh`
  - `docker compose config`
- テスト結果:
  - Python / Bridge は成功。
  - Node / Docker は環境制約で未達。
- 残課題:
  - Node 22+ と Docker が有効な環境での追試結果を追加し、受け入れテスト 1,2,6 を最終確定する。
