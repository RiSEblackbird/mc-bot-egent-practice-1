# ドキュメント構成と責務分担

この文書は、このリポジトリのdocumentをどこに書くかを定義する正本です。READMEは入口に保ち、詳細仕様、運用判断、エージェント手順は該当する文書へ分けます。

## 責務分担

| 文書 | 責務 |
|---|---|
| `README.md` | GitHub訪問者向け入口。短い概要、最短起動、主要directory、代表command、文書案内だけを書く。 |
| `AGENTS.md` | Codex・Claude Code・Cursorが常時共有する短い作業契約を書く。 |
| nested `AGENTS.md` | Python、dashboard、Node bot、Paper plugin、testなど対象path固有の契約を書く。 |
| `docs/agent-harness.md` | 3製品へのrule接続、instruction budget、正本・adapter・Skillの配置方針を書く。 |
| `docs/agent-principles.md` | 設計・実装のheuristicとhard gateの境界を書く。 |
| `.agents/skills/` | task固有の共有workflowを正本として書く。 |
| `.claude/rules/`, `.claude/skills/` | Claude Codeへ正本を接続する薄いadapterだけを書く。 |
| `.cursor/rules/` | Cursorへ正本を接続する薄いpath adapterだけを書く。 |
| `docs/ai-governance/` | UI/UXの詳細判定、証跡、Issue品質、完了条件を書く。 |
| `docs/process/task-execution.md` | 長大taskを実務で進めるためのエージェント非依存flowを書く。 |
| `docs/tech_stack_diagram.md` | 技術stackと主要component関係を書く。 |
| `docs/*_design.md` | 特定機能や拡張設計の現時点で有効な仕様と判断を書く。 |
| `docs/refactor/` | refactor計画、進捗、完了検証など、該当refactorに閉じた情報を書く。 |
| `plans/` | 進行中または再開可能なtask計画、受け入れ条件、検証結果、再開commandを書く。 |
| `env.example`, `env.dev.example`, `env.prod.example` | 環境変数名、既定値、設定例の正本を書く。 |
| `.github/` | Issue / PR template、CI workflowなどGitHub上のharnessを書く。 |

## READMEに書くこと

- project名と1〜2文の概要
- 最短quick start
- 主要componentの短い一覧
- 代表的な起動・test command
- 詳細documentへの案内
- licenseや補足がある場合の短い案内

READMEの粒度は、初見訪問者が3分以内に「何のprojectか」「どう起動するか」「どこを読めばよいか」を判断できる範囲までに留めます。

## READMEに書かないこと

- 環境変数の全key説明
- Minecraft / Paper / Mineflayer / LangGraph / OpenAI SDKの長い設定手順
- Bridge HTTP API、Node bot payload、planner stateの詳細契約
- CI / GitHub Actions / Docker Composeの長いtroubleshooting
- test commandの長い正例 / 負例
- 実装内部の責務分割の詳細
- エージェントの作業完了gate全文
- task固有workflow本文
- 一時的な作業メモ、申し送り、未確定TODO

READMEには短い要約とlinkだけを置き、詳細は該当文書を正本にします。

## 更新判断flow

1. dashboardの操作、画面文言、user flowが変わる場合はREADMEまたは関連docsを確認する。
2. Python planner、LangGraph、OpenAI Responses APIの責務やstateが変わる場合は、関連docsと `python/AGENTS.md` を確認する。
3. Node bot、Mineflayer、WebSocket、payload契約が変わる場合は、`node-bot/AGENTS.md` と関連docsを確認する。
4. Bridge plugin、HTTP API、Paper plugin設定、保護領域判定が変わる場合は、`bridge-plugin/AGENTS.md`、関連docs、環境templateを確認する。
5. 環境変数の意味や既定値が変わる場合は、実装、`env.example`群、README、関連docsを揃える。
6. test command、artifact、CI実行条件が変わる場合は、`.github/workflows/`、README、関連docsを確認する。
7. 全作業共通のエージェント契約が変わる場合は `AGENTS.md` を更新する。
8. path固有の契約が変わる場合はnested `AGENTS.md`とClaude / Cursor adapterを更新する。
9. task固有workflowが変わる場合は`.agents/skills/`とClaude Skill adapterを更新する。
10. rule配置、instruction budget、3製品互換性が変わる場合は`docs/agent-harness.md`と検証scriptを更新する。
11. UI/UXの詳細基準、証跡、Issue品質が変わる場合は`docs/ai-governance/`を更新する。

## エージェントルールの重複管理

- `AGENTS.md`、Skill、詳細docs、tool adapterで同じ長文を正本化しない。
- rootは常時必要な共通核、nested ruleはpath固有差分、Skillはtaskの実行順序、詳細docsは判定基準を持つ。
- `.claude/`と`.cursor/`は正本への接続だけを行い、新しい品質基準を持たない。
- 機械判定できる形式・上限・参照は`verify-agent-harness.sh`へ置く。
- 配置判断の詳細は`docs/agent-harness.md`に従う。

## 一般的な重複管理

- READMEとdocsに同じ長文を書かない。
- 既存文書に正本がある場合は、新規fileを増やさず既存文書を更新する。
- 複数文書で同じ情報が必要な場合は、片方を正本にし、他方は要約とlinkだけにする。
- secret、認証情報、個人情報、本番log原文、trace / request / job IDの実値は公開文書に残さない。
