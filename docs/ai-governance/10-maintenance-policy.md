# Governance Maintenance Policy

このファイルは、AI エージェント向けルールの変更方法を定める。

## 1. Source of truth

- `AGENTS.md` は rule origin かつ startup constitution である。
- `docs/ai-governance/` は詳細な source of truth である。
- `.agents/skills/*/SKILL.md` は task-specific な executable workflow を含む。
- `CLAUDE.md` は `AGENTS.md` だけを import する。
- Cursor rules はこの governance の一部ではない。

## 2. 重複禁止

UI/UX rulebook の全文を次へ重複させない。

- `AGENTS.md`
- `CLAUDE.md`
- IDE rules
- PR templates
- README files
- 複数の skills

重複した summary は drift を生む。詳細文書へ link する。

## 2.1. 言語方針

このリポジトリの maintainer が読めるよう、ガバナンス本文は日本語を正式版とする。英語だけの本文を source of truth として追加しない。外部標準名、tool が読む keyword、file path、一般に翻訳しない技術用語を残す場合は、必要に応じて `glossary.md` に意味を追加する。

## 3. ルール追加基準

新しい rule は、少なくとも 1 つの根拠を持つ。

- accessibility standard
- cognitive psychology または HCI research
- design-system standard
- 観測済みのリポジトリ defect
- 繰り返し発生した review failure
- security requirement
- user instruction

新しい rule は次を満たす。

- specific
- testable または reviewable
- 該当する場合は severity-classified
- evidence に対応付けられている

## 4. 更新手順

governance を変更するときは次を行う。

1. このファイルを読む。
2. `references/canonical-sources.md` を読む。
3. 変更が AGENTS、skills、詳細 docs、templates、または全体のどれに影響するかを特定する。
4. rule body の重複を避ける。
5. 新 rule が evidence を要求する場合は templates/checklists を更新する。
6. 英語用語を増やした場合は `glossary.md` を更新する。
7. `scripts/verify-ai-governance.sh` を実行する。
8. conflicts と migration notes を報告する。

## 5. Skill maintenance

skills は焦点を絞る。

- `SKILL.md` は簡潔に保つ。
- 重い detail は `docs/ai-governance/` に置く。
- description の trigger words は強く、前半に置く。
- maintainer が明示的に求めない限り、tool-specific metadata を追加しない。
- deterministic automation が必要な場合を除き、script を追加しない。

## 6. AGENTS.md maintenance

`AGENTS.md` は compact に保つ。

含めるもの:

- routing
- hard gates
- trust boundaries
- evidence rules
- detailed docs への references

含めないもの:

- UI/UX framework 全文
- 長い research summary
- vendor-specific settings
- duplicated checklists

## 7. Review cadence

次の場合に governance を見直す。

- accessibility standards が変わった。
- design system が変わった。
- agent toolchain が変わった。
- UI/UX review defect が繰り返された。
- repository が新しい frontend framework を採用した。
- major user-facing flow が追加された。

## 8. Deprecation

rule を削除するときは次を行う。

- 理由を説明する。
- 何が置き換えるかを特定する。
- templates/checklists/skills の参照を確認する。
- maintainer が明示的に承認しない限り、P0 blocker を弱めない。
