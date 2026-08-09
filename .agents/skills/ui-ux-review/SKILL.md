---
name: ui-ux-review
description: "アプリ本体またはリポジトリが制御する独自UIの変更・レビューで、ユーザー価値、状態、アクセシビリティ、視覚階層、コピー、熟練者効率、信頼感を証跡付きで確認する。"
---

# UI/UXレビュー Skill

## 1. 発動条件と対象面

次の変更で使います。

- 画面、page、component、layout、navigation、form
- 表示文言、label、説明、error、通知
- loading、empty、no-results、partial、error、validation-error、disabled、permission-denied
- accessibility、responsive、操作方法
- backend変更でも、利用者に見える結果や回復方法が変わるもの

最初に [`docs/ai-governance/02-uiux-review-framework.md`](../../../docs/ai-governance/02-uiux-review-framework.md) で対象面を分類します。

- **アプリ本体UI**: リポジトリがlayout、操作、状態、focus、accessibilityを実装する画面。本文書の全手順を適用する。
- **GitHub共同作業面**: Issue / PR template、repository Markdown、workflow説明など。リポジトリが制御する文言、構造、表示、link、公開安全性だけを範囲に比例して確認する。
- **混在**: 両方を別々に確認し、一方の証跡で他方を代用しない。

## 2. 読む正本

- 全作業: `AGENTS.md` と変更対象に最も近い `AGENTS.md`
- UI品質・P0/P1/P2: `docs/ai-governance/02-uiux-review-framework.md`
- 証跡・完了条件: `docs/ai-governance/03-evidence-and-completion-gates.md`
- 変更内容に直接関係する詳細文書だけ:
  - 認知・初見理解: `04-cognitive-psychology-principles.md`
  - accessibility: `05-accessibility-and-inclusive-design.md`
  - 視覚階層: `06-visual-hierarchy-and-information-architecture.md`
  - copy: `07-ui-copy-and-microcopy.md`
  - 状態・回復: `08-state-design-and-error-recovery.md`
  - ユーザー価値: `10-utility-user-goal-and-product-fit.md`
  - 熟練者効率: `11-efficiency-and-expert-use.md`
  - 満足感・信頼感: `12-satisfaction-trust-and-emotional-ux.md`

indexや全詳細文書を機械的に読み直さず、変更範囲から必要な正本を選びます。

## 3. 実行

1. 対象ユーザー、目的、主要task、変更画面、影響状態を特定する。
2. 初見ユーザーが画面目的、現在地、最初の行動、結果、回復方法を判断できるか確認する。
3. 該当するstate matrixを作る。対象外の状態は理由を記す。
4. keyboard、focus、accessible name、semantic structure、contrast、target size、文字拡大を確認する。
5. 視覚的な優先度、grouping、主操作、長文・大量data・狭幅を確認する。
6. copyが原因、影響、次の行動を示し、ユーザーを責めないことを確認する。
7. 反復操作の手数、入力保持、再選択、shortcut、一括操作、毎回の説明を確認する。
8. 待機、成功、失敗、危険操作、保存、送信、削除、権限の信頼感を確認する。
9. 実装を落とす立場で反証reviewし、P0/P1/P2と証跡不足を探す。
10. 実行した検証、未実行検証、残るriskを記録する。

## 4. 証跡

アプリ本体UIでは [`docs/ai-governance/03-evidence-and-completion-gates.md`](../../../docs/ai-governance/03-evidence-and-completion-gates.md) に従い、該当画面・状態の前後screenshot、test、手動確認、state matrix、各review結果を残します。

前後screenshotを取得できない場合は、取得不能理由、代替証跡、残るrisk、次に必要な確認を示し、取得必須の変更を完了扱いにしません。

GitHub共同作業面だけの場合は、差分、Markdown / form構造、link、公開安全性、未実行項目を証跡とします。GitHubが所有する未変更のfocusやplatform stateまで検査対象に広げません。

## 5. 完了

- P0が残る場合は完了不可。
- P1は原則として同じ変更内で修正し、分離する場合は理由と追跡先を示す。
- P2は完了を止めないが、対応しない理由または後続先を記録する。
- screenshot、test、user feedback、accessibility結果を捏造しない。
