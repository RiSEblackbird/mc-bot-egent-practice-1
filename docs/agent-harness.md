# エージェントハーネス設計・保守ガイド

この文書は、このリポジトリを Codex・Claude Code・Cursor のいずれで扱っても、同じ品質基準を過不足なく適用するための構成を定義する正本です。

## 目的

エージェント向け指示は、増やすほど安全になるとは限りません。常時読み込まれる長文、同じ規則の複数正本、タスクと無関係な手順は、重要な指示への注意を薄め、過剰実装や見落としを増やします。

品質基準を次の4層へ分けます。

| 層 | 役割 | 主な配置 |
|---|---|---|
| 常時読込の共通核 | 全作業で必要な安全境界、証跡、基本進行 | `AGENTS.md`, `CLAUDE.md` |
| path固有ルール | Python、dashboard、Node bot、Paper plugin、testの契約 | nested `AGENTS.md`, `.claude/rules/`, `.cursor/rules/` |
| task固有workflow | UIレビュー、GitHub配送、実環境調査、公開安全性 | `.agents/skills/`, `.claude/skills/` |
| 機械検証 | 形式、参照、instruction budget、禁止された重複 | `scripts/verify-agent-harness.sh`, CI |

## 正本とadapter

- 共有ルールの原点はルート `AGENTS.md`。
- 領域固有ルールの正本は、対象directoryに最も近い `AGENTS.md`。
- task固有手順の正本は `.agents/skills/<name>/SKILL.md`。
- UI/UXの詳細判定基準は `docs/ai-governance/`。
- `.claude/rules/`、`.claude/skills/`、`.cursor/rules/` は、正本を各製品の読込機構へ接続する薄いadapter。
- adapterへ正本の本文をcopyしない。対象pathと読むべき正本だけを示す。

## 3製品の接続方法

### Codex

- ルートから現在の作業directoryまでの `AGENTS.md` を階層的に利用する。
- ルートから複数領域を編集する場合も、変更対象に最も近い `AGENTS.md` を明示的に確認する。
- `tests/`、root文書、`.github/workflows/`、compose、script、環境templateなど、祖先pathだけでは領域正本へ到達しない対象は、ルート `AGENTS.md` のpath bridgeから追加正本を読む。
- task固有手順は `.agents/skills/` から読む。

### Claude Code

- `CLAUDE.md` は `@AGENTS.md` だけをimportし、共通核を一重に共有する。
- `.claude/rules/*.md` の `paths` で、領域固有の正本を必要な時だけ案内する。
- `.claude/skills/<name>/SKILL.md` は、対応する `.agents/skills/` を読む薄いadapterにする。
- `@` importで長文を分割しても常時読込量は減らないため、task手順はSkillへ置く。

### Cursor

- 共通核はルート `AGENTS.md` から読む。
- `.cursor/rules/*.mdc` の `globs` と `alwaysApply: false` でpath固有の正本を案内する。
- task固有手順はAgent Skills互換の `.agents/skills/` を正本として使う。
- `.cursor/rules/` に長い共通ルールを再掲しない。

## 対象リポジトリのpath routing

| 対象 | 近接正本 | Claude / Cursor adapter |
|---|---|---|
| `python/**` | `python/AGENTS.md` | `python` |
| `python/dashboard/**` | `python/AGENTS.md`, `python/dashboard/AGENTS.md` | `dashboard` |
| `node-bot/**` | `node-bot/AGENTS.md` | `node-bot` |
| `bridge-plugin/**` | `bridge-plugin/AGENTS.md` | `bridge-plugin` |
| `tests/**` | `tests/AGENTS.md`。Python対応時は `python/AGENTS.md` も読む | `tests` |
| 複数componentの契約、compose、workflow、script、環境template | 影響componentの近接正本 | ルートbridgeから選択 |

## ルール配置の判断

新しい規則を追加する前に、次の順で判断します。

1. 全タスクで毎回必要か。
   - 必要: ルート `AGENTS.md`。
   - 不要: 次へ。
2. 特定pathだけに必要か。
   - 必要: 最寄りのnested `AGENTS.md`。Claude / Cursorには薄いpath adapterを追加する。
   - 不要: 次へ。
3. 特定作業種類だけに必要か。
   - 必要: `.agents/skills/`。Claudeには薄いSkill adapterを追加する。
   - 不要: 次へ。
