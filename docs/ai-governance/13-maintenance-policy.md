# ガバナンス保守方針

この文書は、AIエージェント向けルールとUI/UXガバナンスを保守するための方針です。Codex・Claude Code・Cursorを同時に支援する全体構成は [`docs/agent-harness.md`](../agent-harness.md) を正本とします。

## 正本

- 共通の常時読込契約: `AGENTS.md`
- 領域固有契約: 対象に最も近いnested `AGENTS.md`
- task固有手順: `.agents/skills/<name>/SKILL.md`
- UI/UX詳細: `docs/ai-governance/`
- 公開安全性詳細: `docs/security-publication-checklist.md`
- Claude Code adapter: `.claude/rules/`, `.claude/skills/`
- Cursor adapter: `.cursor/rules/`
- 機械検証: `scripts/verify-agent-harness.sh`, `scripts/verify-ai-governance.sh`

`CLAUDE.md` は `@AGENTS.md` だけを原則とします。tool adapterは正本を参照するだけで、新しい判断基準を持ちません。

## 変更時の3製品確認

ルール、Skill、adapter、検証scriptを変更する場合は、同じPRで次を確認します。

### Codex

- rootとnested `AGENTS.md`から必要な規則へ到達できる。
- task手順が常時読込へ混入せず、`.agents/skills/`へ分離されている。
- rootとnestedの合計がinstruction budgetを満たす。

### Claude Code

- `CLAUDE.md`が共通核を一重にimportしている。
- path固有の規則が`.claude/rules/`の`paths`で必要時だけ案内される。
- task手順が`.claude/skills/`の薄いadapterから共有Skillへ接続される。
- adapterへ長文の本文をcopyしていない。

### Cursor

- root `AGENTS.md`と`.cursor/rules/`が競合せず、MDC ruleは`alwaysApply: false`と適切な`globs`を持つ。
- task手順は`.agents/skills/`を正本として利用できる。
- `.cursor` directoryの存在を禁止しない。
- ruleへ共通核やSkill本文を複製していない。

## ルール追加の判断

1. 全作業で必要ならroot `AGENTS.md`。
2. 特定pathだけならnested `AGENTS.md`と薄いtool adapter。
3. 特定taskだけなら`.agents/skills/`と必要なadapter。
4. 機械判定できるならscript、test、lint、CI。
5. 既存正本へ統合できる場合は新規文書を増やさない。

詳細手順をrootへ追加する変更は、他の配置では成立しない理由をIssueとPRへ書きます。

## 重複禁止

同じhard gate、checklist、workflow本文を複数箇所で正本化しません。

良い構造:

```text
AGENTS.md -> task Skill -> 詳細正本
Claude / Cursor adapter -> 同じAGENTSまたはSkill
```

避ける構造:

```text
AGENTS.md、Skill、docs、tool専用ruleに同じ長文を複製
```

表現を少し変えた意味上の重複も対象です。indexは入口、Skillは実行順序、詳細docsは判定基準として責務を分けます。

## Hard gateとheuristic

- P0、secret、証跡捏造、data破壊、公開契約、権限境界はhard gateとして明確にする。
- DRY、KISS、SRP、OCP、行数、重複回数、test配分はheuristicとして扱う。
- heuristicを数値だけのFail条件へ変えない。
- P0を格下げする場合は、完了不可ではない根拠を記録する。

## Review収束

- latest meaningful changeに対するCIと利用可能なreviewを確認する。
- 指摘対応でheadが変わった時だけ再確認する。
- 変更のないheadに対するclean reviewを複数回要求しない。
- 特定製品のreview名を3製品共通の完了条件へしない。
- merge、closeは別の明示指示がある場合だけ行う。

## 研究・標準

新しい研究やguidelineを取り込む時は、標準・仕様、長く使われるHCI原則、認知accessibility指針、最新研究、単発研究の順に強制力を判断します。単発研究や製品固有の一時的挙動を、根拠なくhard gateへしません。

公式仕様が変わった場合は、3製品の現行仕様を確認し、adapterと検証scriptを同時に更新します。

## 検証

変更後は次を実行します。

```bash
bash scripts/verify-agent-harness.sh
bash scripts/verify-ai-governance.sh
```

加えて、変更したshellの`bash -n` / `shellcheck`、YAML / frontmatter、link、公開安全性を確認します。検証できない項目は理由と残るリスクを報告します。
