---
name: github-delivery
description: "Issue、branch、commit、push、PR、CI、review、release準備を安全に一気通貫で行う時に使う。利用可能なGitHub clientを使い、latest headの検証とreviewを確認し、merge/closeは明示指示がある場合だけ行う。"
---

# GitHub配送 Skill

## 発動条件

リポジトリ変更をIssue、commit、push、PR、CI、reviewまで運ぶ作業で使います。read-only調査や回答だけでは発動しません。

## 1. 開始前

- ルート `AGENTS.md` と変更対象に最も近い `AGENTS.md` を読む。
- 現在のdefault branch、作業branch、未commit差分、直近履歴を確認する。
- 無関係な差分の所有者と範囲を確認し、巻き込まない。
- GitHub CLI、GitHub API、connectorなど、利用可能で認証済みのclientを使う。一つのclientが使えなくても、同等clientで完了gateを満たせる場合は作業を止めない。

## 2. Issue

- 既存Issueを検索し、依頼を完全に含むものがあれば使う。
- 非軽微な新機能、改修、不具合、設計、security、運用、文書、ガバナンス変更に既存Issueがなければ作成する。
- [`docs/ai-governance/14-issue-quality-gate.md`](../../../docs/ai-governance/14-issue-quality-gate.md) に従い、理由、根拠、現在と目標、範囲、非対象、受け入れ条件、検証、riskを書く。
- typoや同一PR内の局所修正など、Issueを省略する場合はPR本文へ短い理由を書く。

## 3. Branchと実装

- default branchのlatestから作業branchを作る。標準名は `agent/<purpose>` とし、既存branchやユーザー指定がある場合はそれを尊重する。
- 複数工程でも、真のblockerがない限り調査、実装、検証、配送まで継続する。
- 意味のある変更単位で、日本語のcommit messageを付ける。
- commit前に差分、追加file、secret混入、無関係な変更を確認する。

## 4. PR

- ユーザーが完成した変更のPRを求めている場合は、原則としてdraftではないPRを作成または更新する。
- 主Issueは一つに絞る。完全解決は`Closes #123`、部分対応は`Refs #123`を使う。
- PR本文には、変更内容、保持した挙動、検証、未実行項目、対象面の証跡、公開安全性、残るriskを書く。
- UI変更ではUI/UX Skill、公開物では公開安全性Skillの成果を反映する。

## 5. CIとreview

- latest headに紐づく必須CIを確認する。失敗時はlogから原因を特定し、修正、commit、push、再確認する。
- CI成功後、利用可能な自動review、手動review、review thread、review commentをlatest headで確認する。
- actionableな指摘はまとめて修正し、変更後のheadでCIと該当reviewを再確認する。
- latest meaningful changeに対するclean reviewが一回得られ、未解決threadがなく、CIと必須条件を満たせばreviewを終了する。
- 変更のないheadでclean結果を増やすためだけの再reviewを行わない。
- reviewが提供されない環境では、実施した自己review、未確認範囲、残るriskを報告する。

## 6. 権限境界と終了

- merge、close、release、production deploy、破壊的変更は、別の明示指示がある場合だけ行う。
- blocker報告には、失敗しているcheckまたは操作、証跡、試した対応、未完了範囲、次の最短アクションを含める。
- 最終報告には、Issue、branch、commit、PR、local verification、CI、review、remaining risksのうち今回に関係するものを示す。