4. 自動判定できるか。
   - できる: script、test、lint、CIへ置く。
   - できない: 人とエージェントが判断できる短いheuristicとして文書化する。
5. 既存の正本へ統合できるか。
   - できる場合は新規文書を増やさず、既存正本を更新する。

## Hard gateとheuristic

### Hard gate

違反時に作業を停止または未完了扱いにする、客観的に判定可能な条件です。

- secretや個人情報を公開しない。
- 未実施検証を成功扱いしない。
- Python / Node bot / Bridge pluginの公開契約を片側だけで変更しない。
- P0を残してUI/UX完了としない。
- latest headの必須CIが失敗中ならマージ可能と報告しない。
- merge、close、実環境変更、破壊的操作は明示された権限内だけで行う。

### Heuristic

複数の目的が競合する場面で、設計判断を助ける目安です。

- DRY、KISS、SRP、OCP、YAGNI。
- function・fileの大きさ。
- 抽象化する重複回数。
- Unit / Integration / E2Eの配分。
- comment量。

heuristicを「常に」「必ず」と書く場合は、例外が成立しない理由を示します。数値だけをPass / Failへ変換しません。

## Instruction budget

次をhard upper boundとします。短いほど常に良いという意味ではなく、超過時に構造を見直すための上限です。

| 対象 | 行数 | UTF-8 bytes |
|---|---:|---:|
| ルート `AGENTS.md` | 180以下 | 16 KiB以下 |
| nested `AGENTS.md` | 100以下 | 8 KiB以下 |
| `.claude/rules/` / `.cursor/rules/` adapter | 30以下 | 4 KiB以下 |
| `.claude/skills/` adapter | 30以下 | 4 KiB以下 |
| canonical Skill | 180以下 | 16 KiB以下 |
| ルート + 1つのnested `AGENTS.md` | - | 24 KiB以下 |

超過を正当化する場合は、常時読込またはpath scopeでなければならない理由、分割できない理由、3製品への影響をIssueとPRへ記録し、検証scriptの上限を黙って緩和しません。

## 禁止する構造

- tool別fileへ同じ長文を複製する。
- `AGENTS.md`、Skill、詳細docsで同じchecklistをそれぞれ正本化する。
- 一つのGitHub clientだけを、同等clientが使える状況でも必須化する。
- Codex固有のreview名やbranch prefixを、Claude CodeとCursorにも共通の完了条件として課す。
- 変更のない同一headに対して、clean結果を得るためだけにreviewを反復する。
- read-onlyの回答へIssue / branch / PR欄の定型出力を要求する。
- path scopeで解決できる規則を常時読込へ戻す。
- 形式で検査できる条件を自然言語だけで維持する。

## GitHub reviewの収束

- latest meaningful changeに対する必須CIと、利用可能な自動・手動reviewを確認する。
- actionableな指摘を修正した場合は、latest headでCIと該当reviewを再確認する。
- 変更のないheadで追加のclean reviewを複数回集めない。
- reviewが提供されない環境では、未確認範囲と代替自己reviewを報告する。
- mergeまたはcloseは別の明示指示がある場合だけ行う。

## ルール変更時の確認

1. 変更を `common / path / task / machine` のどこへ置くか決めた。
2. Codexのroot / nested `AGENTS.md`で必要なルールへ到達できる。
3. Claude Codeの`CLAUDE.md`、path rule、Skill adapterで必要時だけ到達できる。
4. Cursorの`AGENTS.md`、MDC rule、Agent Skillで必要時だけ到達できる。
5. adapterは正本を参照するだけで、長文を複製していない。
6. hard gateとheuristicを区別した。
7. instruction budgetを満たした。
8. 旧正本、循環参照、壊れたlinkを残していない。
9. `bash scripts/verify-agent-harness.sh`を実行した。
10. UI/UXガバナンスを変えた場合は`bash scripts/verify-ai-governance.sh`も実行した。

## 既知の限界

各製品のversion、Remote SSH、sandbox、権限、Skill discoveryの実装差までは、リポジトリ内の静的検証だけで保証できません。adapterと正本の構造をCIで固定し、実環境で発見できない場合は製品名、version、実行形態、再現pathをIssueへ残します。
