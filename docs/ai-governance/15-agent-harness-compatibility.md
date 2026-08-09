# エージェントハーネス互換性方針

この文書は、Codex、Claude Code、Cursorの3つで、同じ品質契約を過不足なく適用するための詳細正本です。個別製品へ同じ長文を複製せず、共通正本、scope、adapter、Skill、機械検査を組み合わせます。

## 1. 目的

- 重要なhard gateを3エージェントで一貫して適用する。
- 作業に無関係な指示を常時読み込ませない。
- tool固有機能を活用しつつ、特定toolだけで成立するルールを正本にしない。
- ルール追加によるinstruction budgetの無制限な増加を防ぐ。
- 発動漏れ、重複、循環参照、古いadapterをCIで検出する。

## 2. 用語

- **共通正本**: tool非依存の判断基準を置く `AGENTS.md`、`docs/`、`.agents/skills/`。
- **近接ルール**: 特定directory以下にだけ適用する `AGENTS.md`。
- **adapter**: tool固有のscope機構から共通正本へ接続する短いfile。
- **Skill**: 特定作業でだけ読む実行手順。常時ルールにはしない。
- **hard gate**: 違反した状態で完了または公開してはいけない条件。
- **heuristic**: 文脈に応じて採否を判断する設計・実装上の目安。
- **instruction budget**: エージェントが常時または対象scopeで読む指示量。

## 3. 3エージェントの適用構造

| 対象 | 常時入口 | scopeの絞り方 | 作業手順 | fallback |
|---|---|---|---|---|
| Codex | ルート `AGENTS.md` | 作業pathに近い `AGENTS.md` | `.agents/skills/` | 近接ルールがなければルート契約と関連docs |
| Claude Code | `CLAUDE.md` の `@AGENTS.md` | `.claude/rules/` のpath条件 | 共通Skillを明示参照 | adapterが未発動でもルート契約を維持 |
| Cursor | ルート契約と `.cursor/rules/` | `.cursor/rules/*.mdc` のglobs | 共通Skillを明示参照 | adapterが未発動でもルート契約を維持 |

共通の意味は常に共通正本へ置きます。Claude CodeとCursorのadapterは、適用path、追加で読む近接ルール、使うSkillだけを示します。

## 4. 配置判断

新しいルールは、次の順で配置先を判断します。

1. format、lint、schema、禁止pattern、file存在、line budgetなど機械で判定できる条件はscriptまたはCIへ置く。
2. secret、検証捏造、未確認差分、破壊的操作など常に必要なhard gateだけをルートへ置く。
3. Python、dashboard、Node bot、Bridge plugin、test固有の判断は近接 `AGENTS.md` へ置く。
4. UI review、GitHub配送、本番調査、公開審査など特定作業だけの手順はSkillへ置く。
5. 詳細な判断根拠は `docs/` の正本へ置く。
6. tool固有の発動条件だけを `.claude/rules/` または `.cursor/rules/` のadapterへ置く。

ルートへ追加することを既定にしません。

## 5. instruction budget

### 常時読み込み

- ルート `AGENTS.md` は180行・16 KiB以内とする。
- `CLAUDE.md` は `@AGENTS.md` のみを維持する。
- ルートへcommandの全分岐、template全文、詳細checklist、特定review botの操作手順を置かない。

### scoped ruleとadapter

- adapterは30行・4 KiB以内とする。
- 一つのadapterが複数domainを無関係に束ねない。
- `globs`または`paths`は正本の適用範囲と一致させる。
- adapterから別adapterを参照しない。

### Skill

- Skillは発動条件、読む正本、実行順序、成果物、hard gateに集中する。
- 詳細正本と同じ質問・checklistを再掲しない。
- Skillからindexを経由して同じSkillへ戻る循環参照を作らない。
- 一つのSkillへ異なる作業種類を集約しない。

## 6. tool中立性

共通正本では結果と契約を定義し、特定toolの操作方法はadapterまたはSkillの補足に留めます。

良い例:

- 利用可能な認証済みGitHub clientでIssueとPRを作成する。
- latest headに対する利用可能なreview結果を確認する。
- path-scoped adapterから近接ルールを読む。

避ける例:

- すべてのエージェントへ特定CLIの認証commandを必須化する。
- 共通branch名へ特定製品名を固定する。
- 特定review botが存在しない環境を未完了にする。
- Cursorのrules directoryを禁止する。

## 7. hard gateとheuristic

hard gateにできる条件は、違反時の具体的な損害または虚偽が説明でき、Pass / Failを証跡で判定でき、3エージェントで同じ意味に解釈できるものです。

DRY、抽象化、file分割、layering、test配置、行数、重複回数、component粒度、将来拡張など、複数の妥当な解がある設計判断はheuristicとして扱います。heuristicを採用しないこと自体を失敗にせず、品質・保守性へ実質的なriskがある場合だけ指摘します。

## 8. ハーネス変更時の互換性review

| 観点 | Codex | Claude Code | Cursor |
|---|---|---|---|
| 発見 | root / nested `AGENTS.md`から到達できるか | `CLAUDE.md` / path ruleから到達できるか | `.cursor/rules`のglobsから到達できるか |
| scope | 無関係directoryへ適用されないか | pathsが広すぎないか | globs / alwaysApplyが広すぎないか |
| 正本 | tool固有copyを作っていないか | adapterに本文を複製していないか | adapterに本文を複製していないか |
| fallback | 近接ルールなしでも安全か | adapter未発動でも共通hard gateが残るか | adapter未発動でも共通hard gateが残るか |
| 実行可能性 | 利用可能なtoolで成果を完遂できるか | 固有機能なしでも代替経路があるか | 固有機能なしでも代替経路があるか |
| budget | rootと近接ルールが過密でないか | import後の常時量が過密でないか | alwaysApply ruleが増えすぎていないか |

PRには、3者への影響、追加した常時指示量、scoped化した内容、未確認の製品固有挙動を記録します。

## 9. 検証

検証scriptは最低限、次を検査します。

- ルートとnested `AGENTS.md`の行数とbyte数。
- `CLAUDE.md`のimport契約。
- Claude CodeとCursorの必須adapterの存在と正本参照。
- canonical Skillと近接ルールの存在。
- 廃止したreview収束条件とCursor禁止規則の残存。
- UI Skillの上限と詳細正本への参照。
- adapter frontmatterとpath scope。

自動検査だけで、各toolの実際のrule発見挙動を完全に保証したとは扱いません。製品仕様が変わった時、adapterが発動しなかった時、同じルールが異なる意味で解釈された時は、この文書、adapter、検証scriptを同じ変更内で更新します。

## 10. 保守時の停止条件

次が残るハーネス変更は完了扱いにしません。

- 3エージェントのいずれかが共通hard gateへ到達できない。
- tool固有adapterだけに重要な判断基準が存在する。
- 同じhard gateが複数の正本で異なる文言・条件を持つ。
- root budgetを超えたまま、scoped化または機械化の検討がない。
- adapterのscopeが正本の対象と一致しない。
- 廃止した規則が検証scriptやtemplateから引き続き要求される。
