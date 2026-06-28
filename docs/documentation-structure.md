# ドキュメント構成と責務分担

この文書は、このリポジトリのドキュメントをどこに書くかを定義する正本です。README は入口に保ち、詳細仕様、運用判断、エージェント手順は該当する文書へ分けます。

## 責務分担

| 文書 | 責務 |
|---|---|
| `README.md` | GitHub 訪問者向け入口。短い概要、最短起動、主要ディレクトリ、代表コマンド、文書案内だけを書く。 |
| `AGENTS.md` | AI エージェントの実行手順、完了ゲート、信頼境界、必須確認を書く。 |
| サブディレクトリ `AGENTS.md` | 技術領域固有の実装規約、検証コマンド、注意点を書く。 |
| `docs/agent-principles.md` | DRY、KISS、SoC、テスト、可観測性、ドキュメント保守など詳細な品質原則を書く。 |
| `docs/ai-governance/` | UI/UX レビュー、P0/P1/P2、証跡、テンプレート、チェックリストの詳細正本を書く。 |
| `docs/process/task-execution.md` | 長大タスクを実務で進めるためのエージェント非依存フローを書く。 |
| `docs/tech_stack_diagram.md` | 技術スタックと主要コンポーネント関係を書く。 |
| `docs/*_design.md` | 特定機能や拡張設計の現時点で有効な仕様と判断を書く。 |
| `docs/refactor/` | refactor 計画、進捗、完了検証など、該当 refactor に閉じた情報を書く。 |
| `plans/` | 進行中または再開可能なタスク計画、受け入れ条件、検証結果、再開コマンドを書く。 |
| `env.example`, `env.dev.example`, `env.prod.example` | 環境変数名、既定値、設定例の正本を書く。 |
| `.github/` | Issue / PR template、CI workflow など GitHub 上のハーネスを書く。 |

## README に書くこと

- プロジェクト名と 1〜2 文の概要
- 最短クイックスタート
- 主要コンポーネントの短い一覧
- 代表的な起動・テストコマンド
- 詳細ドキュメントへの案内
- ライセンスや補足がある場合の短い案内

README の粒度は、初見の訪問者が 3 分以内に「何のプロジェクトか」「どう起動するか」「どこを読めばよいか」を判断できる範囲までに留めます。

## README に書かないこと

- 環境変数の全キー説明
- Minecraft / Paper / Mineflayer / LangGraph / OpenAI SDK の長い設定手順
- Bridge HTTP API、Node bot payload、planner state の詳細契約
- CI / GitHub Actions / Docker Compose の長いトラブルシューティング
- テストコマンドの長い正例/負例
- 実装内部の責務分割の詳細
- エージェントの作業完了ゲート全文
- 一時的な作業メモ、申し送り、未確定の TODO

README には短い要約とリンクだけを置き、詳細は該当文書を正本にします。

## 更新判断フロー

1. UI の操作、画面文言、ユーザーフローが変わる場合は `README.md` または `docs/` 配下のユーザー向け手順・仕様文書を確認します。
2. Python planner、LangGraph、OpenAI Responses API の責務や state が変わる場合は、関連する `docs/` と `python/AGENTS.md` を確認します。
3. Node bot、Mineflayer、WebSocket、payload 契約が変わる場合は、`node-bot/AGENTS.md`、`node-bot/commands.md`、関連 docs を確認します。
4. Bridge plugin、HTTP API、Paper plugin 設定、保護領域判定が変わる場合は、`bridge-plugin/AGENTS.md`、`docs/`、`env.example` を確認します。
5. 環境変数の意味や既定値が変わる場合は、実装コード、`env.example`、`env.dev.example`、`env.prod.example`、README、関連 docs を揃えます。
6. テストコマンド、成果物、CI 実行条件が変わる場合は、`.github/workflows/`、`README.md`、関連 docs を確認します。
7. AI エージェントの作業手順や完了条件が変わる場合は `AGENTS.md` を更新し、長文の詳細は専用 docs へ置きます。

## 重複管理を避ける基準

- README と docs に同じ長文を書かない。
- 既存文書に正本がある場合は、新規ファイルを増やさず既存文書を更新する。
- 複数文書で同じ情報が必要な場合は、片方を正本にし、他方は要約とリンクだけにする。
- secret、認証情報、個人情報、実運用ログ原文、trace / request / job ID の実値は公開文書に残さない。
